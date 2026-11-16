"""Shared fixtures and test doubles: a fake intervals.icu API, fake Redis, in-memory S3 (moto) and an app test client."""

import asyncio
import base64
import gzip
import json
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from fakeredis import FakeAsyncRedis, FakeServer
from litestar.testing import TestClient
from moto import mock_aws

from skintrackr.clients.blob_store import BlobStore
from skintrackr.clients.intervals import IntervalsClient, RateLimiter
from skintrackr.clients.store import Store
from skintrackr.config import BlobStorageConfig, Config
from skintrackr.crypto import generate_jwt
from skintrackr.server.app import create_app
from skintrackr.server.auth import SESSION_COOKIE
from skintrackr.server.services import Services

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ATHLETE_ID = "i4242"
ACTIVITY_ID = "i1001"
API_KEY = "athlete-api-key"
START_DATE = "2024-01-15T08:30:00Z"

ACTIVITY = {
    "id": ACTIVITY_ID,
    "icu_athlete_id": ATHLETE_ID,
    "name": "Morning Skin",
    "type": "BackcountrySki",
    "start_date": START_DATE,
    "start_date_local": "2024-01-15T00:30:00",
    "distance": 6.5,
    "moving_time": 5,
    "elapsed_time": 5,
    "total_elevation_gain": 0.8,
    "description": None,
    "source": "SUUNTO",
    "icu_sync_date": "2024-01-15T09:00:00.000+00:00",
    "icu_training_load": 12,
}

STREAMS = [
    {"type": "time", "name": None, "data": [0, 5], "valueType": "java.lang.Integer"},
    {"type": "latlng", "name": None, "data": [47.6062, 47.60625], "data2": [-122.3321, -122.33215]},
    {"type": "altitude", "name": None, "data": [56.2, 57.0]},
    {"type": "distance", "name": None, "data": [0.0, 6.5]},
]

API = "/api/v1"
Route = Callable[[httpx.Request], httpx.Response]


def summary(activity_id: str, day: str, vert: float = 1.0, type: str = "BackcountrySki", **fields: Any) -> dict[str, Any]:
    """An activity as intervals.icu lists it, starting at 9am local time on `day` (YYYY-MM-DD)."""
    return {
        **{key: ACTIVITY[key] for key in ("name", "distance", "moving_time", "source", "icu_sync_date")},
        "id": activity_id,
        "type": type,
        "start_date_local": f"{day}T09:00:00",
        "total_elevation_gain": vert,
        **fields,
    }


def basic_auth_key(request: httpx.Request) -> str | None:
    """The API key a request sends (HTTP Basic, username `API_KEY`)."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    username, _, password = base64.b64decode(header.removeprefix("Basic ")).decode().partition(":")
    return password if username == "API_KEY" else None


class FakeIntervals:
    """A fake intervals.icu API: canned responses by method and path, and a record of every request.

    Requests with an API key other than `keys` get 401, like intervals.icu.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Route] = {}
        self.requests: list[httpx.Request] = []
        self.keys = {API_KEY}

    def add(self, method: str, path: str, body: Any = None, status: int = 200) -> None:
        """Answer `method path` (under /api/v1) with `body` as JSON."""
        self.routes[(method, f"{API}{path}")] = lambda _: httpx.Response(status, json=body)

    def add_athlete(self, athlete_id: str = ATHLETE_ID, name: str = "Skinner") -> None:
        """Serve the API key's own athlete."""
        self.add("GET", "/athlete/0", {"id": athlete_id, "name": name, "icu_api_key": "not-ours"})

    def add_activity(self, activity: dict[str, Any] = ACTIVITY, streams: list[dict[str, Any]] = STREAMS) -> None:
        """Serve an activity and its streams."""
        self.add("GET", f"/activity/{activity['id']}", activity)
        self.add("GET", f"/activity/{activity['id']}/streams.json", streams)

    def add_activities(self, activities: list[dict[str, Any]], athlete_id: str = ATHLETE_ID) -> None:
        """Serve the athlete's activity list, filtered by `oldest` (a local date), newest first."""

        def route(request: httpx.Request) -> httpx.Response:
            oldest = request.url.params["oldest"]
            matching = [a for a in activities if a["start_date_local"][:10] >= oldest]
            return httpx.Response(200, json=sorted(matching, key=lambda a: a["start_date_local"], reverse=True))

        self.routes[("GET", f"{API}/athlete/{athlete_id}/activities")] = route

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if basic_auth_key(request) not in self.keys:
            return httpx.Response(401, json={"status": 401, "error": "Unauthorized"})
        route = self.routes.get((request.method, request.url.path))
        return route(request) if route else httpx.Response(404, json={"status": 404, "error": "Not found"})

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def client(self, api_key: str = API_KEY) -> IntervalsClient:
        """An IntervalsClient sending requests to this fake."""
        return IntervalsClient(http=self.http(), api_key=api_key, limiter=RateLimiter(spacing=0))

    def last(self, method: str, path: str) -> httpx.Request:
        """The last request to `method path` (under /api/v1)."""
        return next(r for r in reversed(self.requests) if r.method == method and r.url.path == f"{API}{path}")

    def paths(self) -> list[str]:
        """Every requested path, under /api/v1."""
        return [r.url.path.removeprefix(API) for r in self.requests]


class FakeBucket:
    """An in-memory S3 bucket (moto), with a BlobStore on it."""

    NAME = "skintrackr-test"

    def __init__(self) -> None:
        self.s3 = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
        self.s3.create_bucket(Bucket=self.NAME)
        self.store = BlobStore(s3=self.s3, bucket=self.NAME)

    def keys(self) -> set[str]:
        pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.NAME)
        return {obj["Key"] for page in pages for obj in page.get("Contents", [])}

    def read(self, key: str) -> bytes:
        """An object's contents (decompressed if gzipped)."""
        body = self.s3.get_object(Bucket=self.NAME, Key=key)["Body"].read()
        return gzip.decompress(body) if key.endswith(".gz") else body

    def json(self, key: str) -> Any:
        return json.loads(self.read(key))

    def put(self, key: str, body: bytes = b"{}") -> None:
        self.s3.put_object(Bucket=self.NAME, Key=key, Body=body)

    def put_json(self, key: str, value: Any) -> None:
        """Write `value` as gzipped JSON, as the archive does."""
        self.put(key, gzip.compress(json.dumps(value).encode()))

    def fail_writes(self) -> None:
        """Make every later PutObject fail."""

        def fail(**_: Any) -> None:
            raise ClientError({"Error": {"Code": "InternalError", "Message": "injected"}}, "PutObject")

        self.s3.meta.events.register("before-call.s3.PutObject", fail)


@pytest.fixture
def config() -> Config:
    return Config(
        blob_storage=BlobStorageConfig(access_key_id="test", secret_access_key="test"),
        redis_url="redis://unused",
        secret=SECRET,
        background_spacing=0,
        tracks_debounce=timedelta(0),
    )


@pytest.fixture
def intervals() -> FakeIntervals:
    return FakeIntervals()


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBucket]:
    # boto3 honours endpoint overrides from the environment, which would send these requests past moto
    for name in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    with mock_aws():
        yield FakeBucket()


@pytest.fixture
def redis_server() -> FakeServer:
    """One fake Redis server; every client made from it sees the same data."""
    return FakeServer()


def fake_redis(server: FakeServer) -> FakeAsyncRedis:
    return FakeAsyncRedis(server=server, decode_responses=True)


@pytest.fixture
def store(redis_server: FakeServer) -> Store:
    return Store(redis=fake_redis(redis_server), secret=SECRET)


def with_store[T](server: FakeServer, action: Callable[[Store], Awaitable[T]]) -> T:
    """Run `action` against a fresh Store on `server`, in its own event loop.

    Lets tests read and seed the data an app (running in the test client's loop) uses.
    """

    async def run() -> T:
        redis = fake_redis(server)
        try:
            return await action(Store(redis=redis, secret=SECRET))
        finally:
            await redis.aclose()

    return asyncio.run(run())


def make_services(config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer) -> Services:
    """Services wired to the fakes, outside an app."""
    return Services(
        config=config,
        store=Store(redis=fake_redis(redis_server), secret=config.secret),
        intervals=intervals.client(api_key=""),
        blob_store=bucket.store,
    )


def connect(server: FakeServer, athlete_id: str = ATHLETE_ID, api_key: str = API_KEY) -> None:
    """Store an athlete's API key, as connecting does."""
    with_store(server, lambda s: s.save_api_key(athlete_id, api_key))


def issue_api_token(server: FakeServer, athlete_id: str = ATHLETE_ID) -> str:
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
    config: Config, redis_server: FakeServer, intervals: FakeIntervals, bucket: FakeBucket, frontend_dir: Path
) -> Iterator[TestClient]:
    """A test client for an app wired to the fakes (no sync loop or resumed imports at startup)."""
    app = create_app(
        config,
        redis=fake_redis(redis_server),
        http=intervals.http(),
        blob_store=bucket.store,
        frontend_dir=frontend_dir,
        startup_tasks=False,
    )
    with TestClient(app) as client:
        yield client


def services_of(client: TestClient) -> Services:
    return client.app.state.services


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Wait until `condition()` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        time.sleep(0.01)


def wait_for_background(client: TestClient, timeout: float = 5.0) -> None:
    """Wait until the app's background tasks have finished."""
    wait_until(lambda: not services_of(client).background, timeout)
