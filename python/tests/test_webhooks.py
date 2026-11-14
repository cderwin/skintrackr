from typing import Any

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from tests.conftest import ACTIVITY_ID, ATHLETE_ID, FakeBucket, FakeStrava, save_strava_token, wait_for_background

CALLBACK = "/subscriptions/callback"


def event(object_type: str = "activity", aspect_type: str = "create", **overrides: Any) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "object_id": ACTIVITY_ID,
        "aspect_type": aspect_type,
        "updates": {},
        "owner_id": ATHLETE_ID,
        "subscription_id": 1,
        "event_time": 1_700_000_000,
        **overrides,
    }


# --- subscription verification


def test_subscription_callback_echoes_challenge(client: TestClient) -> None:
    response = client.get(CALLBACK, params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "abc"})
    assert response.status_code == 200
    assert response.json() == {"hub.challenge": "abc"}


@pytest.mark.parametrize("params", [{}, {"hub.verify_token": "wrong", "hub.challenge": "abc"}])
def test_subscription_callback_rejects_wrong_verify_token(client: TestClient, params: dict[str, str]) -> None:
    response = client.get(CALLBACK, params=params)
    assert response.status_code == 400
    assert response.json() == {"message": "hub.verify_token is incorrect"}


# --- push events


@pytest.mark.parametrize("aspect_type", ["create", "update"])
def test_activity_event_persists_activity(
    client: TestClient, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer, aspect_type: str
) -> None:
    strava.add_activity()
    bucket.expect_puts(2)
    save_strava_token(redis_server)

    response = client.post(CALLBACK, json=event(aspect_type=aspect_type, updates={"title": "Renamed"}))
    assert response.status_code == 200
    wait_for_background(client)
    assert set(bucket.objects) == {
        f"activities/{ATHLETE_ID}/metadata/{ACTIVITY_ID}.json",
        f"activities/{ATHLETE_ID}/tracks/{ACTIVITY_ID}.gpx",
    }


@pytest.mark.parametrize(
    "body",
    [
        event(aspect_type="delete"),
        event(object_type="athlete", aspect_type="update", updates={"authorized": "false"}),
        event(object_type="club"),
    ],
)
def test_other_events_are_acknowledged_only(client: TestClient, strava: FakeStrava, body: dict[str, Any]) -> None:
    response = client.post(CALLBACK, json=body)
    assert response.status_code == 200
    wait_for_background(client)
    assert strava.requests == []


def test_undecodable_event_is_acknowledged(client: TestClient) -> None:
    response = client.post(CALLBACK, content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 200


def test_persist_failure_is_logged_not_raised(client: TestClient, strava: FakeStrava, redis_server: FakeServer) -> None:
    # no token for the owner: the background task logs the error, and the webhook still succeeds
    response = client.post(CALLBACK, json=event())
    assert response.status_code == 200
    wait_for_background(client)
    assert strava.requests == []
