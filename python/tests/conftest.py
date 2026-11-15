"""Shared fixtures and test doubles: a fake Strava API, fake Redis, stubbed S3 and an app test client."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from botocore.stub import ANY, Stubber
from fakeredis import FakeAsyncRedis, FakeServer
from litestar.testing import TestClient

from skintrackr.clients.blob_store import BlobStore
from skintrackr.clients.store import Store
from skintrackr.clients.strava import StravaClient
from skintrackr.config import BlobStorageConfig, Config
from skintrackr.crypto import generate_jwt
from skintrackr.models import TokenInfo
from skintrackr.server.app import create_app
from skintrackr.server.auth import SESSION_COOKIE
from skintrackr.server.services import Services

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ATHLETE_ID = 4242
ACTIVITY_ID = 1001
START_DATE = "2024-01-15T08:30:00Z"

ACTIVITY = {
    "id": ACTIVITY_ID,
    "athlete": {"id": ATHLETE_ID, "resource_state": 1},
    "name": "Morning Skin",
    "distance": 6.5,
    "moving_time": 5,
    "elapsed_time": 5,
    "total_elevation_gain": 0.8,
    "type": "BackcountrySki",
    "start_date": START_DATE,
    "start_latlng": [47.6062, -122.3321],
    "end_latlng": [47.60625, -122.33215],
    "description": None,
    "suffer_score": 3.0,
}

STREAMS = [
    {"type": "latlng", "data": [[47.6062, -122.3321], [47.60625, -122.33215]], "original_size": 2},
    {"type": "altitude", "data": [56.2, 57.0], "original_size": 2},
    {"type": "time", "data": [0, 5], "original_size": 2},
    {"type": "distance", "data": [0, 6.5], "original_size": 2},
]

Route = Callable[[httpx.Request], httpx.Response]


def _unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def summary(activity_id: int, day: str, vert: float, sport_type: str = "BackcountrySki") -> dict[str, Any]:
    """An activity as Strava lists it, starting at 9am local time on `day` (YYYY-MM-DD)."""
    return {"id": activity_id, "sport_type": sport_type, "start_date_local": f"{day}T09:00:00Z", "total_elevation_gain": vert}


class FakeStrava:
    """A fake Strava API: canned responses by method and path, and a record of every request."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Route] = {}
        self.requests: list[httpx.Request] = []

    def add(self, method: str, path: str, body: Any = None, status: int = 200) -> None:
        """Answer `method path` with `body` as JSON."""
        self.routes[(method, path)] = lambda _: httpx.Response(status, json=body)

    def add_activity(self, activity: dict[str, Any] = ACTIVITY, streams: list[dict[str, Any]] = STREAMS) -> None:
        """Serve an activity and its streams."""
        self.add("GET", f"/api/v3/activities/{activity['id']}", activity)
        self.add("GET", f"/api/v3/activities/{activity['id']}/streams", streams)

    def add_activities(self, activities: list[dict[str, Any]]) -> None:
        """Serve the athlete's activity list, filtered by `after` and paged like Strava (by `start_date_local`)."""

        def route(request: httpx.Request) -> httpx.Response:
            after = int(request.url.params.get("after", 0))
            matching = [a for a in activities if _unix(a["start_date_local"]) > after]
            per_page, page = int(request.url.params["per_page"]), int(request.url.params["page"])
            return httpx.Response(200, json=matching[(page - 1) * per_page : page * per_page])

        self.routes[("GET", "/api/v3/athlete/activities")] = route

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        return route(request) if route else httpx.Response(404, json={"message": "Record Not Found"})

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def client(self, token: str = "") -> StravaClient:
        """A StravaClient sending requests to this fake."""
        return StravaClient(http=self.http(), token=token, client_id="client-id", client_secret="client-secret")

    def last(self, method: str, path: str) -> httpx.Request:
        """The last request to `method path`."""
        return next(r for r in reversed(self.requests) if r.method == method and r.url.path == path)


class FakeBucket:
    """A real boto3 S3 client with stubbed responses, recording each object written."""

    def __init__(self) -> None:
        self.s3 = boto3.client(
            "s3",
            region_name="auto",
            endpoint_url="https://s3.test",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        self.stubber = Stubber(self.s3)
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.s3.meta.events.register("before-parameter-build.s3.PutObject", self._record)
        self.store = BlobStore(s3=self.s3, bucket="skintrackr-test")

    def _record(self, params: dict[str, Any], **_: Any) -> None:
        self.objects[params["Key"]] = (params["Body"], params["ContentType"])

    def expect_puts(self, count: int) -> None:
        """Accept `count` more PutObject calls."""
        for _ in range(count):
            self.stubber.add_response(
                "put_object", {}, {"Bucket": "skintrackr-test", "Key": ANY, "Body": ANY, "ContentType": ANY}
            )

    def expect_put_error(self) -> None:
        """Fail the next PutObject call."""
        self.stubber.add_client_error("put_object", "InternalError", http_status_code=500)

    def json(self, key: str) -> Any:
        return json.loads(self.objects[key][0])


@pytest.fixture
def config() -> Config:
    return Config(
        blob_storage=BlobStorageConfig(access_key_id="test", secret_access_key="test"),
        strava_client_id="client-id",
        strava_client_secret="client-secret",
        redis_url="redis://unused",
        secret=SECRET,
        base_url="https://skintrackr.test",
        verify_token="verify-me",
    )


@pytest.fixture
def strava() -> FakeStrava:
    return FakeStrava()


@pytest.fixture
def bucket() -> Iterator[FakeBucket]:
    bucket = FakeBucket()
    with bucket.stubber:
        yield bucket


@pytest.fixture
def redis_server() -> FakeServer:
    """One fake Redis server; every client made from it sees the same data."""
    return FakeServer()


def fake_redis(server: FakeServer) -> FakeAsyncRedis:
    return FakeAsyncRedis(server=server, decode_responses=True)


@pytest.fixture
def store(redis_server: FakeServer, strava: FakeStrava) -> Store:
    return Store(redis=fake_redis(redis_server), secret=SECRET, strava=strava.client())


def with_store[T](server: FakeServer, action: Callable[[Store], Awaitable[T]]) -> T:
    """Run `action` against a fresh Store on `server`, in its own event loop.

    Lets tests read and seed the data an app (running in the test client's loop) uses.
    """

    async def run() -> T:
        redis = fake_redis(server)
        try:
            return await action(Store(redis=redis, secret=SECRET, strava=FakeStrava().client()))
        finally:
            await redis.aclose()

    return asyncio.run(run())


def save_strava_token(server: FakeServer, athlete_id: int = ATHLETE_ID, expires_in: int = 3600) -> TokenInfo:
    """Store a Strava token for an athlete."""
    token = TokenInfo(access_token="strava-access", refresh_token="strava-refresh", expires_at=int(time.time()) + expires_in)
    with_store(server, lambda s: s.save_token(athlete_id, token))
    return token


def issue_api_token(server: FakeServer, athlete_id: int = ATHLETE_ID) -> str:
    """Issue (and record) an API token for an athlete; returns the JWT."""
    jwt, claims = generate_jwt(athlete_id, SECRET, timedelta(days=1))
    with_store(server, lambda s: s.save_jwt(claims))
    return jwt


def session(token: str) -> dict[str, str]:
    """Headers sending `token` in the session cookie (the test client is on http, so it won't keep Secure cookies)."""
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


INDEX_HTML = "<!DOCTYPE html><title>skintrackr</title><div id=app></div>"
ASSET_PATH = "assets/index-abc123.js"


@pytest.fixture
def frontend_dir(tmp_path: Path) -> Path:
    """A stand-in for the frontend build (frontend/dist), so the tests don't need one."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML)
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (dist / ASSET_PATH).write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("outside the build")
    return dist


@pytest.fixture
def client(
    config: Config, redis_server: FakeServer, strava: FakeStrava, bucket: FakeBucket, frontend_dir: Path
) -> Iterator[TestClient]:
    """A test client for an app wired to the fakes (no webhook subscription at startup)."""
    app = create_app(
        config,
        redis=fake_redis(redis_server),
        http=strava.http(),
        blob_store=bucket.store,
        frontend_dir=frontend_dir,
        subscribe=False,
    )
    with TestClient(app) as client:
        yield client


def services_of(client: TestClient) -> Services:
    return client.app.state.services


def wait_for_background(client: TestClient, timeout: float = 5.0) -> None:
    """Wait until the app's background tasks have finished."""
    deadline = time.monotonic() + timeout
    while services_of(client).background:
        if time.monotonic() > deadline:
            raise TimeoutError("background tasks still running")
        time.sleep(0.01)

