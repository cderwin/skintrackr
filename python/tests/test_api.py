import json
import shutil
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.clients.blob_store import archive_keys, tracks_key
from skintrackr.crypto import generate_jwt
from skintrackr.models import ActivitySummary, Stream, loads
from skintrackr.tracks import track_feature
from tests.conftest import (
    ACTIVITY,
    ACTIVITY_ID,
    API_KEY,
    ATHLETE_ID,
    SECRET,
    STREAMS,
    FakeBucket,
    FakeIntervals,
    basic_auth_key,
    bearer,
    connect,
    issue_api_token,
    requires_tippecanoe,
    session,
    summary,
    track_streams,
    tracks_archive,
    wait_for_background,
    with_store,
)

EXPORT = f"/api/activity/{ACTIVITY_ID}/export"
PERSIST = f"/api/activity/{ACTIVITY_ID}/persist"
VERT = "/api/stats/vert"
IMPORT = "/api/import"
ACTIVITIES = "/api/activities"
TRACKS = "/api/tracks.pmtiles"
KEYS = archive_keys(ATHLETE_ID, ACTIVITY_ID)


def request(client: TestClient, path: str, headers: dict[str, str] | None = None):
    return client.post(path, headers=headers) if path in (PERSIST, IMPORT) else client.get(path, headers=headers)


# --- authentication (shared by all routes)


@pytest.mark.parametrize("path", [EXPORT, PERSIST, VERT, IMPORT, ACTIVITIES, TRACKS])
@pytest.mark.parametrize(
    ("headers", "status", "message"),
    [
        ({}, 401, "Authorization header required"),
        ({"Authorization": "Token abc"}, 401, "Invalid authorization format"),
        ({"Authorization": "Bearer not-a-jwt"}, 403, "Invalid or expired token"),
    ],
)
def test_requires_valid_token(
    client: TestClient, intervals: FakeIntervals, path: str, headers: dict[str, str], status: int, message: str
) -> None:
    response = request(client, path, headers)
    assert (response.status_code, response.json()) == (status, {"message": message})
    assert intervals.requests == []


@pytest.mark.parametrize("path", [EXPORT, PERSIST, VERT, IMPORT, ACTIVITIES, TRACKS])
def test_rejects_expired_token(client: TestClient, path: str) -> None:
    token, _ = generate_jwt(ATHLETE_ID, SECRET, timedelta(hours=1), now=time.time() - 7200)
    assert request(client, path, bearer(token)).status_code == 403


@pytest.mark.parametrize("path", [EXPORT, PERSIST, VERT, IMPORT, ACTIVITIES, TRACKS])
def test_rejects_token_signed_with_another_secret(client: TestClient, path: str) -> None:
    token, _ = generate_jwt(ATHLETE_ID, "f" * 64, timedelta(hours=1))
    assert request(client, path, bearer(token)).status_code == 403


@pytest.mark.parametrize("path", [EXPORT, PERSIST, VERT, IMPORT, ACTIVITIES, TRACKS])
def test_rejects_revoked_token(client: TestClient, redis_server: FakeServer, path: str) -> None:
    token = issue_api_token(redis_server)
    assert client.post("/token/revoke", headers=bearer(token)).status_code == 200
    response = request(client, path, bearer(token))
    assert (response.status_code, response.json()) == (403, {"message": "token has been revoked"})


@pytest.mark.parametrize("path", [EXPORT, PERSIST])
def test_athlete_not_connected(client: TestClient, redis_server: FakeServer, path: str) -> None:
    response = request(client, path, bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to fetch intervals.icu API key"})


# --- export


def test_export(client: TestClient, intervals: FakeIntervals, redis_server: FakeServer) -> None:
    intervals.add_activity()
    connect(redis_server)
    response = client.get(EXPORT, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gpx+xml"
    assert response.headers["content-disposition"] == f'attachment; filename="activity-{ACTIVITY_ID}.gpx"'
    assert b"<name>Morning Skin</name>" in response.content
    # intervals.icu was called with the athlete's key
    assert basic_auth_key(intervals.requests[0]) == API_KEY


def test_export_activity_not_found(client: TestClient, redis_server: FakeServer) -> None:
    connect(redis_server)
    response = client.get("/api/activity/i999/export", headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to fetch activity"})


def test_export_stream_failure(client: TestClient, intervals: FakeIntervals, redis_server: FakeServer) -> None:
    intervals.add_activity(streams=[{"type": "watts", "data": [1]}])
    connect(redis_server)
    response = client.get(EXPORT, headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to export activity"})


def test_export_from_archive(client: TestClient, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer) -> None:
    streams = [*STREAMS, {"type": "velocity_smooth", "data": [0, 1.3]}]  # not in GPX: skipped
    bucket.put_json(KEYS.activity, ACTIVITY)
    bucket.put_json(KEYS.streams, streams)
    response = client.get(EXPORT, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    assert b"<name>Morning Skin</name>" in response.content
    assert b"<ele>57</ele>" in response.content
    assert intervals.requests == []  # no key needed, nothing fetched


def test_export_invalid_activity_id(client: TestClient, redis_server: FakeServer) -> None:
    response = client.get("/api/activity/i1.gpx/export", headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (400, {"message": "invalid activity id"})


# --- persist


def test_persist(client: TestClient, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer) -> None:
    intervals.add_activity()
    connect(redis_server)
    response = client.post(PERSIST, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    assert response.json() == {"ok": True, "activityId": ACTIVITY_ID}
    wait_for_background(client)  # the map tracks rebuild
    assert bucket.keys() - {tracks_key(ATHLETE_ID)} == {KEYS.activity, KEYS.streams, KEYS.track}
    assert [a.id for a in with_store(redis_server, lambda s: s.activities(ATHLETE_ID))] == [ACTIVITY_ID]


def test_persist_upload_failure(client: TestClient, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer) -> None:
    intervals.add_activity()
    bucket.fail_writes()
    connect(redis_server)
    response = client.post(PERSIST, headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to persist activity"})


# --- imports


def test_import_runs_in_the_background(client: TestClient, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer) -> None:
    intervals.add_activities([summary(ACTIVITY_ID, "2026-01-02")])
    intervals.add_activity()
    connect(redis_server)
    headers = session(issue_api_token(redis_server))

    started = client.post(IMPORT, headers=headers)
    assert started.status_code == 202
    assert started.json()["state"] == "running"
    wait_for_background(client)

    status = client.get(IMPORT, headers=headers).json()
    assert (status["state"], status["activities"], status["tracks_done"], status["tracks_total"]) == ("done", 1, 1, 1)
    assert status["map_tracks_omitted"] == 0
    assert bucket.keys() - {tracks_key(ATHLETE_ID)} == {KEYS.activity, KEYS.streams, KEYS.track}
    if shutil.which("tippecanoe"):
        assert bucket.read(tracks_key(ATHLETE_ID))[:7] == b"PMTiles"


def test_import_status_before_any_import(client: TestClient, redis_server: FakeServer) -> None:
    status = client.get(IMPORT, headers=bearer(issue_api_token(redis_server))).json()
    assert (status["state"], status["activities"]) == ("idle", 0)


# --- vert stats


def test_vert_stats_from_the_index(client: TestClient, intervals: FakeIntervals, redis_server: FakeServer) -> None:
    today = datetime.now(UTC).date()
    day = f"{today.isoformat()}T09:00:00"
    activities = [
        ActivitySummary(id="i1", type="Hike", start_date_local=day, total_elevation_gain=500.0),
        ActivitySummary(id="i2", type="Ride", start_date_local=day, total_elevation_gain=900.0),
    ]
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, activities))
    response = client.get(VERT, params={"today": today.isoformat()}, headers=session(issue_api_token(redis_server)))

    assert response.status_code == 200
    body = response.json()
    assert (body["as_of"], body["activities"]) == (today.isoformat(), 2)
    assert body["years"][0] == {"year": today.year, "vert_m": 500.0}
    assert [y["year"] for y in body["years"]] == list(range(today.year, today.year - 6, -1))
    assert intervals.requests == []  # nothing fetched from intervals.icu


def test_vert_stats_defaults_to_the_server_date(client: TestClient, redis_server: FakeServer) -> None:
    response = client.get(VERT, headers=bearer(issue_api_token(redis_server)))
    assert response.json()["as_of"] == datetime.now(UTC).date().isoformat()
    assert response.json()["activities"] == 0


def test_vert_stats_rejects_far_off_dates(client: TestClient, redis_server: FakeServer) -> None:
    response = client.get(VERT, params={"today": "2001-01-01"}, headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (400, {"message": "today must be within a day of the current date"})


# --- map


def test_activities(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    rows = [
        ActivitySummary(id="i1", name="Skin", type="BackcountrySki", start_date_local="2025-01-01T09:00:00"),
        ActivitySummary(id="i2", type="Ride", start_date_local="2026-01-01T09:00:00"),
        ActivitySummary(id="i3", type="VirtualRide", start_date_local="2026-02-01T09:00:00"),  # no track
    ]
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, rows))
    with_store(redis_server, lambda s: s.save_activities("i777", [ActivitySummary(id="other")]))
    for activity_id in ("i1", "i2"):
        bucket.put(archive_keys(ATHLETE_ID, activity_id).track)
    response = client.get(ACTIVITIES, headers=session(issue_api_token(redis_server)))
    assert response.status_code == 200
    body = response.json()
    assert sorted(a["id"] for a in body["activities"]) == ["i1", "i2", "i3"]
    assert next(a for a in body["activities"] if a["id"] == "i1")["name"] == "Skin"
    assert (body["on_map"], body["map_tracks_omitted"]) == (["i2", "i1"], 0)


def test_tracks_before_any_build(client: TestClient, redis_server: FakeServer) -> None:
    response = client.get(TRACKS, headers=session(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (404, {"message": "no map tracks yet"})


def test_tracks_whole_and_ranged(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    bucket.put(tracks_key(ATHLETE_ID), b"PMTiles-0123456789")
    bucket.put(tracks_key("i777"), b"someone else's")
    headers = session(issue_api_token(redis_server))

    whole = client.get(TRACKS, headers=headers)
    assert (whole.status_code, whole.content) == (200, b"PMTiles-0123456789")
    assert whole.headers["content-type"] == "application/vnd.pmtiles"
    assert (whole.headers["accept-ranges"], whole.headers["cache-control"]) == ("bytes", "private, no-cache")
    assert "content-range" not in whole.headers

    part = client.get(TRACKS, headers={**headers, "Range": "bytes=0-6"})
    assert (part.status_code, part.content, part.headers["content-range"]) == (206, b"PMTiles", "bytes 0-6/18")
    assert part.headers["etag"] == whole.headers["etag"]

    beyond = client.get(TRACKS, headers={**headers, "Range": "bytes=100-200"})
    assert (beyond.status_code, beyond.json()) == (416, {"message": "range not satisfiable"})


# --- CalTopo exports

PREVIEW = "/api/activities/export/preview"
EXPORT_AREA = "/api/activities/export"
WEST, SOUTH = -121.0, 47.0
# ~760 m east-west, 1.1 km north-south
AREA = [WEST, SOUTH, WEST + 0.01, SOUTH + 0.01]
STEP = 0.0001  # ~11 m


def heading_north(lon: float, lat: float, steps: int) -> list[tuple[float, float]]:
    return [(lat + i * STEP, lon) for i in range(steps + 1)]


TOURS = {
    # id: (type, day, fixes)
    "i1": ("BackcountrySki", "2025-01-10", heading_north(WEST + 0.005, SOUTH + 0.002, 50)),  # inside
    "i2": ("BackcountrySki", "2025-02-20", heading_north(WEST + 0.003, SOUTH + 0.008, 40)),  # leaves to the north
    "i3": ("Run", "2025-03-01", heading_north(WEST + 0.007, SOUTH + 0.002, 20)),  # inside, another sport
    "i4": ("BackcountrySki", "2024-12-01", heading_north(WEST + 0.05, SOUTH, 20)),  # outside
}


def archive_tours(bucket: FakeBucket, redis_server: FakeServer, tours: dict = TOURS) -> None:
    """Archive the tours, index them, and build the map's archive of their tracks."""
    summaries, features = [], []
    for activity_id, (type, day, fixes) in tours.items():
        activity = {**ACTIVITY, "id": activity_id, "name": f"Tour {activity_id}", "type": type,
                    "start_date": f"{day}T17:00:00Z", "start_date_local": f"{day}T09:00:00"}
        streams = track_streams(fixes)
        keys = archive_keys(ATHLETE_ID, activity_id)
        bucket.put_json(keys.activity, activity)
        bucket.put_json(keys.streams, streams)
        summary = loads(json.dumps(activity), ActivitySummary)
        summaries.append(summary)
        features.append(track_feature(summary, loads(json.dumps(streams), list[Stream])))
    bucket.put(tracks_key(ATHLETE_ID), tracks_archive(features))
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, summaries))


def export_body(**fields: Any) -> dict[str, Any]:
    return {
        "bbox": AREA,
        "sports": [],
        "date_from": 20240101,
        "date_to": 20251231,
        "mode": "whole",
        "folder": "skintrackr exports",
        "units": "ft",
        "styles": {"BackcountrySki": {"label": "Backcountry ski", "color": "#e11d48"}},
        "stroke_width": 3,
        **fields,
    }


@pytest.mark.parametrize("path", [PREVIEW, EXPORT_AREA])
def test_export_area_requires_a_token(client: TestClient, path: str) -> None:
    response = client.post(path, json=export_body())
    assert (response.status_code, response.json()) == (401, {"message": "Authorization header required"})


@requires_tippecanoe
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, ["i3", "i2", "i1"]),  # newest first
        ({"sports": ["BackcountrySki"]}, ["i2", "i1"]),
        ({"date_from": 20250201}, ["i3", "i2"]),
    ],
)
def test_export_preview(client: TestClient, bucket: FakeBucket, redis_server: FakeServer, filters: dict, expected: list[str]) -> None:
    archive_tours(bucket, redis_server)
    response = client.post(PREVIEW, json=export_body(**filters), headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (200, {"activity_ids": expected, "limit": 100})


def test_export_preview_without_tracks(client: TestClient, redis_server: FakeServer) -> None:
    response = client.post(PREVIEW, json=export_body(), headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (200, {"activity_ids": [], "limit": 100})


@requires_tippecanoe
def test_export_area_whole(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    archive_tours(bucket, redis_server)
    body = export_body(sports=["BackcountrySki"])
    response = client.post(EXPORT_AREA, json=body, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/geo+json"
    today = datetime.now(UTC).date().isoformat()
    assert response.headers["content-disposition"] == f'attachment; filename="skintrackr-tracks-{today}.json"'
    assert response.headers["x-track-count"] == "2"
    folder, *lines = response.json()["features"]
    assert folder["properties"] == {"class": "Folder", "title": "skintrackr exports", "visible": True, "labelVisible": True}
    assert [line["properties"]["title"] for line in lines] == ["2025-01-10 Tour i1", "2025-02-20 Tour i2"]  # oldest first
    first = lines[0]
    assert first["properties"]["folderId"] == folder["id"]
    assert first["properties"]["stroke"] == "#e11d48"
    assert first["properties"]["description"].startswith("Backcountry ski\n")
    # whole, full detail: every fix, with elevation and time
    assert len(first["geometry"]["coordinates"]) == 51
    assert first["geometry"]["coordinates"][0] == [WEST + 0.005, SOUTH + 0.002, 1000, 1736528400000]
    assert len(lines[1]["geometry"]["coordinates"]) == 41  # the part outside the area too


@requires_tippecanoe
def test_export_area_clip(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    archive_tours(bucket, redis_server)
    body = export_body(sports=["BackcountrySki"], mode="clip", folder=None)
    response = client.post(EXPORT_AREA, json=body, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    lines = response.json()["features"]
    assert all("folderId" not in line["properties"] for line in lines)
    leaving = lines[1]["geometry"]["coordinates"]
    # i2 from its start to the area's north edge: 20 fixes inside, then a point on the edge
    assert len(leaving) == 21 and leaving[-1][1] == pytest.approx(SOUTH + 0.01)


@requires_tippecanoe
def test_export_area_skips_deleted_activities(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    archive_tours(bucket, redis_server)
    bucket.s3.delete_object(Bucket=bucket.NAME, Key=archive_keys(ATHLETE_ID, "i1").streams)
    response = client.post(EXPORT_AREA, json=export_body(), headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.headers["x-track-count"]) == (200, "2")


@requires_tippecanoe
@pytest.mark.parametrize(
    ("limit", "body", "message"),
    [
        ("EXPORT_TRACKS_LIMIT", {}, "3 tracks: exports are limited to 2. Narrow the area or the map's filters."),
        ("EXPORT_POINTS_LIMIT", {}, "This export is too large (more than 2 points). Clip to the area, or narrow it or the filters."),
        (None, {"bbox": [WEST + 0.1, SOUTH, WEST + 0.2, SOUTH + 0.1]}, "No tracks in this area match the map's filters."),
    ],
)
def test_export_area_refused(
    client: TestClient, bucket: FakeBucket, redis_server: FakeServer, monkeypatch: pytest.MonkeyPatch,
    limit: str | None, body: dict, message: str,
) -> None:
    archive_tours(bucket, redis_server)
    if limit:
        monkeypatch.setattr(f"skintrackr.server.api.{limit}", 2)
    response = client.post(EXPORT_AREA, json=export_body(**body), headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (400, {"message": message})


@pytest.mark.parametrize("path", [PREVIEW, EXPORT_AREA])
@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not json", "invalid export request"),
        (b'{"bbox": "everywhere"}', "invalid export request"),
        (json.dumps(export_body(bbox=[1, 0, 0, 1])).encode(), "invalid area"),
        (json.dumps(export_body(bbox=[0, 0, 1])).encode(), "invalid area"),
        (json.dumps(export_body(mode="partial")).encode(), "invalid export request"),
        (json.dumps(export_body(units="furlongs")).encode(), "invalid export request"),
        (json.dumps(export_body(stroke_width=0)).encode(), "invalid export request"),
    ],
)
def test_export_area_invalid_request(client: TestClient, redis_server: FakeServer, path: str, body: bytes, message: str) -> None:
    headers = {**bearer(issue_api_token(redis_server)), "Content-Type": "application/json"}
    response = client.post(path, content=body, headers=headers)
    assert (response.status_code, response.json()) == (400, {"message": message})
