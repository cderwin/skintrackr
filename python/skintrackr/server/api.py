"""Authenticated activity API: GPX export, archiving, bulk imports, vert stats, and the map's activities and tracks."""

import re
from datetime import UTC, date, datetime
from typing import Any

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, HTTPException, NotFoundException
from litestar.params import FromPath, FromQuery

from skintrackr.clients.blob_store import InvalidRangeError, tracks_key
from skintrackr.clients.intervals import activity_gpx, merge_streams
from skintrackr.models import converter
from skintrackr.server.auth import authenticate_request
from skintrackr.server.errors import fail_with
from skintrackr.server.ingest import ingest_activity, start_import
from skintrackr.server.services import get_services
from skintrackr.tracks import PMTILES_MEDIA_TYPE, map_track_ids
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


@get("/api/activities")
async def list_activities(request: Request, state: State) -> dict[str, Any]:
    """The athlete's activity index (in no particular order) and which of them are on the map, for its popups
    and filters: `{"activities", "on_map": [id, ...] (newest first), "map_tracks_omitted"}`."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    with fail_with("failed to load activities", athlete_id=info.athlete_id):
        activities = await services.store.activities(info.athlete_id)
        tracks = map_track_ids(activities, await services.blob_store.track_activity_ids(info.athlete_id))
    return {
        "activities": converter.unstructure(activities),
        "on_map": tracks.included,
        "map_tracks_omitted": tracks.omitted,
    }


@get("/api/tracks.pmtiles")
async def map_tracks(request: Request, state: State) -> Response[bytes]:
    """The athlete's map tracks (PMTiles), whole or the byte range the `Range` header asks for.

    404 until the athlete has a track. The ETag changes with every rebuild, which the map's PMTiles client
    watches for.
    """
    services = get_services(state)
    info = await authenticate_request(request, services)
    with fail_with("failed to read map tracks", athlete_id=info.athlete_id):
        try:
            tracks = await services.blob_store.get_range(tracks_key(info.athlete_id), request.headers.get("range"))
        except InvalidRangeError as e:
            raise HTTPException(status_code=416, detail="range not satisfiable") from e
    if tracks is None:
        raise NotFoundException("no map tracks yet")
    # private, no-cache: browsers revalidate, and never share one athlete's tracks with another
    headers = {"Accept-Ranges": "bytes", "ETag": tracks.etag, "Cache-Control": "private, no-cache"}
    if tracks.content_range:
        headers["Content-Range"] = tracks.content_range
    return Response(
        tracks.body,
        media_type=PMTILES_MEDIA_TYPE,
        status_code=206 if tracks.content_range else 200,
        headers=headers,
    )


api_handlers = [export_track, persist_activity, import_start, import_status, vert_stats, list_activities, map_tracks]
