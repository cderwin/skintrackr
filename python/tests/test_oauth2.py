from urllib.parse import parse_qs, urlsplit

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.crypto import verify_jwt
from skintrackr.server.auth import SESSION_COOKIE
from tests.conftest import ATHLETE_ID, SECRET, FakeStrava, issue_api_token, session, wait_for_background, with_store

TOKEN_RESPONSE = {
    "token_type": "Bearer",
    "expires_at": 1_900_000_000,
    "expires_in": 21600,
    "refresh_token": "refresh",
    "access_token": "access",
    "athlete": {"id": ATHLETE_ID, "username": "skinner"},
}


def test_connect_redirects_to_strava(client: TestClient) -> None:
    response = client.get("/oauth2/connect", follow_redirects=False)
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.netloc == "www.strava.com" and location.path == "/oauth/authorize"
    params = parse_qs(location.query)
    assert params["redirect_uri"] == ["https://skintrackr.test/oauth2/callback"]
    assert params["client_id"] == ["client-id"]
    assert "state" not in params


def test_callback_saves_token_and_signs_in(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    strava.add("POST", "/oauth/token", TOKEN_RESPONSE)
    response = client.get("/oauth2/callback", params={"code": "the-code"}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/connected"
    assert "code=the-code" in strava.requests[0].content.decode()
    stored = with_store(redis_server, lambda s: s.fetch_token_info(ATHLETE_ID))
    assert (stored.access_token, stored.refresh_token, stored.expires_at) == ("access", "refresh", 1_900_000_000)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", f"Max-Age={30 * 24 * 3600}"):
        assert attribute in cookie
    claims = verify_jwt(cookie.split(";")[0].split("=", 1)[1], SECRET)
    assert claims.athlete_id == ATHLETE_ID
    # recorded, so logging out can revoke it
    assert with_store(redis_server, lambda s: s.redis.exists(f"jwt:jti:{claims.jti}")) == 1
    # the app's webhook subscription is checked (it covers every athlete who connects)
    wait_for_background(client)
    assert strava.last("GET", "/api/v3/push_subscriptions")


def test_session_cookie_authenticates(client: TestClient, redis_server: FakeServer) -> None:
    response = client.post("/token/verify", headers=session(issue_api_token(redis_server)))
    assert response.status_code == 200
    assert response.json()["athlete_id"] == ATHLETE_ID


def test_bearer_header_takes_precedence_over_cookie(client: TestClient, redis_server: FakeServer) -> None:
    headers = {**session(issue_api_token(redis_server)), "Authorization": "Bearer not-a-jwt"}
    assert client.post("/token/verify", headers=headers).status_code == 401


def test_logout_revokes_session_and_clears_cookie(client: TestClient, redis_server: FakeServer) -> None:
    jwt = issue_api_token(redis_server)
    response = client.post("/oauth2/logout", headers=session(jwt))
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=\"\";")
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.post("/token/verify", headers=session(jwt)).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Cookie": f"{SESSION_COOKIE}=not-a-jwt"}])
def test_logout_without_valid_session_still_clears_cookie(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/oauth2/logout", headers=headers)
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_callback_without_code(client: TestClient) -> None:
    response = client.get("/oauth2/callback")
    assert response.status_code == 400
    assert response.json() == {"message": "No code in callback"}


def test_callback_exchange_failure(client: TestClient, strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", {"message": "Bad Request"}, status=400)
    response = client.get("/oauth2/callback", params={"code": "bad"})
    assert response.status_code == 500
    assert response.json() == {"message": "Failed to exchange temporary code with strava"}
