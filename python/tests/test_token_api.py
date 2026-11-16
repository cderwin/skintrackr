import time
from datetime import timedelta

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.crypto import generate_jwt, verify_jwt
from skintrackr.server.connect import INVALID_KEY_MESSAGE
from tests.conftest import API_KEY, ATHLETE_ID, SECRET, FakeIntervals, bearer, issue_api_token, with_store

# --- /token/new


def test_token_new_issues_jwt(client: TestClient, intervals: FakeIntervals, redis_server: FakeServer) -> None:
    intervals.add_athlete()
    response = client.post("/token/new", json={"api_key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert (body["token_type"], body["athlete_id"]) == ("Bearer", ATHLETE_ID)
    claims = verify_jwt(body["access_token"], SECRET)
    assert claims.athlete_id == ATHLETE_ID
    assert claims.expires_at - claims.issued_at == 30 * 24 * 3600
    # the API key was saved, and the JWT recorded for revocation
    assert with_store(redis_server, lambda s: s.fetch_api_key(ATHLETE_ID)) == API_KEY
    assert with_store(redis_server, lambda s: s.redis.exists(f"jwt:jti:{claims.jti}")) == 1
    assert "set-cookie" not in response.headers


def test_token_new_rejected_key(client: TestClient, intervals: FakeIntervals) -> None:
    intervals.add_athlete()
    response = client.post("/token/new", json={"api_key": "wrong"})
    assert (response.status_code, response.json()) == (400, {"message": INVALID_KEY_MESSAGE})


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
