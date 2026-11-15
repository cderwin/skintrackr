import asyncio
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from skintrackr.cli import migrate as migrate_module
from skintrackr.cli import strava_debug as strava_debug_module
from skintrackr.cli.main import main
from skintrackr.cli.strava_debug import download_activity_gpx
from skintrackr.clients.blob_store import archive_keys
from tests.conftest import ACTIVITY_ID, ATHLETE_ID, FakeBucket, FakeStrava


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert all(name in result.output for name in ("serve", "strava-debug", "migrate-legacy"))


def test_download_activity_gpx(strava: FakeStrava, tmp_path: Path) -> None:
    strava.add_activity()
    output = tmp_path / "activity.gpx"
    asyncio.run(download_activity_gpx(strava.http(), "token", str(ACTIVITY_ID), output))
    document = output.read_bytes()
    assert b"<name>Morning Skin</name>" in document
    # strava-debug includes the extensions, as the Go tool does
    assert b"<gpxtpx:hr>0.000000</gpxtpx:hr>" in document
    assert b"<gpxtpx:atemp>0.000000</gpxtpx:atemp>" in document
    assert strava.requests[0].headers["Authorization"] == "Bearer token"


def test_strava_debug_reads_token_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, Path]] = []

    async def fake_download(_: httpx.AsyncClient, token: str, activity_id: str, output: Path) -> None:
        calls.append((token, activity_id, output))

    monkeypatch.setattr(strava_debug_module, "download_activity_gpx", fake_download)
    output = tmp_path / "out.gpx"
    result = CliRunner().invoke(main, ["strava-debug", "-a", "42", "-o", str(output)], env={"STRAVA_TOKEN": "env-token"})
    assert result.exit_code == 0, result.output
    assert calls == [("env-token", "42", output)]


def test_strava_debug_requires_options() -> None:
    result = CliRunner().invoke(main, ["strava-debug", "-t", "x"], env={"STRAVA_TOKEN": ""})
    assert result.exit_code == 2
    assert "Missing option" in result.output


# --- migrate-legacy


@pytest.fixture
def legacy_bucket(bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch) -> FakeBucket:
    """The bucket the command uses, holding two activities in the former layout."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(migrate_module.BlobStore, "build", classmethod(lambda cls, config: bucket.store))
    for activity_id in (1, 2):
        bucket.put(f"activities/{ATHLETE_ID}/metadata/{activity_id}.json")
        bucket.put(f"activities/{ATHLETE_ID}/tracks/{activity_id}.gpx")
    bucket.put(archive_keys(ATHLETE_ID, 1).streams)
    return bucket


def migrate(*args: str, input: str | None = None):
    return CliRunner().invoke(main, ["migrate-legacy", "--athlete", str(ATHLETE_ID), *args], input=input)


def test_migrate_legacy_refuses_while_activities_are_missing(legacy_bucket: FakeBucket) -> None:
    result = migrate("--yes")
    assert result.exit_code == 1
    assert "1 of 2 activities aren't archived in the new layout yet: 2" in result.output
    assert len(legacy_bucket.keys()) == 5


def test_migrate_legacy_deletes_once_everything_is_archived(legacy_bucket: FakeBucket) -> None:
    legacy_bucket.put(archive_keys(ATHLETE_ID, 2).streams)
    assert migrate(input="n\n").exit_code == 1  # declined at the prompt
    result = migrate(input="y\n")
    assert result.exit_code == 0, result.output
    assert f"deleted 4 objects under activities/{ATHLETE_ID}/" in result.output
    assert legacy_bucket.keys() == {archive_keys(ATHLETE_ID, 1).streams, archive_keys(ATHLETE_ID, 2).streams}


def test_migrate_legacy_allow_missing(legacy_bucket: FakeBucket) -> None:
    assert migrate("--allow-missing", "--yes").exit_code == 0
    assert legacy_bucket.keys() == {archive_keys(ATHLETE_ID, 1).streams}


def test_migrate_legacy_nothing_to_do(bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(migrate_module.BlobStore, "build", classmethod(lambda cls, config: bucket.store))
    assert "nothing under" in migrate().output

