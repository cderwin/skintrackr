"""Authenticated activity API: GPX export and archiving to blob storage."""

from typing import Any

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.params import FromPath

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


api_handlers = [export_track, persist_activity]
