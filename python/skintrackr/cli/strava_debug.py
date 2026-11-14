"""`skintrackr strava-debug`: download an activity's GPX track with a Strava access token."""

import asyncio
from pathlib import Path

import click
import httpx

from skintrackr.clients.strava import StravaClient
from skintrackr.config import debug_strava_responses
from skintrackr.logging import configure_logging


async def download_activity_gpx(http: httpx.AsyncClient, token: str, activity_id: str, output: Path) -> None:
    """Fetch an activity and write its GPX track (with heart rate and temperature extensions) to `output`."""
    strava = StravaClient(http=http, token=token, debug_dir=Path.cwd() if debug_strava_responses() else None)
    activity = await strava.get_activity(activity_id)
    output.write_bytes(await strava.export_activity_gpx(activity, use_heart_rate=True, use_temperature=True))


@click.command("strava-debug")
@click.option("--token", "-t", required=True, envvar="STRAVA_TOKEN", help="Strava access token [env: STRAVA_TOKEN].")
@click.option("--activity-id", "-a", required=True, help="Strava activity id.")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path), help="GPX file to write.")
def strava_debug(token: str, activity_id: str, output: Path) -> None:
    """Download an activity's GPX track straight from Strava, for debugging exports."""
    configure_logging()

    async def run() -> None:
        async with httpx.AsyncClient(timeout=30.0) as http:
            await download_activity_gpx(http, token, activity_id, output)

    asyncio.run(run())
    click.echo(f"wrote {output}")
