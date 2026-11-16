"""Activity tracks for the map: one simplified GeoJSON feature per activity, and the athlete's PMTiles archive.

- `track_feature`: an activity's track from its `latlng` stream, split at GPS dropouts, simplified with
  Douglas–Peucker at `TOLERANCE_M` and rounded to `PRECISION` decimals. Its properties are what the map filters
  on: `id`, `type` and `date` (`YYYYMMDD`, an integer, so date ranges are numeric comparisons).
- `map_track_ids`: which tracks go into the archive: the `MAP_TRACKS_LIMIT` most recent.
- `build_pmtiles`: the archive (layer `tracks`), built with tippecanoe.

See docs/plans/activity-map.md, and docs/benchmarks/tippecanoe-rebuild for why the archive is limited.
"""

import asyncio
import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from attrs import frozen

from skintrackr.models import Activity, ActivitySummary, Stream

# the archive holds at most this many tracks, the most recent ones (rebuilds cost ~7.4 ms of CPU per track)
MAP_TRACKS_LIMIT = 3000
# simplification tolerance: invisible at the archive's max zoom (z14, ~0.6 m per tile unit)
TOLERANCE_M = 2.0
# decimal places kept in coordinates (~0.1 m)
PRECISION = 6
LAYER = "tracks"
PMTILES_MEDIA_TYPE = "application/vnd.pmtiles"
TIPPECANOE = "tippecanoe"
# no track is ever dropped: every zoom keeps every feature, simplified for its zoom
TIPPECANOE_FLAGS = (
    f"--layer={LAYER}",
    "--maximum-zoom=14",
    "--minimum-zoom=0",
    "--no-tile-size-limit",
    "--no-feature-limit",
    "--simplification=4",
    "--read-parallel",
    "--quiet",
    "--force",
)
METERS_PER_DEGREE = 111_320.0

type Point = tuple[float, float]


class TrackBuildError(Exception):
    """tippecanoe failed, or isn't installed."""


def _simplify(points: list[Point], tolerance: float) -> list[Point]:
    """Douglas–Peucker on (lon, lat) points, with `tolerance` in metres (an equirectangular projection)."""
    if len(points) < 3:
        return points
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(points[0][1]))
    xy = [(lon * scale_x, lat * METERS_PER_DEGREE) for lon, lat in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = xy[first], xy[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        farthest, distance = first, 0.0
        for i in range(first + 1, last):
            px, py = xy[i][0] - ax, xy[i][1] - ay
            d = abs(dx * py - dy * px) / length if length else math.hypot(px, py)
            if d > distance:
                farthest, distance = i, d
        if distance > tolerance:
            keep[farthest] = True
            stack += [(first, farthest), (farthest, last)]
    return [point for point, kept in zip(points, keep) if kept]


def _segments(streams: list[Stream]) -> list[list[Point]]:
    """The `latlng` stream as (lon, lat) runs, split wherever a sample has no position."""
    latlng = next((stream for stream in streams if stream.type == "latlng"), None)
    if latlng is None:
        return []
    segments: list[list[Point]] = [[]]
    for lat, lon in zip(latlng.data, latlng.data2):
        if lat is None or lon is None:
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append((float(lon), float(lat)))
    return [segment for segment in segments if len(segment) >= 2]


def feature_date(start_date_local: str) -> int:
    """An activity's local start date as `YYYYMMDD`; 0 if it has none."""
    digits = start_date_local[:10].replace("-", "")
    return int(digits) if len(digits) == 8 and digits.isdigit() else 0


def track_feature(activity: Activity | ActivitySummary, streams: list[Stream]) -> dict[str, Any] | None:
    """An activity's track as a GeoJSON feature; None if it has no positions (indoor or manual activities)."""
    lines = [
        [[round(lon, PRECISION), round(lat, PRECISION)] for lon, lat in _simplify(segment, TOLERANCE_M)]
        for segment in _segments(streams)
    ]
    if not lines:
        return None
    geometry = {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 else {"type": "MultiLineString", "coordinates": lines}
    properties = {"id": activity.id, "type": activity.type, "date": feature_date(activity.start_date_local)}
    return {"type": "Feature", "properties": properties, "geometry": geometry}


@frozen
class MapTracks:
    """The tracks an athlete's archive holds.

    Attributes:
        included: Activity ids in the archive, newest first (at most `MAP_TRACKS_LIMIT`).
        omitted: Older activities with a track that are left out.
    """

    included: list[str]
    omitted: int


def map_track_ids(index: list[ActivitySummary], track_ids: set[str], limit: int = MAP_TRACKS_LIMIT) -> MapTracks:
    """The most recent `limit` indexed activities that have a track (newest first by local start, then id)."""
    ids = [a.id for a in sorted(index, key=lambda a: (a.start_date_local, a.id), reverse=True) if a.id in track_ids]
    return MapTracks(included=ids[:limit], omitted=max(len(ids) - limit, 0))


async def build_pmtiles(features: list[bytes]) -> bytes:
    """A PMTiles archive of GeoJSON features (one per item), built with tippecanoe.

    Raises:
        TrackBuildError: tippecanoe isn't installed, or failed.
    """
    with tempfile.TemporaryDirectory(prefix="skintrackr-tracks-") as work:
        source, archive = Path(work) / "tracks.ndjson", Path(work) / "tracks.pmtiles"
        await asyncio.to_thread(source.write_bytes, b"\n".join(features) + b"\n")
        # Popen and a thread rather than asyncio's subprocesses: a cancel while those connect their pipes can
        # hang the task forever (CPython 3.12), which would block the athlete's later rebuilds
        try:
            process = subprocess.Popen(
                [TIPPECANOE, f"--output={archive}", f"--temporary-directory={work}", *TIPPECANOE_FLAGS, str(source)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise TrackBuildError(f"{TIPPECANOE} isn't installed (see python/README.md)") from e
        try:
            _, stderr = await asyncio.to_thread(process.communicate)
        except asyncio.CancelledError:
            process.kill()
            raise
        if process.returncode != 0:
            raise TrackBuildError(f"{TIPPECANOE} exited with {process.returncode}: {stderr.decode(errors='replace')[-500:]}")
        return await asyncio.to_thread(archive.read_bytes)


def encode_feature(feature: dict[str, Any]) -> bytes:
    """A feature as compact JSON (one line, as tippecanoe reads it)."""
    return json.dumps(feature, separators=(",", ":")).encode()
