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

import pytest

from skintrackr.models import ActivitySummary, Stream
from skintrackr.tracks import (
    MAP_TRACKS_LIMIT,
    TrackBuildError,
    build_pmtiles,
    encode_feature,
    feature_date,
    map_track_ids,
    track_feature,
)

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


@pytest.mark.skipif(shutil.which("tippecanoe") is None, reason="tippecanoe isn't installed (CI installs it)")
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
