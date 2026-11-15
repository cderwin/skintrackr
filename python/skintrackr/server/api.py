"""Authenticated activity API: GPX export, archiving, bulk imports, and vert stats."""

import re
from datetime import UTC, date, datetime
from typing import Any

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import FromPath, FromQuery

from skintrackr.clients.intervals import activity_gpx, merge_streams
from skintrackr.models import converter
from skintrackr.server.auth import authenticate_request
from skintrackr.server.errors import fail_with
from skintrackr.server.ingest import ingest_activity, start_import
from skintrackr.server.services import get_services
from skintrackr.vert import year_to_date

GPX_MEDIA_TYPE = "application/gpx+xml"
# intervals.icu activity ids look like `i123456789`
ACTIVITY_ID = re.compile(r"[A-Za-z0-9_-]+")


def _activity_id(value: str) -> str:
    if not ACTIVITY_ID.fullmatch(value):
        raise ClientException("invalid activity id")
    return value


@get("/api/activity/{activity_id:str}/export")
async def export_track(request: Request, state: State, activity_id: FromPath[str]) -> Response[bytes]:
    """Download one of the athlete's activities as a GPX file: from the archive, else straight from intervals.icu."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    context = {"athlete_id": info.athlete_id, "activity_id": activity_id}

    with fail_with("failed to read archived activity", **context):
        archived = await services.blob_store.read_activity(info.athlete_id, _activity_id(activity_id))
    if archived is not None:
        activity, streams = archived
        with fail_with("failed to export activity", **context):
            track = activity_gpx(activity, merge_streams(streams, skip_unknown=True))
    else:
        with fail_with("failed to fetch intervals.icu API key", **context):
            intervals = await services.intervals_for(info.athlete_id)
        with fail_with("failed to fetch activity", **context):
            activity = await intervals.get_activity(activity_id)
        with fail_with("failed to export activity", **context):
            track = await intervals.export_activity_gpx(activity)

    return Response(
        track,
        media_type=GPX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="activity-{activity_id}.gpx"'},
    )


@post("/api/activity/{activity_id:str}/persist", status_code=200)
async def persist_activity(request: Request, state: State, activity_id: FromPath[str]) -> dict[str, Any]:
    """Archive one of the athlete's activities (details and streams) and add it to their index."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    context = {"athlete_id": info.athlete_id, "activity_id": activity_id}

    with fail_with("failed to fetch intervals.icu API key", **context):
        intervals = await services.intervals_for(info.athlete_id)
    with fail_with("failed to persist activity", **context):
        await ingest_activity(services, intervals, info.athlete_id, _activity_id(activity_id))
    return {"ok": True, "activityId": activity_id}


@post("/api/import", status_code=202)
async def import_start(request: Request, state: State) -> dict[str, Any]:
    """Start a bulk import of the athlete's activities (if one isn't running); answers with its status."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    start_import(services, info.athlete_id)
    with fail_with("failed to load import status", athlete_id=info.athlete_id):
        status = await services.store.load_import(info.athlete_id)
    # the task may not have saved its first status yet
    status.state = "running"
    return converter.unstructure(status)


@get("/api/import")
async def import_status(request: Request, state: State) -> dict[str, Any]:
    """The athlete's bulk import progress."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    with fail_with("failed to load import status", athlete_id=info.athlete_id):
        status = await services.store.load_import(info.athlete_id)
    return converter.unstructure(status)


@get("/api/stats/vert")
async def vert_stats(request: Request, state: State, today: FromQuery[date | None] = None) -> dict[str, Any]:
    """The athlete's on-foot vert from Jan 1 to today's date, this year and in previous years, from their index.

    `today` is the athlete's local date (default: the server's, in UTC); it may differ from UTC by a day.
    """
    services = get_services(state)
    info = await authenticate_request(request, services)
    utc_today = datetime.now(UTC).date()
    as_of = today or utc_today
    if abs((as_of - utc_today).days) > 1:
        raise ClientException("today must be within a day of the current date")

    with fail_with("failed to load activities", athlete_id=info.athlete_id):
        activities = await services.store.activities(info.athlete_id)
    return converter.unstructure(year_to_date(activities, as_of))


api_handlers = [export_track, persist_activity, import_start, import_status, vert_stats]
