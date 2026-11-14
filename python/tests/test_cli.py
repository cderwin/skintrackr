import asyncio
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from skintrackr.cli import strava_debug as strava_debug_module
from skintrackr.cli.main import main
from skintrackr.cli.strava_debug import download_activity_gpx
from tests.conftest import ACTIVITY_ID, FakeStrava


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output and "strava-debug" in result.output


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
