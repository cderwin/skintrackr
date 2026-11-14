from urllib.parse import parse_qs, urlsplit

from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.models import TokenAthlete, TokenResponse
from skintrackr.server.oauth2 import confirmation_url
from tests.conftest import ATHLETE_ID, FakeStrava, with_store

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


def test_callback_saves_token_and_shows_it(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    strava.add("POST", "/oauth/token", TOKEN_RESPONSE)
    response = client.get("/oauth2/callback", params={"code": "the-code"}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == (
        f"/static/confirmation.html#access_token=access&athlete_id={ATHLETE_ID}&expires_at=1900000000"
    )
    assert "code=the-code" in strava.requests[0].content.decode()
    stored = with_store(redis_server, lambda s: s.fetch_token_info(ATHLETE_ID))
    assert (stored.access_token, stored.refresh_token, stored.expires_at) == ("access", "refresh", 1_900_000_000)


def test_callback_without_code(client: TestClient) -> None:
    response = client.get("/oauth2/callback")
    assert response.status_code == 400
    assert response.json() == {"message": "No code in callback"}


def test_callback_exchange_failure(client: TestClient, strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", {"message": "Bad Request"}, status=400)
    response = client.get("/oauth2/callback", params={"code": "bad"})
    assert response.status_code == 500
    assert response.json() == {"message": "Failed to exchange temporary code with strava"}


def test_confirmation_url_escapes_values() -> None:
    token = TokenResponse(
        access_token="a b&c", refresh_token="r", expires_at=5, athlete=TokenAthlete(id=7, username="x")
    )
    assert confirmation_url(token) == "/static/confirmation.html#access_token=a+b%26c&athlete_id=7&expires_at=5"
