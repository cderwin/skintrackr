"""Ingesting athletes' Strava activities: in full into the bucket, and as summaries into the Redis index.

- `ingest_activity`: one activity (webhook create/update events, `POST /api/activity/{id}/persist`).
- `start_import`: an athlete's bulk import, in the background: every activity's summary first (a few requests,
  after which stats work), then each activity not yet archived in full (two requests each, waiting out
  Strava's rate limits). Progress is saved after every activity, and running imports resume at startup.
- `remove_activity` / `remove_athlete`: webhook delete and deauthorization events.
"""

from datetime import UTC, datetime

from skintrackr.clients.strava import StravaClient, StravaError
from skintrackr.logging import get_logger
from skintrackr.models import ActivitySummary
from skintrackr.server.services import Services

logger = get_logger(__name__)

# list activities from the beginning
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _import_task(athlete_id: int) -> str:
    return f"import-{athlete_id}"


async def ingest_activity(services: Services, strava: StravaClient, athlete_id: int, activity_id: int) -> ActivitySummary:
    """Archive an activity in full and add (or update) it in the athlete's index.

    Raises:
        StravaError: Fetching it failed.
        botocore.exceptions.ClientError: Uploading it failed.
    """
    summary = await services.blob_store.archive_activity(strava, athlete_id, activity_id)
    await services.store.save_activities(athlete_id, [summary])
    return summary


async def remove_activity(services: Services, athlete_id: int, activity_id: int) -> None:
    """Delete an activity from the bucket and the athlete's index."""
    await services.blob_store.delete_activity(athlete_id, activity_id)
    await services.store.delete_activity(athlete_id, activity_id)
    logger.info("removed activity", extra={"athlete_id": athlete_id, "activity_id": activity_id})


async def remove_athlete(services: Services, athlete_id: int) -> None:
    """Delete everything stored for an athlete: archive, index, import progress and Strava tokens."""
    services.cancel(_import_task(athlete_id))
    deleted = await services.blob_store.delete_athlete(athlete_id)
    await services.store.delete_athlete(athlete_id)
    logger.info("removed athlete", extra={"athlete_id": athlete_id, "deleted_objects": deleted})


def start_import(services: Services, athlete_id: int) -> bool:
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


async def run_import(services: Services, athlete_id: int) -> None:
    """Import every activity of an athlete (see the module docstring). Failures are recorded in its status."""
    store, context = services.store, {"athlete_id": athlete_id}
    status = await store.load_import(athlete_id)
    status.state, status.error = "running", ""
    await store.save_import(athlete_id, status)
    try:
        strava = await _background_client(services, athlete_id)
        summaries = await strava.list_activities(EPOCH)
        await store.save_activities(athlete_id, summaries)
        archived = await services.blob_store.archived_activity_ids(athlete_id)
        pending = [summary.id for summary in summaries if summary.id not in archived]
        status.activities = status.tracks_total = len(summaries)
        status.tracks_done = len(summaries) - len(pending)
        await store.save_import(athlete_id, status)
        logger.info("imported activity summaries", extra={**context, "activities": len(summaries), "pending": len(pending)})

        for activity_id in pending:
            # fetched per activity: an import can outlast the 6-hour access token
            strava = await _background_client(services, athlete_id)
            try:
                await ingest_activity(services, strava, athlete_id, activity_id)
                status.tracks_done += 1
            except StravaError as e:
                if e.status_code != 404:
                    raise
                # deleted on Strava since it was listed
                await store.delete_activity(athlete_id, activity_id)
                status.activities -= 1
                status.tracks_total -= 1
            await store.save_import(athlete_id, status)
        status.state = "done"
        logger.info("import finished", extra={**context, "activities": status.activities})
    except Exception as e:
        logger.error("import failed", exc_info=e, extra=context)
        status.state, status.error = "failed", str(e)
    await store.save_import(athlete_id, status)


async def _background_client(services: Services, athlete_id: int) -> StravaClient:
    return (await services.strava_for(athlete_id)).in_background()
