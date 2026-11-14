import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from skintrackr.clients.strava import AUTH_URL, SCOPE, StravaClient, StravaError, merge_streams
from skintrackr.models import RawStream, StravaActivity
from tests.conftest import ACTIVITY, ACTIVITY_ID, ATHLETE_ID, FakeStrava


def form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


# --- requests


def test_request_sends_bearer_token(strava: FakeStrava) -> None:
    strava.add("GET", "/api/v3/athlete", {"id": 1})
    assert asyncio.run(strava.client("abc").request("GET", "https://www.strava.com/api/v3/athlete")) == b'{"id":1}'
    assert strava.requests[0].headers["Authorization"] == "Bearer abc"


def test_request_without_token_sends_no_authorization(strava: FakeStrava) -> None:
    strava.add("GET", "/api/v3/athlete", {})
    asyncio.run(strava.client().request("GET", "https://www.strava.com/api/v3/athlete", headers={"X-Test": "1"}))
    assert "Authorization" not in strava.requests[0].headers
    assert strava.requests[0].headers["X-Test"] == "1"


def test_request_sends_form(strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", {})
    asyncio.run(strava.client().request("POST", "https://www.strava.com/oauth/token", form={"a": "1", "b": "x y"}))
    request = strava.requests[0]
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert form(request) == {"a": "1", "b": "x y"}


@pytest.mark.parametrize("status", [301, 400, 401, 404, 429, 500])
def test_request_raises_on_bad_status(strava: FakeStrava, status: int) -> None:
    strava.add("GET", "/api/v3/athlete", {}, status=status)
    with pytest.raises(StravaError, match=f"invalid status {status}") as error:
        asyncio.run(strava.client().request("GET", "https://www.strava.com/api/v3/athlete"))
    assert error.value.status_code == status


def test_request_raises_on_network_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = StravaClient(http=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(StravaError, match="unknown error"):
        asyncio.run(client.request("GET", "https://www.strava.com/api/v3/athlete"))


def test_request_saves_bodies_in_debug_mode(strava: FakeStrava, tmp_path: Path) -> None:
    strava.add("GET", "/api/v3/athlete", {"id": 1})
    client = StravaClient(http=strava.http(), debug_dir=tmp_path)
    asyncio.run(client.request("GET", "https://www.strava.com/api/v3/athlete"))
    (saved,) = tmp_path.glob(".request-debug-*.log")
    assert saved.read_bytes() == b'{"id":1}'


def test_with_token_keeps_the_rest(strava: FakeStrava) -> None:
    client = strava.client()
    derived = client.with_token("t")
    assert (derived.token, derived.http, derived.client_id) == ("t", client.http, client.client_id)
    assert client.token == ""


# --- OAuth and subscriptions


@pytest.mark.parametrize("state", [None, "s1"])
def test_authorization_url(strava: FakeStrava, state: str | None) -> None:
    url = strava.client().authorization_url("https://skintrackr.test/oauth2/callback", state=state)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTH_URL
    expected = {
        "client_id": ["client-id"],
        "redirect_uri": ["https://skintrackr.test/oauth2/callback"],
        "response_type": ["code"],
        "scope": [SCOPE],
    }
    if state:
        expected["state"] = [state]
    assert parse_qs(parts.query) == expected


TOKEN_RESPONSE = {
    "token_type": "Bearer",
    "expires_at": 1_700_000_000,
    "expires_in": 21600,
    "refresh_token": "refresh",
    "access_token": "access",
    "athlete": {"id": ATHLETE_ID, "username": "skinner", "firstname": "S"},
}


def test_exchange_code(strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", TOKEN_RESPONSE)
    token = asyncio.run(strava.client().exchange_code("the-code"))
    assert (token.access_token, token.refresh_token, token.expires_at) == ("access", "refresh", 1_700_000_000)
    assert (token.athlete.id, token.athlete.username, token.token_type, token.expires_in) == (
        ATHLETE_ID,
        "skinner",
        "Bearer",
        21600,
    )
    assert form(strava.requests[0]) == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "code": "the-code",
        "grant_type": "authorization_code",
    }


def test_refresh_token(strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", {**TOKEN_RESPONSE, "athlete": None})
    token = asyncio.run(strava.client().refresh_token("old-refresh"))
    assert token.token_info().access_token == "access"
    assert form(strava.requests[0])["grant_type"] == "refresh_token"
    assert form(strava.requests[0])["refresh_token"] == "old-refresh"


def test_exchange_code_rejects_incomplete_response(strava: FakeStrava) -> None:
    strava.add("POST", "/oauth/token", {"athlete": {"id": 1}})
    with pytest.raises(StravaError, match="error decoding response"):
        asyncio.run(strava.client().exchange_code("code"))


def test_subscriptions(strava: FakeStrava) -> None:
    strava.add("GET", "/api/v3/push_subscriptions", [{"id": 5, "callback_url": "x"}])
    strava.add("POST", "/api/v3/push_subscriptions", {"id": 6})
    client = strava.client()
    assert [s.id for s in asyncio.run(client.list_subscriptions())] == [5]
    assert dict(strava.requests[0].url.params) == {"client_id": "client-id", "client_secret": "client-secret"}
    assert asyncio.run(client.create_subscription("https://x/subscriptions/callback", "verify")).id == 6
    assert form(strava.requests[1]) == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "callback_url": "https://x/subscriptions/callback",
        "verify_token": "verify",
    }


# --- activities


def test_get_activity(strava: FakeStrava) -> None:
    strava.add_activity()
    activity = asyncio.run(strava.client("t").get_activity(str(ACTIVITY_ID)))
    assert activity.id == ACTIVITY_ID
    assert activity.athlete.id == ATHLETE_ID
    assert activity.name == "Morning Skin"
    assert activity.description == ""  # null in the response
    assert activity.start_latlng == (47.6062, -122.3321)


def test_get_activity_without_gps(strava: FakeStrava) -> None:
    strava.add_activity({**ACTIVITY, "start_latlng": [], "end_latlng": []})
    activity = asyncio.run(strava.client("t").get_activity(str(ACTIVITY_ID)))
    assert activity.start_latlng == activity.end_latlng == (0.0, 0.0)


def test_get_activity_not_found(strava: FakeStrava) -> None:
    with pytest.raises(StravaError, match="invalid status 404"):
        asyncio.run(strava.client("t").get_activity("999"))


def test_get_activity_stream_requests_keys(strava: FakeStrava) -> None:
    strava.add_activity()
    points = asyncio.run(strava.client("t").get_activity_stream(str(ACTIVITY_ID)))
    assert dict(strava.requests[0].url.params) == {"keys": "latlng,altitude,time"}
    assert [(p.time, p.latitude, p.longitude, p.altitude, p.distance) for p in points] == [
        (0, 47.6062, -122.3321, 56.2, 0),
        (5, 47.60625, -122.33215, 57.0, 6.5),
    ]


def streams(*items: dict) -> list[RawStream]:
    return [RawStream(type=i["type"], data=i["data"], original_size=i["original_size"]) for i in items]


def test_merge_streams_all_types() -> None:
    points = merge_streams(
        streams(
            {"type": "heartrate", "data": [120], "original_size": 1},
            {"type": "temp", "data": [-4], "original_size": 1},
        )
    )
    assert (points[0].heart_rate, points[0].temperature) == (120, -4)


def test_merge_streams_empty() -> None:
    assert merge_streams([]) == []


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (
            [{"type": "time", "data": [0, 1], "original_size": 2}, {"type": "altitude", "data": [1], "original_size": 1}],
            "does not match size of first stream",
        ),
        (
            [{"type": "time", "data": [0, 1], "original_size": 2}, {"type": "altitude", "data": [1, 2], "original_size": 3}],
            "does not match original_size",
        ),
        ([{"type": "watts", "data": [100], "original_size": 1}], "unrecognized stream type: watts"),
        ([{"type": "latlng", "data": [[1.0]], "original_size": 1}], 'error decoding stream "latlng"'),
        ([{"type": "time", "data": ["x"], "original_size": 1}], 'error decoding stream "time"'),
    ],
)
def test_merge_streams_rejects_malformed_streams(items: list[dict], message: str) -> None:
    with pytest.raises(StravaError, match=message):
        merge_streams(streams(*items))


def test_export_activity_gpx(strava: FakeStrava) -> None:
    strava.add_activity()
    client = strava.client("t")
    document = asyncio.run(client.export_activity_gpx(asyncio.run(client.get_activity(str(ACTIVITY_ID)))))
    assert document.startswith(b"<?xml")
    assert b"<name>Morning Skin</name>" in document
    assert b"<time>2024-01-15T08:30:05Z</time>" in document
    assert b"gpxtpx:hr" not in document


def test_export_activity_gpx_rejects_invalid_start_date(strava: FakeStrava) -> None:
    with pytest.raises(StravaError, match="error parsing activity start date"):
        asyncio.run(strava.client("t").export_activity_gpx(StravaActivity(id=1, start_date="yesterday")))
    assert strava.requests == []


def test_export_activity_gpx_stream_error(strava: FakeStrava) -> None:
    activity = StravaActivity(id=ACTIVITY_ID, start_date="2024-01-15T08:30:00Z")
    with pytest.raises(StravaError, match="invalid status 404"):
        asyncio.run(strava.client("t").export_activity_gpx(activity))

