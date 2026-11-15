"""`skintrackr migrate-legacy`: delete an athlete's archive in the former (Go server) layout."""

import asyncio

import click

from skintrackr.clients.blob_store import BlobStore, legacy_prefix
from skintrackr.config import BlobStorageConfig
from skintrackr.logging import configure_logging


@click.command("migrate-legacy")
@click.option("--athlete", "athlete_id", required=True, type=int, help="Strava athlete id.")
@click.option("--allow-missing", is_flag=True, help="Delete even if some activities aren't in the new layout.")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
def migrate_legacy(athlete_id: int, allow_missing: bool, yes: bool) -> None:
    """Delete an athlete's `activities/{athlete}/` archive (metadata JSON and GPX), once every activity in it is
    archived in the new layout (run their bulk import first). Reads the blob storage settings from the environment.
    """
    configure_logging()
    store = BlobStore.build(BlobStorageConfig.from_env())

    async def compare() -> tuple[set[int], set[int]]:
        return await store.legacy_activity_ids(athlete_id), await store.archived_activity_ids(athlete_id)

    legacy, archived = asyncio.run(compare())
    if not legacy:
        click.echo(f"nothing under {legacy_prefix(athlete_id)}")
        return
    missing = sorted(legacy - archived)
    if missing and not allow_missing:
        shown = ", ".join(map(str, missing[:20])) + (", ..." if len(missing) > 20 else "")
        raise click.ClickException(
            f"{len(missing)} of {len(legacy)} activities aren't archived in the new layout yet: {shown}. "
            "Finish the athlete's import; if they were deleted on Strava, pass --allow-missing."
        )
    if not yes:
        click.confirm(f"Delete everything under {legacy_prefix(athlete_id)} ({len(legacy)} activities)?", abort=True)
    deleted = asyncio.run(store.delete_prefix(legacy_prefix(athlete_id)))
    click.echo(f"deleted {deleted} objects under {legacy_prefix(athlete_id)}")
