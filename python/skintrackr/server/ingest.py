"""Ingesting athletes' intervals.icu activities: in full into the bucket, and as summaries into the Redis index.

- `ingest_activity`: one activity (syncs, imports, `POST /api/activity/{id}/persist`).
- `start_import`: an athlete's bulk import (Re-sync), in the background: lists every activity into the index
  (one request, after which stats work), removes those deleted on intervals.icu, then archives each activity not
  yet archived in full (two requests each). Progress is saved after every activity, and running imports resume
  at startup.
- `run_sync_loop`: every `sync_interval`, syncs each connected athlete's activities that started within the
  `sync_window`: new and changed ones are ingested, deleted ones removed. Older changes need a Re-sync.
- `remove_activity` / `remove_athlete`: deleted activities, and disconnecting.
- `schedule_tracks` / `build_tracks`: the athlete's map tracks (`tracks.pmtiles`), rebuilt in the background
  after their activities change, at most every `tracks_debounce`, from the `MAP_TRACKS_LIMIT` most recent tracks.

Activities intervals.icu lists but doesn't serve through its API (`UNAVAILABLE_SOURCES`, synced from Strava)
are skipped.
"""

import asyncio
from datetime import UTC, date, datetime

from skintrackr.clients.blob_store import BlobStore, tracks_key
from skintrackr.clients.intervals import UNAVAILABLE_SOURCES, IntervalsClient, IntervalsError
from skintrackr.clients.store import Store
from skintrackr.logging import get_logger
from skintrackr.models import ActivitySummary
from skintrackr.server.services import Services
from skintrackr.tracks import PMTILES_MEDIA_TYPE, MapTracks, build_pmtiles, map_track_ids

logger = get_logger(__name__)

# list activities from the beginning
EPOCH = date(1970, 1, 1)
SYNC_TASK = "sync"
RECONNECT_MESSAGE = "intervals.icu rejected the API key: reconnect to update it"


def _import_task(athlete_id: str) -> str:
    return f"import-{athlete_id}"


def _tracks_task(athlete_id: str) -> str:
    return f"tracks-{athlete_id}"


def _available(summaries: list[ActivitySummary]) -> list[ActivitySummary]:
    return [summary for summary in summaries if summary.source not in UNAVAILABLE_SOURCES]


async def ingest_activity(
    services: Services, intervals: IntervalsClient, athlete_id: str, activity_id: str
) -> ActivitySummary:
    """Archive an activity in full and add (or update) it in the athlete's index.

    Raises:
        IntervalsError: Fetching it failed.
        botocore.exceptions.ClientError: Uploading it failed.
    """
    summary = await services.blob_store.archive_activity(intervals, athlete_id, activity_id)
    await services.store.save_activities(athlete_id, [summary])
    schedule_tracks(services, athlete_id)
    return summary


async def remove_activity(services: Services, athlete_id: str, activity_id: str) -> None:
    """Delete an activity from the bucket and the athlete's index."""
    await services.blob_store.delete_activity(athlete_id, activity_id)
    await services.store.delete_activity(athlete_id, activity_id)
    schedule_tracks(services, athlete_id)
    logger.info("removed activity", extra={"athlete_id": athlete_id, "activity_id": activity_id})


async def remove_athlete(services: Services, athlete_id: str) -> None:
    """Delete everything stored for an athlete: archive, index, import progress and API key."""
    services.cancel(_import_task(athlete_id))
    services.cancel(_tracks_task(athlete_id))
    services.pending_tracks.discard(athlete_id)
    deleted = await services.blob_store.delete_athlete(athlete_id)
    await services.store.delete_athlete(athlete_id)
    logger.info("removed athlete", extra={"athlete_id": athlete_id, "deleted_objects": deleted})


# --- bulk imports


def start_import(services: Services, athlete_id: str) -> bool:
    """Start an athlete's bulk import in the background; False if one is already running."""
    if services.is_running(_import_task(athlete_id)):
        return False
    services.spawn(run_import(services, athlete_id), name=_import_task(athlete_id))
    return True


async def resume_imports(services: Services) -> None:
    """Restart the imports that were running when the server last stopped. Errors are logged."""
    try:
        athletes = await services.store.running_imports()
    except Exception as e:
        logger.error("error listing running imports", exc_info=e)
        return
    for athlete_id in athletes:
        logger.info("resuming import", extra={"athlete_id": athlete_id})
        start_import(services, athlete_id)


async def run_import(services: Services, athlete_id: str) -> None:
    """Import every activity of an athlete (see the module docstring). Failures are recorded in its status."""
    store, context = services.store, {"athlete_id": athlete_id}
    status = await store.load_import(athlete_id)
    status.state, status.error = "running", ""
    await store.save_import(athlete_id, status)
    try:
        intervals = (await services.intervals_for(athlete_id)).in_background()
        listed = await intervals.list_activities(athlete_id, EPOCH)
        summaries = _available(listed)
        await store.save_activities(athlete_id, summaries)
        listed_ids = {summary.id for summary in summaries}
        for activity in await store.activities(athlete_id):
            if activity.id not in listed_ids:  # deleted on intervals.icu
                await remove_activity(services, athlete_id, activity.id)

        archived = await services.blob_store.archived_activity_ids(athlete_id)
        pending = [summary.id for summary in summaries if summary.id not in archived]
        status.activities = status.tracks_total = len(summaries)
        status.tracks_done = len(summaries) - len(pending)
        status.skipped = len(listed) - len(summaries)
        await store.save_import(athlete_id, status)
        logger.info("imported activity summaries", extra={**context, "activities": len(summaries), "pending": len(pending)})

        for activity_id in pending:
            try:
                await ingest_activity(services, intervals, athlete_id, activity_id)
                status.tracks_done += 1
            except IntervalsError as e:
                if e.status_code != 404:
                    raise
                # deleted on intervals.icu since it was listed
                await store.delete_activity(athlete_id, activity_id)
                status.activities -= 1
                status.tracks_total -= 1
            await store.save_import(athlete_id, status)
        status.map_tracks_omitted = (await select_map_tracks(store, services.blob_store, athlete_id)).omitted
        schedule_tracks(services, athlete_id)
        status.state = "done"
        logger.info("import finished", extra={**context, "activities": status.activities})
    except Exception as e:
        logger.error("import failed", exc_info=e, extra=context)
        status.state = "failed"
        status.error = RECONNECT_MESSAGE if isinstance(e, IntervalsError) and e.unauthorized else str(e)
    await store.save_import(athlete_id, status)


# --- map tracks


async def select_map_tracks(store: Store, blob_store: BlobStore, athlete_id: str) -> MapTracks:
    """The tracks an athlete's map holds: the most recent indexed activities with a track."""
    return map_track_ids(await store.activities(athlete_id), await blob_store.track_activity_ids(athlete_id))


async def build_tracks(store: Store, blob_store: BlobStore, athlete_id: str) -> MapTracks:
    """Rebuild an athlete's map tracks (`tracks.pmtiles`), or delete them if they have no tracks.

    Raises:
        TrackBuildError: tippecanoe failed.
        botocore.exceptions.ClientError: Reading the tracks or uploading the archive failed.
    """
    tracks = await select_map_tracks(store, blob_store, athlete_id)
    context = {"athlete_id": athlete_id, "included": len(tracks.included)}
    if tracks.omitted:
        total = len(tracks.included) + tracks.omitted
        logger.warning("map tracks limited", extra={**context, "tracks": total, "omitted": tracks.omitted})
    if tracks.included:
        features = await blob_store.read_track_features(athlete_id, tracks.included)
        await blob_store.put(tracks_key(athlete_id), await build_pmtiles(features), PMTILES_MEDIA_TYPE)
    else:
        await blob_store.delete_keys([tracks_key(athlete_id)])
    logger.info("built map tracks", extra=context)
    return tracks


def schedule_tracks(services: Services, athlete_id: str) -> None:
    """Rebuild an athlete's map tracks in the background: now if no rebuild is running, else after it."""
    services.pending_tracks.add(athlete_id)
    if not services.is_running(_tracks_task(athlete_id)):
        services.spawn(run_tracks(services, athlete_id), name=_tracks_task(athlete_id))


async def run_tracks(services: Services, athlete_id: str) -> None:
    """Rebuild an athlete's map tracks while rebuilds are pending, at most every `tracks_debounce`.

    Failures are logged; the next change schedules another rebuild.
    """
    while athlete_id in services.pending_tracks:
        services.pending_tracks.discard(athlete_id)
        try:
            await build_tracks(services.store, services.blob_store, athlete_id)
        except Exception as e:
            logger.error("map tracks build failed", exc_info=e, extra={"athlete_id": athlete_id})
        await asyncio.sleep(services.config.tracks_debounce.total_seconds())


# --- periodic sync


async def sync_athlete(services: Services, athlete_id: str, today: date | None = None) -> None:
    """Sync an athlete's activities that started within the sync window (see the module docstring).

    Skipped while the athlete's import is running: it covers everything.

    Raises:
        NotFoundError: The athlete isn't connected.
        IntervalsError: Listing or fetching activities failed.
        botocore.exceptions.ClientError: Archiving failed.
    """
    if services.is_running(_import_task(athlete_id)):
        return
    oldest = (today or datetime.now(UTC).date()) - services.config.sync_window
    intervals = (await services.intervals_for(athlete_id)).in_background()
    listed = {summary.id: summary for summary in _available(await intervals.list_activities(athlete_id, oldest))}
    indexed = {
        activity.id: activity
        for activity in await services.store.activities(athlete_id)
        if activity.start_date_local[:10] >= oldest.isoformat()
    }
    changed = [activity_id for activity_id, summary in listed.items() if indexed.get(activity_id) != summary]
    deleted = indexed.keys() - listed.keys()
    for activity_id in changed:
        await ingest_activity(services, intervals, athlete_id, activity_id)
    for activity_id in deleted:
        await remove_activity(services, athlete_id, activity_id)
    if changed or deleted:
        logger.info("synced activities", extra={"athlete_id": athlete_id, "ingested": len(changed), "removed": len(deleted)})


async def sync_all(services: Services) -> None:
    """Sync every connected athlete, one at a time. Errors are logged per athlete."""
    try:
        athletes = await services.store.connected_athletes()
    except Exception as e:
        logger.error("error listing connected athletes", exc_info=e)
        return
    for athlete_id in athletes:
        try:
            await sync_athlete(services, athlete_id)
        except Exception as e:
            unauthorized = isinstance(e, IntervalsError) and e.unauthorized
            logger.error(RECONNECT_MESSAGE if unauthorized else "sync failed", exc_info=e, extra={"athlete_id": athlete_id})


async def run_sync_loop(services: Services) -> None:
    """Sync every connected athlete now and then every `sync_interval`, until cancelled (at shutdown)."""
    while True:
        await sync_all(services)
        await asyncio.sleep(services.config.sync_interval.total_seconds())


def start_sync_loop(services: Services) -> None:
    """Run the sync loop in the background."""
    services.spawn(run_sync_loop(services), name=SYNC_TASK)
