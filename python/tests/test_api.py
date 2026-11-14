import time
from datetime import timedelta

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.crypto import generate_jwt
from tests.conftest import (
    ACTIVITY_ID,
    ATHLETE_ID,
    SECRET,
    FakeBucket,
    FakeStrava,
    bearer,
    issue_api_token,
    save_strava_token,
    with_store,
)

EXPORT = f"/api/activity/{ACTIVITY_ID}/export"
PERSIST = f"/api/activity/{ACTIVITY_ID}/persist"


def request(client: TestClient, path: str, headers: dict[str, str] | None = None):
    return client.get(path, headers=headers) if path == EXPORT else client.post(path, headers=headers)


# --- authentication (shared by both routes)


@pytest.mark.parametrize("path", [EXPORT, PERSIST])
@pytest.mark.parametrize(
    ("headers", "status", "message"),
    [
        ({}, 401, "Authorization header required"),
        ({"Authorization": "Token abc"}, 401, "Invalid authorization format"),
        ({"Authorization": "Bearer not-a-jwt"}, 403, "Invalid or expired token"),
    ],
)
def test_requires_valid_token(
    client: TestClient, strava: FakeStrava, path: str, headers: dict[str, str], status: int, message: str
) -> None:
    response = request(client, path, headers)
    assert (response.status_code, response.json()) == (status, {"message": message})
    assert strava.requests == []


@pytest.mark.parametrize("path", [EXPORT, PERSIST])
def test_rejects_expired_token(client: TestClient, path: str) -> None:
    token, _ = generate_jwt(ATHLETE_ID, SECRET, timedelta(hours=1), now=time.time() - 7200)
    assert request(client, path, bearer(token)).status_code == 403


@pytest.mark.parametrize("path", [EXPORT, PERSIST])
def test_rejects_token_signed_with_another_secret(client: TestClient, path: str) -> None:
    token, _ = generate_jwt(ATHLETE_ID, "f" * 64, timedelta(hours=1))
    assert request(client, path, bearer(token)).status_code == 403


@pytest.mark.parametrize("path", [EXPORT, PERSIST])
def test_rejects_revoked_token(client: TestClient, redis_server: FakeServer, path: str) -> None:
    token = issue_api_token(redis_server)
    assert client.post("/token/revoke", headers=bearer(token)).status_code == 200
    response = request(client, path, bearer(token))
    assert (response.status_code, response.json()) == (403, {"message": "token has been revoked"})


@pytest.mark.parametrize("path", [EXPORT, PERSIST])
def test_athlete_not_connected(client: TestClient, redis_server: FakeServer, path: str) -> None:
    response = request(client, path, bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to fetch Strava token"})


# --- export


def test_export(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    strava.add_activity()
    save_strava_token(redis_server)
    response = client.get(EXPORT, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gpx+xml"
    assert response.headers["content-disposition"] == f'attachment; filename="activity-{ACTIVITY_ID}.gpx"'
    assert b"<name>Morning Skin</name>" in response.content
    # Strava was called as the athlete
    assert strava.requests[0].headers["Authorization"] == "Bearer strava-access"


def test_export_refreshes_expired_strava_token(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    strava.add_activity()
    strava.add("POST", "/oauth/token", {"access_token": "fresh", "refresh_token": "r2", "expires_at": int(time.time()) + 600})
    save_strava_token(redis_server, expires_in=-10)

    assert client.get(EXPORT, headers=bearer(issue_api_token(redis_server))).status_code == 200
    assert strava.last("GET", f"/api/v3/activities/{ACTIVITY_ID}").headers["Authorization"] == "Bearer fresh"
    assert with_store(redis_server, lambda s: s.fetch_token(ATHLETE_ID)) == "fresh"


def test_export_activity_not_found(client: TestClient, redis_server: FakeServer) -> None:
    save_strava_token(redis_server)
    response = client.get("/api/activity/999/export", headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to fetch activity"})


def test_export_stream_failure(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    strava.add_activity(streams=[{"type": "watts", "data": [1], "original_size": 1}])
    save_strava_token(redis_server)
    response = client.get(EXPORT, headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to export activity"})


# --- persist


def test_persist(client: TestClient, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer) -> None:
    strava.add_activity()
    bucket.expect_puts(2)
    save_strava_token(redis_server)
    response = client.post(PERSIST, headers=bearer(issue_api_token(redis_server)))

    assert response.status_code == 200
    assert response.json() == {"ok": True, "activityId": str(ACTIVITY_ID)}
    assert set(bucket.objects) == {
        f"activities/{ATHLETE_ID}/metadata/{ACTIVITY_ID}.json",
        f"activities/{ATHLETE_ID}/tracks/{ACTIVITY_ID}.gpx",
    }


def test_persist_upload_failure(client: TestClient, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer) -> None:
    strava.add_activity()
    bucket.expect_put_error()
    save_strava_token(redis_server)
    response = client.post(PERSIST, headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to persist activity"})
