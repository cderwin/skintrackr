from collections.abc import Iterator
from typing import Any

import pytest
from click.testing import CliRunner, Result
from fakeredis import FakeServer

from skintrackr.cli import migrate as migrate_module
from skintrackr.cli.main import main
from skintrackr.clients.blob_store import archive_keys, tracks_key
from skintrackr.models import ActivitySummary
from tests.conftest import (
    ACTIVITY,
    ATHLETE_ID,
    SECRET,
    STREAMS,
    FakeBucket,
    FakeIntervals,
    connect,
    fake_redis,
    summary,
    with_store,
)

LEGACY_ATHLETE = "4242"


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert all(name in result.output for name in ("serve", "migrate"))
    migrations = CliRunner().invoke(main, ["migrate", "--help"])
    assert all(
        name in migrations.output for name in ("legacy-layout", "intervals-reset", "build-tracks", "refresh-summaries")
    )


@pytest.fixture
def env_bucket(bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch) -> FakeBucket:
    """The bucket the commands use, with its credentials in the environment."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(migrate_module.BlobStore, "build", classmethod(lambda cls, config: bucket.store))
    return bucket


# --- migrate legacy-layout


@pytest.fixture
def legacy_bucket(env_bucket: FakeBucket) -> FakeBucket:
    """Two activities in the former layout, one of them also in the new one."""
    for activity_id in (1, 2):
        env_bucket.put(f"activities/{LEGACY_ATHLETE}/metadata/{activity_id}.json")
        env_bucket.put(f"activities/{LEGACY_ATHLETE}/tracks/{activity_id}.gpx")
    env_bucket.put(archive_keys(LEGACY_ATHLETE, "1").streams)
    return env_bucket


def legacy_layout(*args: str, input: str | None = None) -> Result:
    return CliRunner().invoke(main, ["migrate", "legacy-layout", "--athlete", LEGACY_ATHLETE, *args], input=input)


def test_legacy_layout_refuses_while_activities_are_missing(legacy_bucket: FakeBucket) -> None:
    result = legacy_layout("--yes")
    assert result.exit_code == 1
    assert "1 of 2 activities aren't archived in the new layout yet: 2" in result.output
    assert len(legacy_bucket.keys()) == 5


def test_legacy_layout_deletes_once_everything_is_archived(legacy_bucket: FakeBucket) -> None:
    legacy_bucket.put(archive_keys(LEGACY_ATHLETE, "2").streams)
    assert legacy_layout(input="n\n").exit_code == 1  # declined at the prompt
    result = legacy_layout(input="y\n")
    assert result.exit_code == 0, result.output
    assert f"deleted 4 objects under activities/{LEGACY_ATHLETE}/" in result.output
    assert legacy_bucket.keys() == {archive_keys(LEGACY_ATHLETE, "1").streams, archive_keys(LEGACY_ATHLETE, "2").streams}


def test_legacy_layout_allow_missing(legacy_bucket: FakeBucket) -> None:
    assert legacy_layout("--allow-missing", "--yes").exit_code == 0
    assert legacy_bucket.keys() == {archive_keys(LEGACY_ATHLETE, "1").streams}


def test_legacy_layout_nothing_to_do(env_bucket: FakeBucket) -> None:
    assert "nothing under" in legacy_layout().output


# --- migrate intervals-reset


@pytest.fixture
def reset_env(env_bucket: FakeBucket, redis_server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBucket]:
    """Settings for `intervals-reset`, with Redis pointing at the fake server."""
    monkeypatch.setenv("APP_SECRET", SECRET)
    monkeypatch.setenv("UPSTASH_REDIS_URL", "redis://unused")
    monkeypatch.setattr(migrate_module.Redis, "from_url", lambda *_, **__: fake_redis(redis_server))
    yield env_bucket


def reset(input: str | None = None, *args: str) -> Result:
    return CliRunner().invoke(main, ["migrate", "intervals-reset", *args], input=input)


def test_intervals_reset_deletes_everything(reset_env: FakeBucket, redis_server: FakeServer) -> None:
    reset_env.put(f"activities/{LEGACY_ATHLETE}/tracks/1.gpx")
    reset_env.put(archive_keys(ATHLETE_ID, "i1").streams)
    connect(redis_server)
    with_store(redis_server, lambda s: s.redis.set("someone-elses-key", 1))

    declined = reset("n\n")
    assert declined.exit_code == 1
    assert "2 objects; redis: 2 skintrackr keys" in declined.output
    assert len(reset_env.keys()) == 2

    result = reset("y\n")
    assert result.exit_code == 0, result.output
    assert "deleted 2 objects and 2 keys" in result.output
    assert reset_env.keys() == set()
    assert with_store(redis_server, lambda s: s.redis.keys("*")) == ["someone-elses-key"]
    assert "nothing to delete" in reset(None, "--yes").output  # safe to re-run


def test_intervals_reset_requires_settings(env_bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_SECRET", raising=False)
    result = reset(None, "--yes")
    assert result.exit_code == 1
    assert "APP_SECRET must be set" in str(result.exception)


# --- migrate build-tracks


def archive_activity(bucket: FakeBucket, activity_id: str, streams: list[dict[str, Any]]) -> None:
    """An activity archived before tracks existed: its JSON and streams, no track."""
    keys = archive_keys(ATHLETE_ID, activity_id)
    bucket.put_json(keys.activity, {**ACTIVITY, "id": activity_id})
    bucket.put_json(keys.streams, streams)


def test_build_tracks_backfills_tracks_and_builds_the_map(
    reset_env: FakeBucket, redis_server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_build(features: list[bytes]) -> bytes:
        return b"PMTiles %d" % len(features)

    monkeypatch.setattr("skintrackr.server.ingest.build_pmtiles", fake_build)
    connect(redis_server)
    archive_activity(reset_env, "i1", STREAMS)
    archive_activity(reset_env, "i2", [s for s in STREAMS if s["type"] != "latlng"])  # indoor
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, [ActivitySummary(id="i1"), ActivitySummary(id="i2")]))

    for _ in range(2):  # safe to re-run
        result = CliRunner().invoke(main, ["migrate", "build-tracks"])
        assert result.exit_code == 0, result.output
        assert f"{ATHLETE_ID}: 2 archived activities, 1 tracks on the map" in result.output

    assert archive_keys(ATHLETE_ID, "i1").track in reset_env.keys()
    assert archive_keys(ATHLETE_ID, "i2").track not in reset_env.keys()
    assert reset_env.json(archive_keys(ATHLETE_ID, "i1").track)["properties"]["id"] == "i1"
    assert reset_env.read(tracks_key(ATHLETE_ID)) == b"PMTiles 1"


# --- migrate refresh-summaries


def test_refresh_summaries_rewrites_indexed_summaries_from_intervals(
    reset_env: FakeBucket, redis_server: FakeServer, intervals: FakeIntervals, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrate_module, "build_intervals", lambda config, http: intervals.client(api_key=""))
    connect(redis_server)
    listed = [summary("i1", "2025-01-02", strava_id="161"), summary("i2", "2025-01-03"), summary("i3", "2025-01-04")]
    intervals.add_activities(listed)
    archived = [ActivitySummary(**summary(a, d)) for a, d in [("i1", "2025-01-02"), ("i2", "2025-01-03")]]
    with_store(redis_server, lambda s: s.save_activities(ATHLETE_ID, archived))

    for rewritten in (1, 0):  # safe to re-run
        result = CliRunner().invoke(main, ["migrate", "refresh-summaries"])
        assert result.exit_code == 0, result.output
        assert f"{ATHLETE_ID}: {rewritten} summaries rewritten" in result.output

    index = {a.id: a for a in with_store(redis_server, lambda s: s.activities(ATHLETE_ID))}
    assert set(index) == {"i1", "i2"}  # i3 isn't archived yet: left to imports and syncs
    assert (index["i1"].strava_id, index["i2"].strava_id) == ("161", "")
    assert "i0: failed" in CliRunner().invoke(main, ["migrate", "refresh-summaries", "--athlete", "i0"]).output
