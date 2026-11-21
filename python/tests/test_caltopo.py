import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from skintrackr.caltopo import (
    CaltopoDocument,
    ExportTooLarge,
    export_lines,
    export_points,
    folder_id,
    line_id,
    track_description,
    track_title,
)
from skintrackr.models import Activity, ActivitySummary, SportStyle, Stream, loads
from skintrackr.tracks import BBox
from tests.conftest import ACTIVITY, ATHLETE_ID, STREAMS

FIXTURE = Path(__file__).parent / "fixtures" / "caltopo-export.json"
SUMMARY = ActivitySummary(
    id="i1001",
    name="Snoqualmie Mountain",
    type="BackcountrySki",
    start_date_local="2026-01-04T08:30:00",
    distance=12_345.0,
    moving_time=4 * 3600 + 5 * 60 + 30,
    total_elevation_gain=1234.0,
    strava_id="987",
)
STYLE = SportStyle(label="Backcountry ski", color="#e11d48")
# a box ~111 m across, so tracks in it have no GPS dropouts (`MAX_GAP_M`)
S = 0.001
BOX = BBox(0.0, 0.0, S, S)


def activity() -> Activity:
    return loads(json.dumps(ACTIVITY), Activity)


def streams(*types: str) -> list[Stream]:
    return [s for s in loads(json.dumps(STREAMS), list[Stream]) if s.type in types]


# --- points and lines


def test_export_points_with_altitude_and_time() -> None:
    points = export_points(activity(), streams("time", "latlng", "altitude"))
    # 2024-01-15T08:30:00Z, then 5 s later
    np.testing.assert_allclose(points, [[-122.3321, 47.6062, 56.2, 1705307400000], [-122.33215, 47.60625, 57.0, 1705307405000]])


@pytest.mark.parametrize(
    ("types", "width"),
    [(("latlng", "altitude"), 3), (("time", "latlng"), 2), (("latlng",), 2)],
)
def test_export_points_without_altitude_or_time(types: tuple[str, ...], width: int) -> None:
    assert export_points(activity(), streams(*types)).shape == (2, width)


def test_export_points_without_positions() -> None:
    assert export_points(activity(), streams("time", "altitude")).shape[0] == 0


def track(*lons: float) -> np.ndarray:
    """A track heading east or west across the middle of the box: `lons` are fractions of its width."""
    return np.array([[lon * S, 0.5 * S, 100.0 + i, 1000.0 * i] for i, lon in enumerate(lons)])


def test_export_lines_whole() -> None:
    points = track(-1.0, -0.5, 0.5, 2.0)
    [line] = export_lines(points, BOX, "whole")
    assert line is points  # all of it, unsplit
    assert export_lines(track(2.0, 3.0), BOX, "whole") == []


def test_export_lines_clip() -> None:
    # inside, out, and back in: two pieces
    pieces = export_lines(track(0.5, 1.5, 2.0, 1.5, 0.5), BOX, "clip")
    assert [len(piece) for piece in pieces] == [2, 2]
    assert export_lines(track(2.0, 3.0), BOX, "clip") == []


def test_export_lines_clip_splits_at_dropouts() -> None:
    # two runs in a 1-degree box, ~89 km apart: two pieces, no line across the gap
    points = np.array([[0.1, 0.1], [0.1001, 0.1], [0.9, 0.9], [0.9001, 0.9]])
    assert len(export_lines(points, BBox(0.0, 0.0, 1.0, 1.0), "clip")) == 2


# --- titles, descriptions, ids


def test_track_title() -> None:
    assert track_title(SUMMARY) == "2026-01-04 Snoqualmie Mountain"
    assert track_title(SUMMARY, (2, 3)) == "2026-01-04 Snoqualmie Mountain (2/3)"


@pytest.mark.parametrize(
    ("units", "figures"),
    [("ft", "7.7 mi · 4,049 ft gain · 4:05 moving"), ("m", "12.3 km · 1,234 m gain · 4:05 moving")],
)
def test_track_description(units: str, figures: str) -> None:
    assert track_description(SUMMARY, "Backcountry ski", units) == (
        f"Backcountry ski\n{figures}\nhttps://intervals.icu/activities/i1001\nhttps://www.strava.com/activities/987"
    )


def test_track_description_without_strava() -> None:
    no_strava = ActivitySummary(id="i1", moving_time=59)
    assert track_description(no_strava, "", "m") == "0.0 km · 0 m gain · 0:00 moving\nhttps://intervals.icu/activities/i1"


def test_ids_are_stable() -> None:
    assert folder_id(ATHLETE_ID, "skintrackr exports") == folder_id(ATHLETE_ID, "skintrackr exports")
    assert uuid.UUID(folder_id(ATHLETE_ID, "a")).version == 5
    whole = line_id(ATHLETE_ID, "i1", "a")
    assert whole == line_id(ATHLETE_ID, "i1", "a")
    others = {
        folder_id(ATHLETE_ID, "a"),
        folder_id("i1", "a"),
        line_id(ATHLETE_ID, "i1", "b"),
        line_id(ATHLETE_ID, "i1", None),
        line_id("other", "i1", "a"),
        line_id(ATHLETE_ID, "i1", "a", (BOX, 1, 2)),
        line_id(ATHLETE_ID, "i1", "a", (BOX, 2, 2)),
        line_id(ATHLETE_ID, "i1", "a", (BBox(0.0, 0.0, S, 2 * S), 1, 2)),
    }
    assert whole not in others and len(others) == 8


# --- the document


def document(folder: str | None, mode: str = "whole", limit: int = 1_000) -> CaltopoDocument:
    doc = CaltopoDocument(ATHLETE_ID, folder, limit)
    old = ActivitySummary(id="i1", name="Old", type="Run", start_date_local="2025-12-01T09:00:00", moving_time=600)
    doc.add_track(old, [track(0.25, 0.5)[:, :2]], None, units="m", stroke_width=3)
    lines = export_lines(track(0.5, 1.5, 0.75), BOX, mode)  # leaves the box and comes back
    doc.add_track(SUMMARY, lines, STYLE, units="ft", stroke_width=3, clipped_to=BOX if mode == "clip" else None)
    return doc


def test_document_matches_fixture() -> None:
    """Pins the exact format CalTopo imports (docs/caltopo.md): regenerate the fixture only for a deliberate change."""
    assert json.loads(document("skintrackr exports").to_bytes()) == json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("folder", ["skintrackr exports", None])
def test_document_folder(folder: str | None) -> None:
    features = json.loads(document(folder).to_bytes())["features"]
    folders = [f for f in features if f["properties"]["class"] == "Folder"]
    lines = [f for f in features if f["properties"]["class"] == "Shape"]
    assert len(folders) == (1 if folder else 0) and len(lines) == 2
    assert all(line["properties"].get("folderId") == (folders[0]["id"] if folder else None) for line in lines)
    assert len({f["id"] for f in features}) == len(features)


def test_document_clipped_pieces() -> None:
    doc = document(None, mode="clip")
    lines = json.loads(doc.to_bytes())["features"][1:]
    assert [line["properties"]["title"] for line in lines] == [
        "2026-01-04 Snoqualmie Mountain (1/2)",
        "2026-01-04 Snoqualmie Mountain (2/2)",
    ]
    # crossing points get whole metres and milliseconds
    assert lines[0]["geometry"]["coordinates"][-1] == [S, 0.5 * S, 100, 500]  # 100.5 rounds to even
    assert (doc.tracks, doc.points) == (2, 2 + 4)


def test_document_missing_style_is_red() -> None:
    lines = json.loads(document(None).to_bytes())["features"]
    assert lines[0]["properties"]["stroke"] == "#FF0000"


def test_document_points_limit() -> None:
    with pytest.raises(ExportTooLarge):
        document(None, limit=4)
