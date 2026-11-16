"""`skintrackr migrate ...`: one-off data migrations.

Each migration is a subcommand. Migrations stay here after they've run (so they can be re-run or read later),
and may be cleaned up in the distant future. Each reads its settings from the environment.
"""

import asyncio
from collections.abc import Awaitable, Callable

import click
from redis.asyncio import Redis

from skintrackr.clients.blob_store import BlobStore, legacy_prefix
from skintrackr.clients.store import Store
from skintrackr.config import BlobStorageConfig, Config
from skintrackr.logging import configure_logging
from skintrackr.server.ingest import build_tracks

# activities whose tracks are written at once by `build-tracks`
TRACK_CONCURRENCY = 8


@click.group()
def migrate() -> None:
    """One-off data migrations (they stay available after they've run)."""
    configure_logging()


@migrate.command("legacy-layout")
@click.option("--athlete", "athlete_id", required=True, help="Athlete id the former layout is keyed by.")
@click.option("--allow-missing", is_flag=True, help="Delete even if some activities aren't in the new layout.")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
def legacy_layout(athlete_id: str, allow_missing: bool, yes: bool) -> None:
    """Delete an athlete's `activities/{athlete}/` archive (the Go server's metadata JSON and GPX), once every
    activity in it is archived in the new layout.

    Written for the move to the `athletes/` layout (run the athlete's import first). Since the move to
    intervals.icu, whose activity ids differ, activities in the former layout are never in the new one: pass
    `--allow-missing`, or use `intervals-reset`, which deletes both layouts.
    """
    store = BlobStore.build(BlobStorageConfig.from_env())

    async def compare() -> tuple[set[str], set[str]]:
        return await store.legacy_activity_ids(athlete_id), await store.archived_activity_ids(athlete_id)

    legacy, archived = asyncio.run(compare())
    if not legacy:
        click.echo(f"nothing under {legacy_prefix(athlete_id)}")
        return
    missing = sorted(legacy - archived)
    if missing and not allow_missing:
        shown = ", ".join(missing[:20]) + (", ..." if len(missing) > 20 else "")
        raise click.ClickException(
            f"{len(missing)} of {len(legacy)} activities aren't archived in the new layout yet: {shown}. "
            "Finish the athlete's import; if they were deleted since, pass --allow-missing."
        )
    if not yes:
        click.confirm(f"Delete everything under {legacy_prefix(athlete_id)} ({len(legacy)} activities)?", abort=True)
    deleted = asyncio.run(store.delete_prefix(legacy_prefix(athlete_id)))
    click.echo(f"deleted {deleted} objects under {legacy_prefix(athlete_id)}")


def _with_store[T](config: Config, action: Callable[[Store], Awaitable[T]]) -> T:
    """Run `action` against a Store on the configured Redis, in its own event loop."""

    async def run() -> T:
        redis = Redis.from_url(config.redis_url, decode_responses=True)
        try:
            return await action(Store(redis=redis, secret=config.secret))
        finally:
            await redis.aclose()

    return asyncio.run(run())


@migrate.command("intervals-reset")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
def intervals_reset(yes: bool) -> None:
    """Delete everything in the bucket and every skintrackr key in Redis, for a fresh start.

    Written for the move to intervals.icu (2026-11), whose ids differ from everything stored before. Other keys
    in the Redis database are left alone. Safe to re-run.
    """
    config = Config.from_env()
    blob_store = BlobStore.build(config.blob_storage)
    objects = asyncio.run(blob_store.list_keys(""))
    keys = _with_store(config, lambda store: store.app_keys())
    click.echo(f"bucket {blob_store.bucket}: {len(objects)} objects; redis: {len(keys)} skintrackr keys")
    if not objects and not keys:
        click.echo("nothing to delete")
        return
    if not yes:
        click.confirm("Delete all of them?", abort=True)
    deleted_objects = asyncio.run(blob_store.delete_prefix(""))
    deleted_keys = _with_store(config, lambda store: store.delete_everything())
    click.echo(f"deleted {deleted_objects} objects and {deleted_keys} keys")


async def _write_tracks(blob_store: BlobStore, athlete_id: str) -> int:
    """Write the track of every activity archived for an athlete, from its streams; returns how many were read."""
    activity_ids = await blob_store.archived_activity_ids(athlete_id)
    limit = asyncio.Semaphore(TRACK_CONCURRENCY)

    async def write(activity_id: str) -> None:
        async with limit:
            archived = await blob_store.read_activity(athlete_id, activity_id)
            if archived is not None:
                await blob_store.save_track(athlete_id, *archived)

    await asyncio.gather(*(write(activity_id) for activity_id in activity_ids))
    return len(activity_ids)


@migrate.command("build-tracks")
@click.option("--athlete", "athlete_ids", multiple=True, help="Only this athlete (repeatable; default: every connected one).")
def build_tracks_command(athlete_ids: tuple[str, ...]) -> None:
    """Write `track.geojson.gz` for every archived activity, then build each athlete's map tracks.

    Written for the activity map (2026-11): tracks are derived from the archived streams, which were archived
    before tracks existed. Rewrites every track and archive, so it's safe to re-run (e.g. after changing how
    tracks are simplified).
    """
    config = Config.from_env()
    blob_store = BlobStore.build(config.blob_storage)
    athletes = list(athlete_ids) or _with_store(config, lambda store: store.connected_athletes())
    for athlete_id in athletes:
        activities = asyncio.run(_write_tracks(blob_store, athlete_id))
        tracks = _with_store(config, lambda store: build_tracks(store, blob_store, athlete_id))
        omitted = f", {tracks.omitted} older ones left off the map" if tracks.omitted else ""
        click.echo(f"{athlete_id}: {activities} archived activities, {len(tracks.included)} tracks on the map{omitted}")
