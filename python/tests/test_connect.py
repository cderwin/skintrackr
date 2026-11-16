from typing import Any

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.clients.blob_store import archive_keys
from skintrackr.crypto import verify_jwt
from skintrackr.models import ActivitySummary
from skintrackr.server.auth import SESSION_COOKIE
from skintrackr.server.connect import INVALID_KEY_MESSAGE
from tests.conftest import (
    API_KEY,
    ATHLETE_ID,
    SECRET,
    FakeBucket,
    FakeIntervals,
    basic_auth_key,
    bearer,
    connect,
    issue_api_token,
    session,
    with_store,
)


def test_connect_saves_key_and_signs_in(client: TestClient, intervals: FakeIntervals, redis_server: FakeServer) -> None:
    intervals.add_athlete()
    response = client.post("/auth/connect", json={"api_key": f"  {API_KEY}\n"})

    assert response.status_code == 200
    assert response.json() == {"athlete_id": ATHLETE_ID, "name": "Skinner"}
    assert basic_auth_key(intervals.requests[0]) == API_KEY  # trimmed
    assert with_store(redis_server, lambda s: s.fetch_api_key(ATHLETE_ID)) == API_KEY
    assert with_store(redis_server, lambda s: s.connected_athletes()) == [ATHLETE_ID]

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", f"Max-Age={30 * 24 * 3600}"):
        assert attribute in cookie
    claims = verify_jwt(cookie.split(";")[0].split("=", 1)[1], SECRET)
    assert claims.athlete_id == ATHLETE_ID
    # recorded, so logging out can revoke it
    assert with_store(redis_server, lambda s: s.redis.exists(f"jwt:jti:{claims.jti}")) == 1


@pytest.mark.parametrize("body", [{}, {"api_key": ""}, {"api_key": "   "}, {"api_key": 42}])
def test_connect_requires_a_key(client: TestClient, intervals: FakeIntervals, body: dict[str, Any]) -> None:
    response = client.post("/auth/connect", json=body)
    assert (response.status_code, response.json()) == (400, {"message": "api_key is required"})
    assert intervals.requests == []


def test_connect_rejected_key(client: TestClient, intervals: FakeIntervals, redis_server: FakeServer) -> None:
    intervals.add_athlete()
    response = client.post("/auth/connect", json={"api_key": "wrong"})
    assert (response.status_code, response.json()) == (400, {"message": INVALID_KEY_MESSAGE})
    assert "set-cookie" not in response.headers
    assert with_store(redis_server, lambda s: s.connected_athletes()) == []


def test_connect_intervals_failure(client: TestClient, intervals: FakeIntervals) -> None:
    intervals.add("GET", "/athlete/0", {"error": "down"}, status=503)
    response = client.post("/auth/connect", json={"api_key": API_KEY})
    assert (response.status_code, response.json()) == (500, {"message": "Failed to check the API key with intervals.icu"})


def test_session_cookie_authenticates(client: TestClient, redis_server: FakeServer) -> None:
    response = client.post("/token/verify", headers=session(issue_api_token(redis_server)))
    assert response.status_code == 200
    assert response.json()["athlete_id"] == ATHLETE_ID


def test_bearer_header_takes_precedence_over_cookie(client: TestClient, redis_server: FakeServer) -> None:
    headers = {**session(issue_api_token(redis_server)), "Authorization": "Bearer not-a-jwt"}
    assert client.post("/token/verify", headers=headers).status_code == 401


def test_logout_revokes_session_and_clears_cookie(client: TestClient, redis_server: FakeServer) -> None:
    jwt = issue_api_token(redis_server)
    response = client.post("/auth/logout", headers=session(jwt))
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=\"\";")
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.post("/token/verify", headers=session(jwt)).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Cookie": f"{SESSION_COOKIE}=not-a-jwt"}])
def test_logout_without_valid_session_still_clears_cookie(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/auth/logout", headers=headers)
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_disconnect_deletes_everything_and_signs_out(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    connect(redis_server)
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, [ActivitySummary(id="i1")]))
    bucket.put(archive_keys(ATHLETE_ID, "i1").streams)
    jwt = issue_api_token(redis_server)

    response = client.post("/auth/disconnect", headers=session(jwt))

    assert (response.status_code, response.json()) == (200, {"ok": True})
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert bucket.keys() == set()
    assert with_store(redis_server, lambda s: s.connected_athletes()) == []
    assert with_store(redis_server, lambda s: s.activities(ATHLETE_ID)) == []
    assert client.post("/token/verify", headers=bearer(jwt)).status_code == 401  # revoked


def test_disconnect_requires_auth(client: TestClient) -> None:
    assert client.post("/auth/disconnect").status_code == 401
