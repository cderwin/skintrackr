"""Authenticated activity API: GPX export, archiving to blob storage, and vert stats."""

from datetime import UTC, date, datetime
from typing import Any

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import FromPath, FromQuery

from skintrackr.models import converter
from skintrackr.server.auth import authenticate_request
from skintrackr.server.errors import fail_with
from skintrackr.server.services import get_services

GPX_MEDIA_TYPE = "application/gpx+xml"


@get("/api/activity/{activity_id:str}/export")
async def export_track(request: Request, state: State, activity_id: FromPath[str]) -> Response[bytes]:
    """Download one of the athlete's activities as a GPX file."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    context = {"athlete_id": info.athlete_id, "activity_id": activity_id}

    with fail_with("failed to fetch Strava token", **context):
        strava = await services.strava_for(info.athlete_id)
    with fail_with("failed to fetch activity", **context):
        activity = await strava.get_activity(activity_id)
    with fail_with("failed to export activity", **context):
        track = await strava.export_activity_gpx(activity)

    return Response(
        track,
        media_type=GPX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="activity-{activity_id}.gpx"'},
    )


@post("/api/activity/{activity_id:str}/persist", status_code=200)
async def persist_activity(request: Request, state: State, activity_id: FromPath[str]) -> dict[str, Any]:
    """Archive one of the athlete's activities (details and GPX track) to blob storage."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    context = {"athlete_id": info.athlete_id, "activity_id": activity_id}

    with fail_with("failed to fetch Strava token", **context):
        strava = await services.strava_for(info.athlete_id)
    with fail_with("failed to persist activity", **context):
        await services.blob_store.persist_activity(strava, activity_id)
    return {"ok": True, "activityId": activity_id}


@get("/api/stats/vert")
async def vert_stats(request: Request, state: State, today: FromQuery[date | None] = None) -> dict[str, Any]:
    """The athlete's on-foot vert from Jan 1 to today's date, this year and in previous years.

    `today` is the athlete's local date (default: the server's, in UTC); it may differ from UTC by a day.
    """
    services = get_services(state)
    info = await authenticate_request(request, services)
    utc_today = datetime.now(UTC).date()
    as_of = today or utc_today
    if abs((as_of - utc_today).days) > 1:
        raise ClientException("today must be within a day of the current date")

    with fail_with("failed to fetch Strava token", athlete_id=info.athlete_id):
        strava = await services.strava_for(info.athlete_id)
    with fail_with("failed to fetch activities", athlete_id=info.athlete_id):
        stats = await services.vert.year_to_date(strava, info.athlete_id, as_of)
    return converter.unstructure(stats)


api_handlers = [export_track, persist_activity, vert_stats]
