import asyncio
from typing import Any

from fakeredis import FakeServer

from skintrackr.clients.blob_store import archive_keys
from skintrackr.clients.store import Store
from skintrackr.config import Config
from skintrackr.models import ImportStatus
from skintrackr.server.ingest import resume_imports, run_import, start_import
from skintrackr.server.services import Services
from tests.conftest import ACTIVITY, ATHLETE_ID, FakeBucket, FakeStrava, fake_redis, save_strava_token, with_store


def activity(activity_id: int, **fields: Any) -> dict[str, Any]:
    return {**ACTIVITY, "id": activity_id, "start_date_local": "2026-01-02T09:00:00Z", **fields}


def make_services(config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer) -> Services:
    client = strava.client()
    return Services(
        config=config,
        store=Store(redis=fake_redis(redis_server), secret=config.secret, strava=client),
        strava=client,
        blob_store=bucket.store,
    )


def import_status(redis_server: FakeServer) -> ImportStatus:
    return with_store(redis_server, lambda s: s.load_import(ATHLETE_ID))


def test_import_indexes_every_summary_then_archives_the_rest(
    config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    save_strava_token(redis_server)
    strava.add_activities([activity(1), activity(2), activity(3)])
    for activity_id in (2, 3):
        strava.add_activity(activity(activity_id))
    # 1 is already archived (e.g. a resumed import): it isn't fetched again
    bucket.put(archive_keys(ATHLETE_ID, 1).activity)
    bucket.put(archive_keys(ATHLETE_ID, 1).streams)

    asyncio.run(run_import(make_services(config, strava, bucket, redis_server), ATHLETE_ID))

    status = import_status(redis_server)
    assert (status.state, status.activities, status.tracks_done, status.tracks_total) == ("done", 3, 3, 3)
    assert {a.id for a in with_store(redis_server, lambda s: s.activities(ATHLETE_ID))} == {1, 2, 3}
    assert archive_keys(ATHLETE_ID, 3).streams in bucket.keys()
    fetched = {r.url.path for r in strava.requests}
    assert "/api/v3/activities/1" not in fetched and "/api/v3/activities/2" in fetched


def test_activity_deleted_during_import_is_dropped(
    config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    save_strava_token(redis_server)
    strava.add_activities([activity(1), activity(2)])
    strava.add_activity(activity(2))  # 1 is gone: Strava answers 404

    asyncio.run(run_import(make_services(config, strava, bucket, redis_server), ATHLETE_ID))

    status = import_status(redis_server)
    assert (status.state, status.activities, status.tracks_done, status.tracks_total) == ("done", 1, 1, 1)
    assert [a.id for a in with_store(redis_server, lambda s: s.activities(ATHLETE_ID))] == [2]


def test_failed_import_records_the_error(
    config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    save_strava_token(redis_server)
    strava.add_activities([activity(1)])
    strava.add("GET", "/api/v3/activities/1", {"message": "boom"}, status=500)

    asyncio.run(run_import(make_services(config, strava, bucket, redis_server), ATHLETE_ID))

    status = import_status(redis_server)
    assert (status.state, status.activities, status.tracks_done) == ("failed", 1, 0)
    assert "500" in status.error
    assert with_store(redis_server, lambda s: s.running_imports()) == []


def test_import_without_strava_token_fails(
    config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    asyncio.run(run_import(make_services(config, strava, bucket, redis_server), ATHLETE_ID))
    assert import_status(redis_server).state == "failed"
    assert strava.requests == []


def test_start_import_runs_one_at_a_time_and_resumes(
    config: Config, strava: FakeStrava, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    save_strava_token(redis_server)
    strava.add_activities([])

    async def run() -> list[bool]:
        services = make_services(config, strava, bucket, redis_server)
        started = [start_import(services, ATHLETE_ID), start_import(services, ATHLETE_ID)]
        await asyncio.gather(*services.background)
        # an import left running when the server stopped is restarted
        await services.store.save_import(ATHLETE_ID, ImportStatus(state="running"))
        await resume_imports(services)
        started.append(services.is_running(f"import-{ATHLETE_ID}"))
        await asyncio.gather(*services.background)
        return started

    assert asyncio.run(run()) == [True, False, True]
    assert import_status(redis_server).state == "done"
