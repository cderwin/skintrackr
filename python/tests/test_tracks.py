import asyncio
import gzip
import json
import math
import shutil
from collections.abc import Sequence
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
    feature = track_feature(ACTIVITY, latlng([(47.123456789, -121.987654321), (47.2, -121.9)]))
    assert feature == {
        "type": "Feature",
        "properties": {"id": "i1", "type": "BackcountrySki", "date": 20250203},
        "geometry": {"type": "LineString", "coordinates": [[-121.987654, 47.123457], [-121.9, 47.2]]},
    }


def test_track_feature_simplifies_within_two_metres() -> None:
    # a straight line north with 1 m wobbles, and one 10 m corner that must stay
    points = [(47.0 + i * 5 * METRE, -121.0 + (METRE if i % 2 else 0) / math.cos(math.radians(47))) for i in range(100)]
    points[50] = (points[50][0], -121.0 + 10 * METRE / math.cos(math.radians(47)))
    kept = coordinates(track_feature(ACTIVITY, latlng(points)))
    # the 1 m wobbles go; the corner stays, with the few points where the line bends into it
    assert [round(points[50][1], 6), round(points[50][0], 6)] in kept
    assert kept[0] == [-121.0, 47.0] and len(kept) <= 8


def test_track_feature_splits_at_gps_dropouts() -> None:
    feature = track_feature(ACTIVITY, latlng([(47.0, -121.0), (47.1, -121.1), None, None, (47.2, -121.2), (47.3, -121.3), None, (47.4, -121.4)]))
    assert feature is not None and feature["geometry"]["type"] == "MultiLineString"
    # the lone point after the last dropout isn't a line
    assert coordinates(feature) == [[[-121.0, 47.0], [-121.1, 47.1]], [[-121.2, 47.2], [-121.3, 47.3]]]


@pytest.mark.parametrize(
    "streams",
    [
        [],  # manual activity
        [Stream(type="heartrate", data=[100, 110])],  # indoor
        latlng([(47.0, -121.0), None, (47.1, -121.1)]),  # no two positions in a row
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
    feature = track_feature(ACTIVITY, latlng([(47.0, -121.0), (47.01, -121.01), (47.02, -121.0)]))
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
