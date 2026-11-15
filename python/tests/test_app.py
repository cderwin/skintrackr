"""App-wide behaviour: health, serving the frontend, error shape, startup."""

import pytest
from fakeredis import FakeServer
from litestar.testing import TestClient

from skintrackr.config import Config
from skintrackr.models import ImportStatus
from skintrackr.server.app import create_app
from skintrackr.server.ingest import SYNC_TASK
from tests.conftest import (
    ASSET_PATH,
    ATHLETE_ID,
    INDEX_HTML,
    FakeBucket,
    FakeIntervals,
    connect,
    fake_redis,
    services_of,
    wait_until,
    with_store,
)


def test_healthcheck(client: TestClient) -> None:
    response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("path", ["/", "/connected", "/some/spa/route", "/assets-like", "/../secret.txt"])
def test_other_paths_get_the_spa_index_uncached(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX_HTML
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "no-cache"


def test_serves_files_from_the_build(client: TestClient) -> None:
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-disposition"].startswith("inline")


def test_assets_are_cached_for_good(client: TestClient) -> None:
    response = client.get(f"/{ASSET_PATH}")
    assert response.status_code == 200
    assert response.text == "console.log('app')"
    assert "immutable" in response.headers["cache-control"]


def test_missing_asset_is_404_not_the_index(client: TestClient) -> None:
    assert client.get("/assets/gone-123.js").status_code == 404


def test_unmatched_method_uses_the_error_shape(client: TestClient) -> None:
    # every GET path falls back to the SPA index, so an unknown POST hits the catch-all's 405
    response = client.post("/nope")
    assert response.status_code == 405
    assert response.json() == {"message": "Method Not Allowed"}


def test_syncs_and_resumes_running_imports_at_startup(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    connect(redis_server)
    with_store(redis_server, lambda s: s.save_import(ATHLETE_ID, ImportStatus(state="running")))
    intervals.add_activities([])
    app = create_app(config, redis=fake_redis(redis_server), http=intervals.http(), blob_store=bucket.store)
    with TestClient(app) as client:
        wait_until(lambda: with_store(redis_server, lambda s: s.load_import(ATHLETE_ID)).state == "done")
        # the sync loop keeps running until shutdown
        assert services_of(client).is_running(SYNC_TASK)
    oldest = [r.url.params["oldest"] for r in intervals.requests]
    assert "1970-01-01" in oldest  # the import
    assert any(day != "1970-01-01" for day in oldest)  # the sync
