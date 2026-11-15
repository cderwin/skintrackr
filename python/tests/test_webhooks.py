from typing import Any

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.clients.blob_store import archive_keys
from skintrackr.models import ActivitySummary, ImportStatus
from tests.conftest import (
    ACTIVITY_ID,
    ATHLETE_ID,
    FakeBucket,
    FakeStrava,
    save_strava_token,
    wait_for_background,
    with_store,
)

CALLBACK = "/subscriptions/callback"
KEYS = archive_keys(ATHLETE_ID, ACTIVITY_ID)


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


def post(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post(CALLBACK, json=body)
    assert response.status_code == 200
    wait_for_background(client)


def index(redis_server: FakeServer, athlete_id: int = ATHLETE_ID) -> list[ActivitySummary]:
    return with_store(redis_server, lambda s: s.activities(athlete_id))


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
def test_activity_event_archives_and_indexes_activity(
    client: TestClient, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer, aspect_type: str
) -> None:
    strava.add_activity()
    save_strava_token(redis_server)
    post(client, event(aspect_type=aspect_type, updates={"title": "Renamed"}))

    assert bucket.keys() == {KEYS.activity, KEYS.streams}
    assert [a.id for a in index(redis_server)] == [ACTIVITY_ID]


def test_delete_event_removes_activity(client: TestClient, bucket: FakeBucket, redis_server: FakeServer) -> None:
    bucket.put(KEYS.activity)
    bucket.put(KEYS.streams)
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, [ActivitySummary(id=ACTIVITY_ID), ActivitySummary(id=2)]))
    post(client, event(aspect_type="delete"))

    assert bucket.keys() == set()
    assert [a.id for a in index(redis_server)] == [2]


@pytest.mark.parametrize("authorized", ["false", False])
def test_deauthorization_deletes_everything(
    client: TestClient, bucket: FakeBucket, redis_server: FakeServer, authorized: Any
) -> None:
    save_strava_token(redis_server)
    bucket.put(KEYS.streams)
    bucket.put(f"activities/{ATHLETE_ID}/tracks/1.gpx")
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, [ActivitySummary(id=ACTIVITY_ID)]))
    with_store(redis_server, lambda s: s.save_import(ATHLETE_ID, ImportStatus(state="done")))

    post(client, event(object_type="athlete", object_id=ATHLETE_ID, aspect_type="update", updates={"authorized": authorized}))

    assert bucket.keys() == set()
    assert index(redis_server) == []
    assert with_store(redis_server, lambda s: s.redis.keys("athlete:*")) == []


@pytest.mark.parametrize(
    "body",
    [
        event(object_type="athlete", aspect_type="update", updates={"name": "New Name"}),
        event(object_type="club"),
    ],
)
def test_other_events_are_acknowledged_only(
    client: TestClient, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer, body: dict[str, Any]
) -> None:
    save_strava_token(redis_server)
    post(client, body)
    assert strava.requests == []
    assert bucket.keys() == set()


def test_undecodable_event_is_acknowledged(client: TestClient) -> None:
    response = client.post(CALLBACK, content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 200


def test_failure_is_logged_not_raised(client: TestClient, strava: FakeStrava, bucket: FakeBucket) -> None:
    # no token for the owner: the background task logs the error, and the webhook still succeeds
    post(client, event())
    assert strava.requests == []
    assert bucket.keys() == set()
