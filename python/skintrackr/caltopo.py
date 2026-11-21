"""CalTopo exports: an area's tracks as a GeoJSON document CalTopo imports (see docs/caltopo.md).

Tracks are full detail, built from the archived streams: `[lon, lat, ele, ms]` points (fewer values when the
activity has no time or altitude), one `LineString` per line, styled like the map, optionally in one folder.
Ids are stable (`folder_id`, `line_id`): CalTopo recognizes objects by id, so importing a track that's already on
the map doesn't duplicate it. This module does no I/O.
"""

import json
import uuid
from datetime import UTC
from typing import Any

import numpy as np

from skintrackr.clients.intervals import activity_start, merge_streams
from skintrackr.models import Activity, ActivitySummary, SportStyle, Stream
from skintrackr.tracks import PRECISION, BBox, clip_line, meets, split_at_gaps

# CalTopo's own default line color
DEFAULT_COLOR = "#FF0000"
FEET_PER_METER = 3.28084
METERS_PER_MILE = 1609.344


class ExportTooLarge(Exception):
    """An export would hold more points than its limit."""

    def __init__(self, points: int, limit: int) -> None:
        super().__init__(f"{points:,} points; the limit is {limit:,}")
        self.points = points
        self.limit = limit


def _id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"skintrackr:{key}"))


def folder_id(athlete_id: str, name: str) -> str:
    """A folder's id: the same for every export of the athlete's into a folder of that name."""
    return _id(f"{athlete_id}:folder:{name}")


def line_id(athlete_id: str, activity_id: str, folder: str | None, piece: tuple[BBox, int, int] | None = None) -> str:
    """A line's id: the same whenever the same track (or piece of it) is exported into the same folder.

    Args:
        athlete_id: The athlete.
        activity_id: The track's activity.
        folder: The folder's name; None for none.
        piece: For a clipped piece, the area and the piece's number (from 1) and count.
    """
    key = f"{athlete_id}:track:{activity_id}:{folder or ''}"
    if piece is not None:
        bbox, number, count = piece
        corners = ",".join(f"{value:.{PRECISION}f}" for value in (bbox.west, bbox.south, bbox.east, bbox.north))
        key += f":{corners}:{number}/{count}"
    return _id(key)


def export_points(activity: Activity, streams: list[Stream]) -> np.ndarray:
    """An activity's full-detail points: rows of `(lon, lat, ele, ms)`.

    `ms` is Unix milliseconds. Without a `time` stream the rows are `(lon, lat, ele)`, and without an `altitude`
    stream `(lon, lat)`: CalTopo fills a missing elevation profile from its own terrain data, which zeros would
    fake.

    Raises:
        IntervalsError: The streams are malformed, or the start date is invalid.
    """
    types = {stream.type for stream in streams}
    merged = merge_streams(streams, skip_unknown=True) if "latlng" in types else []
    columns = [[p.longitude for p in merged], [p.latitude for p in merged]]
    if "altitude" in types:
        columns.append([p.altitude for p in merged])
        if "time" in types:
            start = activity_start(activity)
            start_ms = (start if start.tzinfo else start.replace(tzinfo=UTC)).timestamp() * 1000
            columns.append([start_ms + p.time * 1000 for p in merged])
    return np.array(columns, dtype=float).T.reshape(len(merged), len(columns))


def export_lines(points: np.ndarray, bbox: BBox, mode: str) -> list[np.ndarray]:
    """The lines an activity gives in an export: none if its track misses the area.

    In `whole` mode, the whole track as one line (across GPS dropouts, as CalTopo records tracks); in `clip`
    mode, each piece inside the area, split at dropouts too.
    """
    segments = split_at_gaps(points)
    if mode == "clip":
        return [piece for segment in segments for piece in clip_line(segment, bbox)]
    return [points] if any(meets(segment, bbox) for segment in segments) else []


def _coordinates(line: np.ndarray) -> list[list[float | int]]:
    """A line's points as CalTopo writes them: lon and lat to `PRECISION` decimals, whole metres and ms."""
    rows = line[:, :2].round(PRECISION).tolist()
    if line.shape[1] > 2:
        extra = np.rint(line[:, 2:]).astype(np.int64).tolist()
        rows = [row + more for row, more in zip(rows, extra)]
    return rows


def track_title(summary: ActivitySummary, piece: tuple[int, int] | None = None) -> str:
    """`"2026-01-04 Snoqualmie Mountain"`: the local start date first, so CalTopo's sidebar lists tracks in order."""
    title = f"{summary.start_date_local[:10]} {summary.name}".strip()
    return f"{title} ({piece[0]}/{piece[1]})" if piece else title


def track_description(summary: ActivitySummary, label: str, units: str) -> str:
    """The sport, distance, gain and moving time, and links to intervals.icu and Strava, one per line."""
    hours, minutes = divmod(summary.moving_time // 60, 60)
    if units == "ft":
        distance = f"{summary.distance / METERS_PER_MILE:.1f} mi"
        gain = f"{summary.total_elevation_gain * FEET_PER_METER:,.0f} ft gain"
    else:
        distance = f"{summary.distance / 1000:.1f} km"
        gain = f"{summary.total_elevation_gain:,.0f} m gain"
    items = [label, f"{distance} · {gain} · {hours}:{minutes:02d} moving", f"https://intervals.icu/activities/{summary.id}"]
    if summary.strava_id:
        items.append(f"https://www.strava.com/activities/{summary.strava_id}")
    return "\n".join(item for item in items if item)


def _encode(feature: dict[str, Any]) -> bytes:
    return json.dumps(feature, separators=(",", ":")).encode()


class CaltopoDocument:
    """A CalTopo GeoJSON document, built one track at a time.

    Features are encoded as they're added, and their points counted against `points_limit`.
    """

    def __init__(self, athlete_id: str, folder: str | None, points_limit: int) -> None:
        self.athlete_id = athlete_id
        self.folder = folder
        self.points_limit = points_limit
        self.points = 0
        self.tracks = 0
        self.features: list[bytes] = []
        self.folder_id = folder_id(athlete_id, folder) if folder else None
        if folder:
            properties = {"class": "Folder", "title": folder, "visible": True, "labelVisible": True}
            self.features.append(_encode({"type": "Feature", "id": self.folder_id, "geometry": None, "properties": properties}))

    def add_track(
        self,
        summary: ActivitySummary,
        lines: list[np.ndarray],
        style: SportStyle | None,
        *,
        units: str,
        stroke_width: int,
        clipped_to: BBox | None = None,
    ) -> None:
        """Add an activity's lines (from `export_lines`); pieces of a clipped track are numbered.

        Raises:
            ExportTooLarge: The document would hold more than `points_limit` points.
        """
        self.points += sum(len(line) for line in lines)
        if self.points > self.points_limit:
            raise ExportTooLarge(self.points, self.points_limit)
        description = track_description(summary, style.label if style else "", units)
        for number, line in enumerate(lines, 1):
            piece = (number, len(lines)) if clipped_to and len(lines) > 1 else None
            properties: dict[str, Any] = {
                "class": "Shape",
                "title": track_title(summary, piece),
                "description": description,
                "stroke": (style.color if style and style.color else DEFAULT_COLOR),
                "stroke-width": stroke_width,
                "stroke-opacity": 1,
                "pattern": "solid",
            }
            if self.folder_id:
                properties["folderId"] = self.folder_id
            ids = (clipped_to, number, len(lines)) if clipped_to else None
            feature = {
                "type": "Feature",
                "id": line_id(self.athlete_id, summary.id, self.folder, ids),
                "geometry": {"type": "LineString", "coordinates": _coordinates(line)},
                "properties": properties,
            }
            self.features.append(_encode(feature))
        self.tracks += 1

    def to_bytes(self) -> bytes:
        """The document, as compact JSON."""
        return b'{"type":"FeatureCollection","features":[' + b",".join(self.features) + b"]}"
