import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.crypto import generate_jwt, verify_jwt
from tests.conftest import ATHLETE_ID, SECRET, FakeStrava, bearer, issue_api_token, save_strava_token, with_store
from tests.test_oauth2 import TOKEN_RESPONSE


def start_flow(client: TestClient) -> str:
    """Start the token OAuth flow; returns the state Strava would send back."""
    response = client.get("/token/new", follow_redirects=False)
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["redirect_uri"] == ["https://skintrackr.test/token/callback"]
    return params["state"][0]


# --- /token/new and /token/callback


def test_token_flow_issues_jwt(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    strava.add("POST", "/oauth/token", TOKEN_RESPONSE)
    state = start_flow(client)
    response = client.get("/token/callback", params={"code": "the-code", "state": state})

    assert response.status_code == 200
    body = response.json()
    assert (body["token_type"], body["athlete_id"]) == ("Bearer", ATHLETE_ID)
    claims = verify_jwt(body["access_token"], SECRET)
    assert claims.athlete_id == ATHLETE_ID
    assert claims.expires_at - claims.issued_at == 30 * 24 * 3600
    # the Strava token was saved, and the JWT recorded for revocation
    assert with_store(redis_server, lambda s: s.fetch_token(ATHLETE_ID)) == "access"
    assert with_store(redis_server, lambda s: s.redis.exists(f"jwt:jti:{claims.jti}")) == 1

    # the state is single use
    replay = client.get("/token/callback", params={"code": "the-code", "state": state})
    assert replay.status_code == 403


@pytest.mark.parametrize(
    ("params", "message"),
    [({"state": "s"}, "No code in callback"), ({"code": "c"}, "No state in callback")],
)
def test_token_callback_requires_code_and_state(client: TestClient, params: dict[str, str], message: str) -> None:
    response = client.get("/token/callback", params=params)
    assert response.status_code == 400
    assert response.json() == {"message": message}


def test_token_callback_rejects_unknown_state(client: TestClient, strava: FakeStrava) -> None:
    response = client.get("/token/callback", params={"code": "c", "state": "forged"})
    assert response.status_code == 403
    assert response.json() == {"message": "Invalid or expired state token"}
    assert strava.requests == []


def test_token_callback_exchange_failure(client: TestClient, strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", {}, status=401)
    response = client.get("/token/callback", params={"code": "c", "state": start_flow(client)})
    assert response.status_code == 500


# --- /token/verify


def test_verify(client: TestClient, redis_server: FakeServer) -> None:
    token = issue_api_token(redis_server)
    response = client.post("/token/verify", headers=bearer(token))
    assert response.status_code == 200
    body = response.json()
    claims = verify_jwt(token, SECRET)
    assert body == {
        "valid": True,
        "athlete_id": ATHLETE_ID,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
        "jti": claims.jti,
    }


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({}, "Authorization header required"),
        ({"Authorization": "Basic abc"}, "Invalid authorization format"),
        ({"Authorization": "Bearer "}, "Invalid authorization format"),
        ({"Authorization": "Bearer not-a-jwt"}, "Invalid or expired token"),
    ],
)
def test_verify_rejects_bad_tokens_with_401(client: TestClient, headers: dict[str, str], message: str) -> None:
    response = client.post("/token/verify", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"message": message}


def test_verify_rejects_expired_token(client: TestClient) -> None:
    token, _ = generate_jwt(ATHLETE_ID, SECRET, timedelta(hours=1), now=time.time() - 7200)
    assert client.post("/token/verify", headers=bearer(token)).status_code == 401


def test_verify_rejects_token_signed_with_another_secret(client: TestClient) -> None:
    token, _ = generate_jwt(ATHLETE_ID, "f" * 64, timedelta(hours=1))
    assert client.post("/token/verify", headers=bearer(token)).status_code == 401


# --- /token/revoke


def test_revoke(client: TestClient, redis_server: FakeServer) -> None:
    token = issue_api_token(redis_server)
    claims = verify_jwt(token, SECRET)

    response = client.post("/token/revoke", headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == {"revoked": True, "jti": claims.jti, "athlete_id": ATHLETE_ID}

    # the token no longer works, and can't be revoked twice
    verify = client.post("/token/verify", headers=bearer(token))
    assert (verify.status_code, verify.json()) == (401, {"message": "token has been revoked"})
    again = client.post("/token/revoke", headers=bearer(token))
    assert (again.status_code, again.json()) == (400, {"message": "Token is already revoked"})


def test_revoke_requires_token(client: TestClient) -> None:
    assert client.post("/token/revoke").status_code == 401
    assert client.post("/token/revoke", headers=bearer("garbage")).status_code == 401


def test_revoke_unrecorded_token(client: TestClient) -> None:
    token, _ = generate_jwt(ATHLETE_ID, SECRET, timedelta(hours=1))
    response = client.post("/token/revoke", headers=bearer(token))
    assert (response.status_code, response.json()) == (500, {"message": "Failed to revoke token"})


# --- /api/strava-token


def test_strava_token(client: TestClient, redis_server: FakeServer) -> None:
    stored = save_strava_token(redis_server)
    response = client.get("/api/strava-token", headers=bearer(issue_api_token(redis_server)))
    assert response.status_code == 200
    assert response.json() == {
        "access_token": stored.access_token,
        "refresh_token": stored.refresh_token,
        "expires_at": stored.expires_at,
    }


def test_strava_token_requires_auth(client: TestClient) -> None:
    assert client.get("/api/strava-token").status_code == 401


def test_strava_token_not_connected(client: TestClient, redis_server: FakeServer) -> None:
    response = client.get("/api/strava-token", headers=bearer(issue_api_token(redis_server)))
    assert (response.status_code, response.json()) == (500, {"message": "failed to fetch Strava token"})
