import shutil
import time
from datetime import UTC, datetime, timedelta

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.clients.blob_store import archive_keys, tracks_key
from skintrackr.crypto import generate_jwt
from skintrackr.models import ActivitySummary
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
    session,
    summary,
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
