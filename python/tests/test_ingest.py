import asyncio
import functools
import json
import logging
from datetime import date
from typing import Any

import pytest
from fakeredis import FakeServer

from skintrackr.clients.blob_store import archive_keys, tracks_key
from skintrackr.config import Config
from skintrackr.models import ActivitySummary, ImportStatus, loads
from skintrackr.server.ingest import (
    RECONNECT_MESSAGE,
    build_tracks,
    remove_athlete,
    resume_imports,
    run_import,
    schedule_tracks,
    start_import,
    sync_all,
    sync_athlete,
)
from skintrackr.tracks import TrackBuildError, map_track_ids
from tests.conftest import (
    ACTIVITY,
    ATHLETE_ID,
    FakeBucket,
    FakeIntervals,
    connect,
    make_services,
    summary,
    with_store,
)

TODAY = date(2026, 11, 14)


def activity(activity_id: str, day: str = "2026-01-02", **fields: Any) -> dict[str, Any]:
    """An activity's full JSON, and its summary as listed (the same fields)."""
    return {**ACTIVITY, **summary(activity_id, day, **fields)}


def import_status(redis_server: FakeServer) -> ImportStatus:
    return with_store(redis_server, lambda s: s.load_import(ATHLETE_ID))


def indexed(redis_server: FakeServer) -> dict[str, ActivitySummary]:
    return {a.id: a for a in with_store(redis_server, lambda s: s.activities(ATHLETE_ID))}


@pytest.fixture
def connected(redis_server: FakeServer) -> None:
    connect(redis_server)


# --- imports


@pytest.mark.usefixtures("connected")
def test_import_indexes_every_summary_then_archives_the_rest(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    intervals.add_activities([activity("i1"), activity("i2"), activity("i3"), activity("i4", source="STRAVA")])
    for activity_id in ("i2", "i3"):
        intervals.add_activity(activity(activity_id))
    # i1 is already archived (e.g. a resumed import): it isn't fetched again
    bucket.put(archive_keys(ATHLETE_ID, "i1").activity)
    bucket.put(archive_keys(ATHLETE_ID, "i1").streams)
    # i9 was deleted on intervals.icu since the last import
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, [ActivitySummary(id="i9")]))
    bucket.put(archive_keys(ATHLETE_ID, "i9").streams)

    asyncio.run(run_import(make_services(config, intervals, bucket, redis_server), ATHLETE_ID))

    status = import_status(redis_server)
    assert (status.state, status.activities, status.tracks_done, status.tracks_total) == ("done", 3, 3, 3)
    assert status.skipped == 1  # the Strava-sourced stub
    assert indexed(redis_server).keys() == {"i1", "i2", "i3"}
    assert archive_keys(ATHLETE_ID, "i3").streams in bucket.keys()
    assert archive_keys(ATHLETE_ID, "i9").streams not in bucket.keys()
    assert "/activity/i1" not in intervals.paths() and "/activity/i2" in intervals.paths()
    assert intervals.last("GET", f"/athlete/{ATHLETE_ID}/activities").url.params["oldest"] == "1970-01-01"


@pytest.mark.usefixtures("connected")
def test_activity_deleted_during_import_is_dropped(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    intervals.add_activities([activity("i1"), activity("i2")])
    intervals.add_activity(activity("i2"))  # i1 is gone: intervals.icu answers 404

    asyncio.run(run_import(make_services(config, intervals, bucket, redis_server), ATHLETE_ID))

    status = import_status(redis_server)
    assert (status.state, status.activities, status.tracks_done, status.tracks_total) == ("done", 1, 1, 1)
    assert indexed(redis_server).keys() == {"i2"}


@pytest.mark.usefixtures("connected")
def test_failed_import_records_the_error(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    intervals.add_activities([activity("i1")])
    intervals.add("GET", "/activity/i1", {"error": "boom"}, status=500)

    asyncio.run(run_import(make_services(config, intervals, bucket, redis_server), ATHLETE_ID))

    status = import_status(redis_server)
    assert (status.state, status.activities, status.tracks_done) == ("failed", 1, 0)
    assert "500" in status.error
    assert with_store(redis_server, lambda s: s.running_imports()) == []


def test_import_with_revoked_key_asks_to_reconnect(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    connect(redis_server, api_key="revoked")
    asyncio.run(run_import(make_services(config, intervals, bucket, redis_server), ATHLETE_ID))
    assert (import_status(redis_server).state, import_status(redis_server).error) == ("failed", RECONNECT_MESSAGE)


def test_import_without_api_key_fails(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    asyncio.run(run_import(make_services(config, intervals, bucket, redis_server), ATHLETE_ID))
    assert import_status(redis_server).state == "failed"
    assert intervals.requests == []


@pytest.mark.usefixtures("connected")
def test_start_import_runs_one_at_a_time_and_resumes(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    intervals.add_activities([])

    async def run() -> list[bool]:
        services = make_services(config, intervals, bucket, redis_server)
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


# --- sync


@pytest.mark.usefixtures("connected")
def test_sync_ingests_new_and_changed_activities_and_removes_deleted_ones(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    unchanged, renamed = activity("i1", "2026-11-01"), activity("i2", "2026-11-02", name="Renamed")
    new, stub = activity("i3", "2026-11-13"), activity("i4", "2026-11-13", source="STRAVA")
    intervals.add_activities([unchanged, renamed, new, stub])
    for item in (renamed, new):
        intervals.add_activity(item)
    old = ActivitySummary(id="i0", start_date_local="2026-10-01T09:00:00")  # before the window: not listed, kept
    deleted = ActivitySummary(id="i5", start_date_local="2026-11-05T09:00:00")
    previous = [old, deleted, *(ActivitySummary(**summary(a, d)) for a, d in [("i1", "2026-11-01"), ("i2", "2026-11-02")])]
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, previous))
    bucket.put(archive_keys(ATHLETE_ID, "i5").streams)

    asyncio.run(sync_athlete(make_services(config, intervals, bucket, redis_server), ATHLETE_ID, today=TODAY))

    assert intervals.last("GET", f"/athlete/{ATHLETE_ID}/activities").url.params["oldest"] == "2026-10-15"
    assert {p for p in intervals.paths() if p.startswith("/activity/")} == {
        "/activity/i2",
        "/activity/i2/streams.json",
        "/activity/i3",
        "/activity/i3/streams.json",
    }
    rows = indexed(redis_server)
    assert rows.keys() == {"i0", "i1", "i2", "i3"}
    assert rows["i2"].name == "Renamed"
    assert archive_keys(ATHLETE_ID, "i5").streams not in bucket.keys()


@pytest.mark.usefixtures("connected")
def test_sync_skips_athletes_with_an_import_running(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    async def run() -> None:
        services = make_services(config, intervals, bucket, redis_server)
        services.spawn(asyncio.sleep(1), name=f"import-{ATHLETE_ID}")
        await sync_athlete(services, ATHLETE_ID, today=TODAY)
        await services.cancel_background()

    asyncio.run(run())
    assert intervals.requests == []


def test_sync_all_carries_on_after_errors(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer
) -> None:
    connect(redis_server, athlete_id="i1", api_key="revoked")
    connect(redis_server)
    intervals.add_activities([])
    asyncio.run(sync_all(make_services(config, intervals, bucket, redis_server)))
    assert [r.url.path for r in intervals.requests] == ["/api/v1/athlete/i1/activities", f"/api/v1/athlete/{ATHLETE_ID}/activities"]


# --- map tracks


class FakeTiles:
    """Stands in for tippecanoe: records each build's features, and returns a fake archive."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.builds: list[list[str]] = []
        self.error: Exception | None = None
        monkeypatch.setattr("skintrackr.server.ingest.build_pmtiles", self.build)

    async def build(self, features: list[bytes]) -> bytes:
        self.builds.append([json.loads(feature)["properties"]["id"] for feature in features])
        if self.error:
            raise self.error
        return b"PMTiles fake"


@pytest.fixture
def tiles(monkeypatch: pytest.MonkeyPatch) -> FakeTiles:
    return FakeTiles(monkeypatch)


def limit_map_tracks(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    monkeypatch.setattr("skintrackr.server.ingest.map_track_ids", functools.partial(map_track_ids, limit=limit))


def seed_tracks(bucket: FakeBucket, redis_server: FakeServer, days: dict[str, str]) -> None:
    """Index activities (id -> local start day) and give each a track."""
    index = [ActivitySummary(id=a, start_date_local=f"{day}T09:00:00") for a, day in days.items()]
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, index))
    for activity_id in days:
        bucket.put_json(archive_keys(ATHLETE_ID, activity_id).track, {"type": "Feature", "properties": {"id": activity_id}})


def test_build_tracks_from_the_most_recent_and_warns_when_limited(
    config: Config,
    intervals: FakeIntervals,
    bucket: FakeBucket,
    redis_server: FakeServer,
    tiles: FakeTiles,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed_tracks(bucket, redis_server, {"i1": "2024-03-01", "i2": "2026-01-05", "i3": "2025-07-04"})
    limit_map_tracks(monkeypatch, 2)
    services = make_services(config, intervals, bucket, redis_server)

    with caplog.at_level(logging.WARNING, logger="skintrackr.server.ingest"):
        tracks = asyncio.run(build_tracks(services.store, services.blob_store, ATHLETE_ID))

    assert (tracks.included, tracks.omitted) == (["i2", "i3"], 1)
    assert tiles.builds == [["i2", "i3"]]
    assert bucket.read(tracks_key(ATHLETE_ID)) == b"PMTiles fake"
    head = bucket.s3.head_object(Bucket=bucket.NAME, Key=tracks_key(ATHLETE_ID))
    assert head["ContentType"] == "application/vnd.pmtiles"
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning.getMessage() == "map tracks limited"
    assert [getattr(warning, k) for k in ("athlete_id", "tracks", "included", "omitted")] == [ATHLETE_ID, 3, 2, 1]


def test_build_tracks_under_the_limit_doesnt_warn(
    config: Config,
    intervals: FakeIntervals,
    bucket: FakeBucket,
    redis_server: FakeServer,
    tiles: FakeTiles,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed_tracks(bucket, redis_server, {"i1": "2024-03-01"})
    services = make_services(config, intervals, bucket, redis_server)
    with caplog.at_level(logging.WARNING, logger="skintrackr.server.ingest"):
        asyncio.run(build_tracks(services.store, services.blob_store, ATHLETE_ID))
    assert tiles.builds == [["i1"]]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_build_tracks_without_tracks_deletes_the_archive(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer, tiles: FakeTiles
) -> None:
    bucket.put(tracks_key(ATHLETE_ID))  # every activity with a track was deleted since
    services = make_services(config, intervals, bucket, redis_server)
    asyncio.run(build_tracks(services.store, services.blob_store, ATHLETE_ID))
    assert tiles.builds == []
    assert tracks_key(ATHLETE_ID) not in bucket.keys()


def test_scheduled_rebuilds_coalesce_and_survive_failures(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer, tiles: FakeTiles
) -> None:
    seed_tracks(bucket, redis_server, {"i1": "2024-03-01"})

    async def run() -> int:
        services = make_services(config, intervals, bucket, redis_server)
        tiles.error = TrackBuildError("boom")
        for _ in range(3):  # changes before the rebuild starts: one rebuild
            schedule_tracks(services, ATHLETE_ID)
        await asyncio.gather(*services.background)
        tiles.error = None
        schedule_tracks(services, ATHLETE_ID)  # a later change still rebuilds
        await asyncio.gather(*services.background)
        return len(services.background)

    assert asyncio.run(run()) == 0
    assert tiles.builds == [["i1"], ["i1"]]
    assert tracks_key(ATHLETE_ID) in bucket.keys()


def test_remove_athlete_cancels_pending_rebuilds(
    config: Config, intervals: FakeIntervals, bucket: FakeBucket, redis_server: FakeServer, tiles: FakeTiles
) -> None:
    async def run() -> None:
        services = make_services(config, intervals, bucket, redis_server)
        schedule_tracks(services, ATHLETE_ID)
        await remove_athlete(services, ATHLETE_ID)
        await asyncio.gather(*services.background, return_exceptions=True)
        assert ATHLETE_ID not in services.pending_tracks

    asyncio.run(run())
    assert tiles.builds == []


@pytest.mark.usefixtures("connected")
def test_import_over_the_map_limit_records_omitted_tracks(
    config: Config,
    intervals: FakeIntervals,
    bucket: FakeBucket,
    redis_server: FakeServer,
    tiles: FakeTiles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities = [activity("i1", "2025-01-01"), activity("i2", "2026-01-01")]
    intervals.add_activities(activities)
    for item in activities:
        intervals.add_activity(item)
    limit_map_tracks(monkeypatch, 1)

    async def run() -> None:
        services = make_services(config, intervals, bucket, redis_server)
        await run_import(services, ATHLETE_ID)
        await asyncio.gather(*services.background)

    asyncio.run(run())
    assert import_status(redis_server).map_tracks_omitted == 1
    assert tiles.builds[-1] == ["i2"]


def test_import_status_from_before_the_map_limit() -> None:
    status = loads('{"state": "done", "activities": 3, "updated_at": 1}', ImportStatus)
    assert (status.state, status.map_tracks_omitted) == ("done", 0)
