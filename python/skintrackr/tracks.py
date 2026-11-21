"""Activity tracks for the map: one simplified GeoJSON feature per activity, and the athlete's PMTiles archive.

- `track_feature`: an activity's track from its `latlng` stream, split at GPS dropouts (`MAX_GAP_M` between fixes),
  simplified with Douglas–Peucker at `TOLERANCE_M` and rounded to `PRECISION` decimals. Its properties are what the
  map filters on: `id`, `type` and `date` (`YYYYMMDD`, an integer, so date ranges are numeric comparisons).
- `map_track_ids`: which tracks go into the archive: the `MAP_TRACKS_LIMIT` most recent.
- `build_pmtiles`: the archive (layer `tracks`), built with tippecanoe.
- Areas, for exports (docs/plans/caltopo-export.md): `BBox`; `split_at_gaps` and `clip_line` on full-detail
  point arrays; and the prefilter that finds the tracks in an area from the archive's tiles (`prefilter_zoom`,
  `prefilter_margin_m`, `tiles_covering`, `tile_track_ids`).

See docs/plans/activity-map.md, and docs/benchmarks/tippecanoe-rebuild for why the archive is limited.
"""

import asyncio
import json
import math
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self

import mapbox_vector_tile
import numpy as np
import shapely
from attrs import frozen
from shapely.geometry import shape

from skintrackr.models import Activity, ActivitySummary, Stream

# the archive holds at most this many tracks, the most recent ones (rebuilds cost ~7.4 ms of CPU per track)
MAP_TRACKS_LIMIT = 3000
# simplification tolerance: invisible at the archive's max zoom (z14, ~0.6 m per tile unit)
TOLERANCE_M = 2.0
# consecutive fixes farther apart than this are a GPS dropout, which splits the track rather than drawing a
# straight line across it (rare: 17 such jumps in 150 real activities; most fixes are 1–5 s and a few m apart)
MAX_GAP_M = 200.0
# decimal places kept in coordinates (~0.1 m)
PRECISION = 6
LAYER = "tracks"
PMTILES_MEDIA_TYPE = "application/vnd.pmtiles"
TIPPECANOE = "tippecanoe"
MAX_ZOOM = 14
# tippecanoe's line simplification at each zoom, in tile units (its default tolerance, 1 unit, times this)
SIMPLIFICATION = 4
# no track is ever dropped: every zoom keeps every feature, simplified for its zoom. Exports find the tracks in an
# area from these tiles (`tile_track_ids`), which relies on both: change them only with `prefilter_margin_m`
TIPPECANOE_FLAGS = (
    f"--layer={LAYER}",
    f"--maximum-zoom={MAX_ZOOM}",
    "--minimum-zoom=0",
    "--no-tile-size-limit",
    "--no-feature-limit",
    f"--simplification={SIMPLIFICATION}",
    "--read-parallel",
    "--quiet",
    "--force",
)
METERS_PER_DEGREE = 111_320.0
EARTH_CIRCUMFERENCE_M = 40_075_016.686
# Web Mercator's latitude limit
MAX_LATITUDE = 85.051129
# the deepest zoom at which an export's area covers at most this many tiles is the one its prefilter reads
PREFILTER_MAX_TILES = 64
# exports: tracks per export (each is a bucket read and a decode of every stream), points per file (CalTopo
# imports ~100,000 points in 3 s; see docs/caltopo.md), and activities read and built at once
EXPORT_TRACKS_LIMIT = 100
EXPORT_POINTS_LIMIT = 1_000_000
EXPORT_READ_CONCURRENCY = 4

type Tile = tuple[int, int, int]


class TrackBuildError(Exception):
    """tippecanoe failed, or isn't installed."""


def split_at_gaps(points: np.ndarray) -> list[np.ndarray]:
    """Points (rows whose first two columns are lon and lat) split at GPS dropouts (`MAX_GAP_M` between fixes).

    Runs of fewer than two points (a lone fix between dropouts) are left out.
    """
    if len(points) < 2:
        return []
    lon, lat = points[:, 0], points[:, 1]
    scale_x = METERS_PER_DEGREE * np.cos(np.radians(lat[:-1]))
    steps = np.hypot(np.diff(lon) * scale_x, np.diff(lat) * METERS_PER_DEGREE)
    runs = np.split(points, np.flatnonzero(steps > MAX_GAP_M) + 1)
    return [run for run in runs if len(run) >= 2]


def _simplify(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Douglas–Peucker (GEOS) on (lon, lat) points, with `tolerance` in metres (an equirectangular projection)."""
    if len(points) < 3:
        return points
    scale = np.array([METERS_PER_DEGREE * math.cos(math.radians(points[0, 1])), METERS_PER_DEGREE])
    line = shapely.simplify(shapely.linestrings(points * scale), tolerance, preserve_topology=False)
    return shapely.get_coordinates(line) / scale


def _segments(streams: list[Stream]) -> list[np.ndarray]:
    """The `latlng` stream's positions as (lon, lat) runs, split at GPS dropouts (`MAX_GAP_M` between fixes).

    Samples without a position are skipped: watches log a fix every few seconds while other streams have one
    sample a second, so most gaps are only a sample or two.
    """
    latlng = next((stream for stream in streams if stream.type == "latlng"), None)
    if latlng is None:
        return []
    points = [(lon, lat) for lat, lon in zip(latlng.data, latlng.data2) if lat is not None and lon is not None]
    return split_at_gaps(np.array(points, dtype=float).reshape(-1, 2))


def feature_date(start_date_local: str) -> int:
    """An activity's local start date as `YYYYMMDD`; 0 if it has none."""
    digits = start_date_local[:10].replace("-", "")
    return int(digits) if len(digits) == 8 and digits.isdigit() else 0


def track_feature(activity: Activity | ActivitySummary, streams: list[Stream]) -> dict[str, Any] | None:
    """An activity's track as a GeoJSON feature; None if it has no positions (indoor or manual activities)."""
    lines = [
        _simplify(segment, TOLERANCE_M).round(PRECISION).tolist() for segment in _segments(streams)
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


# --- areas, for exports


@frozen
class BBox:
    """An area, in degrees: west < east (it can't cross the antimeridian), south < north, within Web Mercator."""

    west: float
    south: float
    east: float
    north: float

    @classmethod
    def from_list(cls, values: list[float]) -> Self:
        """A box from `[west, south, east, north]`.

        Raises:
            ValueError: It isn't four numbers, or isn't a valid box.
        """
        if len(values) != 4 or not all(isinstance(v, int | float) and math.isfinite(v) for v in values):
            raise ValueError(f"not a box: {values!r}")
        box = cls(*map(float, values))
        if not (-180 <= box.west < box.east <= 180 and -MAX_LATITUDE <= box.south < box.north <= MAX_LATITUDE):
            raise ValueError(f"not a box: {values!r}")
        return box

    def expanded(self, metres: float) -> Self:
        """The box grown by at least `metres` on every side (clamped to the valid range)."""
        dlat = metres / METERS_PER_DEGREE
        widest = max(abs(self.south), abs(self.north)) + dlat
        dlon = metres / (METERS_PER_DEGREE * math.cos(math.radians(min(widest, MAX_LATITUDE))))
        return type(self)(
            max(self.west - dlon, -180.0),
            max(self.south - dlat, -MAX_LATITUDE),
            min(self.east + dlon, 180.0),
            min(self.north + dlat, MAX_LATITUDE),
        )

    @property
    def polygon(self) -> shapely.Polygon:
        """The box as a shapely polygon, in lon/lat."""
        return shapely.box(self.west, self.south, self.east, self.north)


def meets(points: np.ndarray, bbox: BBox) -> bool:
    """Whether a line (rows whose first two columns are lon and lat) touches the box, its edges included."""
    return bool(shapely.intersects(shapely.linestrings(points[:, :2]), bbox.polygon))


def clip_line(points: np.ndarray, bbox: BBox) -> list[np.ndarray]:
    """The pieces of a line inside the box (Liang–Barsky, vectorized), its edges counting as inside.

    Points are rows whose first two columns are lon and lat; where a piece enters or leaves the box, every column
    is interpolated, so a crossing point gets a matching elevation and time. GEOS overlays (`shapely`'s
    `intersection`) can't do that: they carry at most one extra value.
    """
    if len(points) < 2:
        return []
    start, step = points[:-1], np.diff(points, axis=0)
    x, y, dx, dy = start[:, 0], start[:, 1], step[:, 0], step[:, 1]
    # for each edge, p * t <= q: the step's parameter t where it crosses that edge
    p = np.stack([-dx, dx, -dy, dy])
    q = np.stack([x - bbox.west, bbox.east - x, y - bbox.south, bbox.north - y])
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = q / p
    t0 = np.maximum(0.0, np.where(p < 0, ratio, -np.inf).max(axis=0))
    t1 = np.minimum(1.0, np.where(p > 0, ratio, np.inf).min(axis=0))
    kept = (t0 <= t1) & ~((p == 0) & (q < 0)).any(axis=0)
    steps = np.flatnonzero(kept)
    if not len(steps):
        return []
    # a piece continues while kept steps follow each other through a point inside the box
    joined = (np.diff(steps) == 1) & (t1[steps[:-1]] == 1.0) & (t0[steps[1:]] == 0.0)
    pieces = []
    for run in np.split(steps, np.flatnonzero(~joined) + 1):
        first, last = run[0], run[-1]
        entry = start[first] + t0[first] * step[first]
        leave = start[last] + t1[last] * step[last]
        piece = np.vstack([entry, points[first + 1 : last + 1], leave])
        if (piece[:, :2] != piece[0, :2]).any():  # not just a touch at one point
            pieces.append(piece)
    return pieces


@frozen
class TrackFilters:
    """The map's filters, applied to its tracks' `type` and `date` properties as `filterExpression` does.

    Attributes:
        sports: Activity types to keep; empty keeps every type.
        date_from: First local start date (`YYYYMMDD`), inclusive.
        date_to: Last local start date (`YYYYMMDD`), inclusive.
    """

    sports: frozenset[str]
    date_from: int
    date_to: int

    def matches(self, properties: dict[str, Any]) -> bool:
        """Whether a track's properties pass the filters."""
        type_ok = not self.sports or properties.get("type") in self.sports
        date = properties.get("date")
        return type_ok and isinstance(date, int) and self.date_from <= date <= self.date_to


def _tile_xy(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """A position in (fractional) tile coordinates at `zoom` (Web Mercator, y down)."""
    n = 2**zoom
    lat = math.radians(max(-MAX_LATITUDE, min(MAX_LATITUDE, lat)))
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(lat)) / math.pi) / 2 * n


def _tile_ranges(bbox: BBox, zoom: int) -> tuple[range, range]:
    """The columns and rows of the tiles at `zoom` that the box covers."""
    n = 2**zoom
    x0, y0 = _tile_xy(bbox.west, bbox.north, zoom)
    x1, y1 = _tile_xy(bbox.east, bbox.south, zoom)
    return range(max(int(x0), 0), min(int(x1), n - 1) + 1), range(max(int(y0), 0), min(int(y1), n - 1) + 1)


def tiles_covering(bbox: BBox, zoom: int) -> list[Tile]:
    """The tiles at `zoom` that the box covers, as `(z, x, y)`."""
    xs, ys = _tile_ranges(bbox, zoom)
    return [(zoom, x, y) for x in xs for y in ys]


def prefilter_zoom(bbox: BBox) -> int:
    """The deepest zoom (at most the archive's) at which the box covers at most `PREFILTER_MAX_TILES` tiles."""

    def count(zoom: int) -> int:
        xs, ys = _tile_ranges(bbox, zoom)
        return len(xs) * len(ys)

    return next((z for z in range(MAX_ZOOM, 0, -1) if count(z) <= PREFILTER_MAX_TILES), 0)


def prefilter_margin_m(zoom: int, lat: float) -> float:
    """How far a track in the archive's tiles at `zoom` can be from the full track, near latitude `lat`.

    The track itself is simplified by `TOLERANCE_M`; tippecanoe then moves it up to `SIMPLIFICATION` tile units
    at each zoom, plus half a unit of rounding (one unit is allowed).
    """
    unit_m = EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (2**zoom * 4096)
    return TOLERANCE_M + (SIMPLIFICATION + 1) * unit_m


def tile_track_ids(tile: bytes, at: Tile, bbox: BBox, filters: TrackFilters) -> dict[str, int]:
    """The tracks in one of the archive's tiles (decompressed) that pass the filters and meet the box.

    Args:
        tile: The tile, a Mapbox vector tile.
        at: Its `(z, x, y)`.
        bbox: The area, already grown by the prefilter's margin.
        filters: The map's filters.

    Returns:
        Each track's activity id, and its date (`YYYYMMDD`).
    """
    layer = mapbox_vector_tile.decode(tile, default_options={"y_coord_down": True}).get(LAYER)
    features = [f for f in (layer or {}).get("features", []) if filters.matches(f["properties"])]
    if not features:
        return {}
    zoom, x, y = at
    extent = layer["extent"]
    (west, north), (east, south) = (_tile_xy(bbox.west, bbox.north, zoom), _tile_xy(bbox.east, bbox.south, zoom))
    area = shapely.box((west - x) * extent, (north - y) * extent, (east - x) * extent, (south - y) * extent)
    hits = shapely.intersects([shape(f["geometry"]) for f in features], area)
    return {f["properties"]["id"]: f["properties"]["date"] for f, hit in zip(features, hits) if hit}


def area_track_ids(tiles: Iterable[tuple[Tile, bytes]], bbox: BBox, filters: TrackFilters) -> list[str]:
    """The tracks in the tiles that pass the filters and meet the (grown) box, newest first (by date, then id)."""
    found: dict[str, int] = {}
    for at, tile in tiles:
        found |= tile_track_ids(tile, at, bbox, filters)
    return sorted(found, key=lambda activity_id: (found[activity_id], activity_id), reverse=True)
