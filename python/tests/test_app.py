"""App-wide behaviour: health, static pages, error shape, startup."""

import asyncio

import pytest
from litestar.testing import TestClient

from skintrackr.clients.store import Store
from skintrackr.clients.strava import StravaClient
from skintrackr.config import Config
from skintrackr.server.app import DEFAULT_STATIC_DIR, create_app
from skintrackr.server.services import Services
from skintrackr.server.webhooks import establish_subscriptions
from tests.conftest import FakeBucket, FakeStrava, fake_redis, wait_for_background


def test_healthcheck(client: TestClient) -> None:
    response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_index_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.text == (DEFAULT_STATIC_DIR / "index.html").read_text()


def test_static_files(client: TestClient) -> None:
    response = client.get("/static/confirmation.html")
    assert response.status_code == 200
    assert "Your Strava token" in response.text


@pytest.mark.parametrize("path", ["/", "/some/spa/route"])
def test_get_serves_landing_page_uncached(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "no-cache"


def test_unmatched_method_uses_echo_error_shape(client: TestClient) -> None:
    # every GET path falls back to the landing page, so an unknown POST hits the catch-all's 405
    response = client.post("/nope")
    assert response.status_code == 405
    assert response.json() == {"message": "Method Not Allowed"}


def services(config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server) -> Services:
    client: StravaClient = strava.client()
    return Services(
        config=config,
        store=Store(redis=fake_redis(redis_server), secret=config.secret, strava=client),
        strava=client,
        blob_store=bucket.store,
    )


def test_establish_subscriptions_keeps_existing(config, strava: FakeStrava, bucket, redis_server) -> None:
    strava.add("GET", "/api/v3/push_subscriptions", [{"id": 5}])
    asyncio.run(establish_subscriptions(services(config, strava, bucket, redis_server)))
    assert [r.method for r in strava.requests] == ["GET"]


def test_establish_subscriptions_creates_one(config, strava: FakeStrava, bucket, redis_server) -> None:
    strava.add("GET", "/api/v3/push_subscriptions", [])
    strava.add("POST", "/api/v3/push_subscriptions", {"id": 6})
    asyncio.run(establish_subscriptions(services(config, strava, bucket, redis_server)))
    body = strava.last("POST", "/api/v3/push_subscriptions").content.decode()
    assert "callback_url=https%3A%2F%2Fskintrackr.test%2Fsubscriptions%2Fcallback" in body
    assert "verify_token=verify-me" in body


def test_establish_subscriptions_logs_errors(config, strava: FakeStrava, bucket, redis_server) -> None:
    # nothing is served, so listing fails; the error is logged rather than raised
    asyncio.run(establish_subscriptions(services(config, strava, bucket, redis_server)))
    assert len(strava.requests) == 1


def test_subscribes_at_startup(config, strava: FakeStrava, bucket, redis_server) -> None:
    strava.add("GET", "/api/v3/push_subscriptions", [{"id": 5}])
    app = create_app(config, redis=fake_redis(redis_server), http=strava.http(), blob_store=bucket.store)
    with TestClient(app) as client:
        wait_for_background(client)
    assert strava.last("GET", "/api/v3/push_subscriptions")
