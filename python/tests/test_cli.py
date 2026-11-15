from collections.abc import Iterator

import pytest
from click.testing import CliRunner, Result
from fakeredis import FakeServer

from skintrackr.cli import migrate as migrate_module
from skintrackr.cli.main import main
from skintrackr.clients.blob_store import archive_keys
from tests.conftest import ATHLETE_ID, SECRET, FakeBucket, connect, fake_redis, with_store

LEGACY_ATHLETE = "4242"


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert all(name in result.output for name in ("serve", "migrate"))
    migrations = CliRunner().invoke(main, ["migrate", "--help"])
    assert all(name in migrations.output for name in ("legacy-layout", "intervals-reset"))


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
