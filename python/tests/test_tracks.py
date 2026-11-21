import asyncio
import gzip
import json
import math
import os
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pmtiles.reader import MemorySource, Reader

from skintrackr.models import ActivitySummary, Stream
from skintrackr.tracks import (
    MAP_TRACKS_LIMIT,
    MAX_ZOOM,
    PREFILTER_MAX_TILES,
    BBox,
    Tile,
    TrackBuildError,
    TrackFilters,
    area_track_ids,
    build_pmtiles,
    clip_line,
    encode_feature,
    feature_date,
    map_track_ids,
    meets,
    prefilter_margin_m,
    prefilter_zoom,
    split_at_gaps,
    tiles_covering,
    track_feature,
)
from tests.conftest import requires_tippecanoe, tracks_archive

ACTIVITY = ActivitySummary(id="i1", type="BackcountrySki", start_date_local="2025-02-03T08:00:00")
# 1e-5 degrees of latitude is ~1.1 m
METRE = 1 / 111_320


def latlng(points: Sequence[tuple[float, float] | None]) -> list[Stream]:
    """A latlng stream from (lat, lon) samples; None is a sample without a position."""
    return [
        Stream(type="time", data=list(range(len(points)))),
        Stream(type="latlng", data=[p and p[0] for p in points], data2=[p and p[1] for p in points]),
    ]


def coordinates(feature: dict[str, Any] | None) -> Any:
    assert feature is not None
    return feature["geometry"]["coordinates"]


def test_track_feature_properties_and_rounding() -> None:
    feature = track_feature(ACTIVITY, latlng([(47.123456789, -121.987654321), (47.1235, -121.9876)]))
    assert feature == {
        "type": "Feature",
        "properties": {"id": "i1", "type": "BackcountrySki", "date": 20250203},
        "geometry": {"type": "LineString", "coordinates": [[-121.987654, 47.123457], [-121.9876, 47.1235]]},
    }


def test_track_feature_simplifies_within_two_metres() -> None:
    # a straight line north with 1 m wobbles, and one 10 m corner that must stay
    points = [(47.0 + i * 5 * METRE, -121.0 + (METRE if i % 2 else 0) / math.cos(math.radians(47))) for i in range(100)]
    points[50] = (points[50][0], -121.0 + 10 * METRE / math.cos(math.radians(47)))
    kept = coordinates(track_feature(ACTIVITY, latlng(points)))
    # the 1 m wobbles go; the corner stays, with the few points where the line bends into it
    assert [round(points[50][1], 6), round(points[50][0], 6)] in kept
    assert kept[0] == [-121.0, 47.0] and len(kept) <= 8


def test_track_feature_joins_fixes_across_samples_without_positions() -> None:
    # as watches record: a fix every few samples, 10 m apart, heading north then east (so no point simplifies away)
    fixes = [(47.0 + min(i, 3) * 10 * METRE, -121.0 + max(i - 3, 0) * 10 * METRE / math.cos(math.radians(47))) for i in range(6)]
    samples: list[tuple[float, float] | None] = [None]
    for fix in fixes:
        samples += [fix, None, None]
    corners = [fixes[0], fixes[3], fixes[5]]
    assert coordinates(track_feature(ACTIVITY, latlng(samples))) == [[round(lon, 6), round(lat, 6)] for lat, lon in corners]


def test_track_feature_splits_at_gps_dropouts() -> None:
    # 1 km between the second and third fixes; the lone fix 1 km after that isn't a line
    north = [(47.0 + i * 10 * METRE, -121.0) for i in (0, 1, 101, 102, 202)]
    feature = track_feature(ACTIVITY, latlng([north[0], north[1], None, north[2], north[3], north[4]]))
    assert feature is not None and feature["geometry"]["type"] == "MultiLineString"
    assert coordinates(feature) == [[[-121.0, 47.0], [-121.0, 47.00009]], [[-121.0, 47.009073], [-121.0, 47.009163]]]


@pytest.mark.parametrize(
    "streams",
    [
        [],  # manual activity
        [Stream(type="heartrate", data=[100, 110])],  # indoor
        latlng([(47.0, -121.0), None, (47.1, -121.1)]),  # two fixes, 13 km apart
    ],
)
def test_track_feature_without_positions(streams: list[Stream]) -> None:
    assert track_feature(ACTIVITY, streams) is None


@pytest.mark.parametrize(("value", "expected"), [("2025-02-03T08:00:00", 20250203), ("", 0), ("garbage", 0)])
def test_feature_date(value: str, expected: int) -> None:
    assert feature_date(value) == expected


def test_map_track_ids_newest_first() -> None:
    index = [
        ActivitySummary(id="old", start_date_local="2020-01-01T09:00:00"),
        ActivitySummary(id="new", start_date_local="2025-01-01T09:00:00"),
        ActivitySummary(id="b", start_date_local="2024-01-01T09:00:00"),
        ActivitySummary(id="a", start_date_local="2024-01-01T09:00:00"),  # same start: ties broken by id
        ActivitySummary(id="indoor", start_date_local="2026-01-01T09:00:00"),  # no track
    ]
    tracks = map_track_ids(index, {"old", "new", "a", "b", "not-indexed"}, limit=3)
    assert (tracks.included, tracks.omitted) == (["new", "b", "a"], 1)


@pytest.mark.parametrize(("count", "omitted"), [(MAP_TRACKS_LIMIT, 0), (MAP_TRACKS_LIMIT + 1, 1)])
def test_map_track_ids_limit(count: int, omitted: int) -> None:
    index = [ActivitySummary(id=f"i{n}", start_date_local=f"2025-01-01T{n:09d}") for n in range(count)]
    tracks = map_track_ids(index, {a.id for a in index})
    assert (len(tracks.included), tracks.omitted) == (MAP_TRACKS_LIMIT, omitted)
    assert f"i{count - 1}" in tracks.included and ("i0" in tracks.included) == (omitted == 0)


@requires_tippecanoe
def test_build_pmtiles() -> None:
    feature = track_feature(ACTIVITY, latlng([(47.0, -121.0), (47.001, -121.001), (47.002, -121.0)]))
    assert feature is not None
    archive = asyncio.run(build_pmtiles([encode_feature(feature)]))
    assert archive[:7] == b"PMTiles" and archive[7] == 3  # magic and spec version
    metadata_offset, metadata_length = (int.from_bytes(archive[i : i + 8], "little") for i in (24, 32))
    compression = archive[97]  # 2: gzip
    metadata = archive[metadata_offset : metadata_offset + metadata_length]
    if compression == 2:
        metadata = gzip.decompress(metadata)
    assert [layer["id"] for layer in json.loads(metadata)["vector_layers"]] == ["tracks"]


def test_build_pmtiles_without_tippecanoe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skintrackr.tracks.TIPPECANOE", "tippecanoe-that-does-not-exist")
    with pytest.raises(TrackBuildError, match="isn't installed"):
        asyncio.run(build_pmtiles([b"{}"]))


@pytest.mark.parametrize("delay", [0, 0.05, 0.3])
def test_build_pmtiles_cancelled_stops_tippecanoe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delay: float) -> None:
    """Cancelling a build (at shutdown, or when an athlete disconnects) ends it promptly and kills tippecanoe."""
    pid_file = tmp_path / "pid"
    slow = tmp_path / "slow-tippecanoe"
    slow.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n")
    slow.chmod(0o755)
    monkeypatch.setattr("skintrackr.tracks.TIPPECANOE", str(slow))

    async def run() -> None:
        task = asyncio.create_task(build_pmtiles([b"{}"]))
        await asyncio.sleep(delay)
        task.cancel()
        async with asyncio.timeout(5):
            with pytest.raises(asyncio.CancelledError):
                await task

    started = time.monotonic()
    asyncio.run(run())
    assert time.monotonic() - started < 5
    if pid_file.exists() and pid_file.read_text().strip():
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("tippecanoe is still running")


# --- areas, for exports

BOX = BBox(0.0, 0.0, 1.0, 1.0)


def line(*rows: tuple[float, ...]) -> np.ndarray:
    return np.array(rows, dtype=float)


def test_split_at_gaps_keeps_extra_columns() -> None:
    # 1 km between the second and third fixes; the lone fix after another jump is dropped
    lats = [47.0, 47.0001, 47.0101, 47.0102, 47.0302]
    points = line(*((-121.0, lat, 1000.0 + i, i * 1000.0) for i, lat in enumerate(lats)))
    runs = split_at_gaps(points)
    assert [run[:, 3].tolist() for run in runs] == [[0.0, 1000.0], [2000.0, 3000.0]]


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        # entirely inside: unchanged
        (line((0.2, 0.2, 10, 0), (0.8, 0.8, 20, 10)), [[(0.2, 0.2, 10, 0), (0.8, 0.8, 20, 10)]]),
        # entirely outside
        (line((2, 2, 0, 0), (3, 3, 0, 0)), []),
        # entering and leaving, with elevation and time interpolated on the edges
        (
            line((-1, 0.5, 100, 0), (0.5, 0.5, 200, 1500), (2, 0.5, 300, 3000)),
            [[(0, 0.5, 100 + 100 * 2 / 3, 1000), (0.5, 0.5, 200, 1500), (1, 0.5, 200 + 100 / 3, 2000)]],
        ),
        # leaving and coming back: two pieces
        (
            line((0.5, 0.5, 0, 0), (0.5, 1.5, 0, 0), (0.6, 0.5, 0, 0)),
            [[(0.5, 0.5, 0, 0), (0.5, 1, 0, 0)], [(0.55, 1, 0, 0), (0.6, 0.5, 0, 0)]],
        ),
        # a point exactly on an edge counts as inside
        (line((1, 0.5, 0, 0), (0.5, 0.5, 0, 0)), [[(1, 0.5, 0, 0), (0.5, 0.5, 0, 0)]]),
        # crossing the box with no point inside
        (line((-1, -1, 0, 0), (2, 2, 3, 3)), [[(0, 0, 1, 1), (1, 1, 2, 2)]]),
        # touching a corner only: no piece
        (line((-1, 0, 0, 0), (0, 1, 0, 0), (-1, 2, 0, 0)), []),
    ],
)
def test_clip_line(points: np.ndarray, expected: list[list[tuple[float, ...]]]) -> None:
    pieces = clip_line(points, BOX)
    assert len(pieces) == len(expected)
    for piece, rows in zip(pieces, expected):
        np.testing.assert_allclose(piece, np.array(rows, dtype=float))


def test_clip_line_two_columns() -> None:
    [piece] = clip_line(line((-1, 0.5), (0.5, 0.5)), BOX)
    np.testing.assert_allclose(piece, [[0, 0.5], [0.5, 0.5]])


def test_meets() -> None:
    assert meets(line((-1, 0.5, 0), (2, 0.5, 0)), BOX)  # crosses, no point inside
    assert meets(line((1, 0.5), (2, 0.5)), BOX)  # starts on the edge
    assert not meets(line((1.1, 0.5), (2, 0.5)), BOX)


@pytest.mark.parametrize(
    "values",
    [[], [0, 0, 1], [1, 0, 0, 1], [0, 1, 1, 0], [-181, 0, 0, 1], [0, 0, 1, 89], [0, 0, "1", 1], [0, 0, float("nan"), 1]],
)
def test_bbox_rejects_invalid_boxes(values: list) -> None:
    with pytest.raises(ValueError):
        BBox.from_list(values)


def test_bbox_expanded() -> None:
    box = BBox.from_list([-121.0, 47.0, -120.9, 47.1]).expanded(10)
    assert box.south == pytest.approx(47.0 - 10 * METRE)
    # at least 10 m of longitude at the box's northern edge
    assert (-121.0 - box.west) * 111_320 * math.cos(math.radians(47.1 + 10 * METRE)) == pytest.approx(10)
    assert BBox(-180, -85, 180, 85).expanded(10_000) == BBox(-180, -85.051129, 180, 85.051129)


def test_prefilter_zoom() -> None:
    assert prefilter_zoom(BBox(-121.01, 47.0, -121.0, 47.01)) == MAX_ZOOM
    big = BBox(-121.5, 47.0, -120.8, 47.5)
    zoom = prefilter_zoom(big)
    assert zoom < MAX_ZOOM and len(tiles_covering(big, zoom)) <= PREFILTER_MAX_TILES < len(tiles_covering(big, zoom + 1))
    assert prefilter_zoom(BBox(-180, -85, 180, 85)) == 3  # 8 x 8 tiles


def test_tiles_covering() -> None:
    # y counts down from the north
    assert tiles_covering(BBox(-180, -85, 180, 85), 1) == [(1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)]
    assert tiles_covering(BBox(10, 10, 20, 20), 1) == [(1, 1, 0)]
    assert tiles_covering(BBox(-20, -20, -10, -10), 2) == [(2, 1, 2)]


def test_prefilter_margin() -> None:
    # 2 m, plus 5 tile units: ~0.4 m each at z14 and 47°N, 8 times that at z11
    assert prefilter_margin_m(14, 47) == pytest.approx(4.0, abs=0.1)
    assert prefilter_margin_m(11, 47) == pytest.approx(18.3, abs=0.1)


def archive_tiles(archive: bytes, tiles: list[Tile]) -> list[tuple[Tile, bytes]]:
    reader = Reader(MemorySource(archive))
    return [(tile, gzip.decompress(data)) for tile in tiles if (data := reader.get(*tile))]


def feature(activity_id: str, points: list[tuple[float, float]], type: str = "BackcountrySki", day: str = "2025-02-03") -> dict[str, Any] | None:
    activity = ActivitySummary(id=activity_id, type=type, start_date_local=f"{day}T08:00:00")
    return track_feature(activity, latlng(points))


def north(lon: float, lat: float, metres: float) -> list[tuple[float, float]]:
    """(lat, lon) fixes 10 m apart heading north from (lon, lat), `metres` in all."""
    count = int(metres / 10) + 1
    return [(lat + i * metres / (count - 1) * METRE, lon) for i in range(count)]


ALL = TrackFilters(sports=frozenset(), date_from=0, date_to=99991231)


@requires_tippecanoe
@pytest.mark.parametrize("size_m", [500, 50_000])  # read at z14, and at a lower zoom
def test_area_track_ids_from_the_archive(size_m: float) -> None:
    """The prefilter finds the tracks in an area from the server's own archive, at any zoom."""
    west, south = -121.0, 47.0
    east, top = west + size_m * METRE / math.cos(math.radians(47)), south + size_m * METRE
    area = BBox(west, south, east, top)
    edge = (south + top) / 2
    archive = tracks_archive([
        feature("inside", north(west + (east - west) / 2, south + (top - south) / 4, size_m / 4)),
        # outside the east edge by 1 m: inside the margin, so it's a candidate (the exact test drops it later)
        feature("near", north(east + METRE / math.cos(math.radians(edge)), edge, 30)),
        feature("outside", north(east + 0.05, edge, 300)),
        feature("run", north(west + (east - west) / 3, south + (top - south) / 3, 100), type="Run"),
        feature("old", north(west + (east - west) / 5, south + (top - south) / 5, 100), day="2019-01-01"),
    ])
    zoom = prefilter_zoom(area)
    grown = area.expanded(prefilter_margin_m(zoom, 47))
    tiles = archive_tiles(archive, tiles_covering(grown, zoom))
    assert set(area_track_ids(tiles, grown, ALL)) == {"inside", "near", "run", "old"}
    filters = TrackFilters(sports=frozenset({"BackcountrySki"}), date_from=20200101, date_to=20251231)
    assert area_track_ids(tiles, grown, filters) == ["near", "inside"]  # newest first, then by id


def test_track_filters() -> None:
    filters = TrackFilters(sports=frozenset({"Run"}), date_from=20250101, date_to=20251231)
    assert filters.matches({"type": "Run", "date": 20250101})
    assert not filters.matches({"type": "Run", "date": 20241231})
    assert not filters.matches({"type": "Hike", "date": 20250601})
    assert not filters.matches({"type": "Run"})
    assert ALL.matches({"type": "Anything", "date": 1})
