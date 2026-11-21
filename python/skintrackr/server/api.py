"""Authenticated activity API: GPX export, archiving, bulk imports, vert stats, the map's activities and tracks, and
CalTopo exports of an area's tracks."""

import asyncio
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
from cattrs import BaseValidationError

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, HTTPException, NotFoundException
from litestar.params import FromPath, FromQuery

from skintrackr.caltopo import CaltopoDocument, ExportTooLarge, export_lines, export_points
from skintrackr.clients.blob_store import InvalidRangeError, tracks_key
from skintrackr.clients.intervals import activity_gpx, merge_streams
from skintrackr.logging import get_logger
from skintrackr.models import ActivitySummary, ExportPreview, TrackExportRequest, converter, loads
from skintrackr.server.auth import authenticate_request
from skintrackr.server.errors import fail_with
from skintrackr.server.ingest import ingest_activity, start_import
from skintrackr.server.services import Services, get_services
from skintrackr.tracks import (
    EXPORT_POINTS_LIMIT,
    EXPORT_READ_CONCURRENCY,
    EXPORT_TRACKS_LIMIT,
    PMTILES_MEDIA_TYPE,
    BBox,
    TrackFilters,
    area_track_ids,
    map_track_ids,
    prefilter_margin_m,
    prefilter_zoom,
    tiles_covering,
)
from skintrackr.vert import year_to_date

logger = get_logger(__name__)

GPX_MEDIA_TYPE = "application/gpx+xml"
GEOJSON_MEDIA_TYPE = "application/geo+json"
EXPORT_MODES = ("whole", "clip")
EXPORT_UNITS = ("ft", "m")
MAX_FOLDER_NAME = 100
MAX_STROKE_WIDTH = 20
NO_TRACKS = "No tracks in this area match the map's filters."
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


async def _export_request(request: Request) -> tuple[TrackExportRequest, BBox]:
    """The export request in the body, validated, and its area.

    Raises:
        ClientException: The body isn't a valid export request.
    """
    try:
        export = loads(await request.body(), TrackExportRequest)
    except (ValueError, TypeError, BaseValidationError) as e:
        raise ClientException("invalid export request") from e
    try:
        bbox = BBox.from_list(export.bbox)
    except ValueError as e:
        raise ClientException("invalid area") from e
    export.folder = (export.folder or "").strip()[:MAX_FOLDER_NAME] or None
    if export.mode not in EXPORT_MODES or export.units not in EXPORT_UNITS:
        raise ClientException("invalid export request")
    if not 1 <= export.stroke_width <= MAX_STROKE_WIDTH:
        raise ClientException("invalid export request")
    if export.activity_ids is not None:
        export.activity_ids = list(dict.fromkeys(export.activity_ids))
    return export, bbox


async def export_candidates(services: Services, athlete_id: str, export: TrackExportRequest, bbox: BBox) -> list[str]:
    """The map's tracks that pass the export's filters and may meet its area, newest first.

    Read from the athlete's map archive (`tracks.pmtiles`): the tiles covering the area, at the deepest zoom where
    they're few, with the area grown by how far those tiles' simplified tracks can be from the full ones. So no
    track in the area is missed, and only a track passing within a few metres of it can be extra.
    """
    zoom = prefilter_zoom(bbox)
    equatorward = 0.0 if bbox.south <= 0 <= bbox.north else min(abs(bbox.south), abs(bbox.north))
    grown = bbox.expanded(prefilter_margin_m(zoom, equatorward))
    tiles = await services.blob_store.read_track_tiles(athlete_id, tiles_covering(grown, zoom))
    filters = TrackFilters(sports=frozenset(export.sports), date_from=export.date_from, date_to=export.date_to)
    return await asyncio.to_thread(area_track_ids, tiles, grown, filters)


@post("/api/activities/export/preview", status_code=200)
async def export_preview(request: Request, state: State) -> dict[str, Any]:
    """The tracks an export of an area would hold: `{"activity_ids": [...] (newest first), "limit"}`."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    export, bbox = await _export_request(request)
    with fail_with("failed to find tracks", athlete_id=info.athlete_id):
        ids = await export_candidates(services, info.athlete_id, export, bbox)
    return converter.unstructure(ExportPreview(activity_ids=ids, limit=EXPORT_TRACKS_LIMIT))


@post("/api/activities/export", status_code=200)
async def export_tracks(request: Request, state: State) -> Response[bytes]:
    """Download the full-detail tracks in an area (or the chosen ones, `activity_ids`) as a CalTopo GeoJSON file
    (`X-Track-Count`: how many)."""
    services = get_services(state)
    info = await authenticate_request(request, services)
    export, bbox = await _export_request(request)
    context = {"athlete_id": info.athlete_id, "mode": export.mode}
    started = time.monotonic()

    with fail_with("failed to find tracks", **context):
        # chosen ids need no ownership check: they're looked up in the athlete's own index and archive
        ids = export.activity_ids
        if ids is None:
            ids = await export_candidates(services, info.athlete_id, export, bbox)
        index = {a.id: a for a in await services.store.activities(info.athlete_id)}
    if not ids:
        raise ClientException(NO_TRACKS)
    if len(ids) > EXPORT_TRACKS_LIMIT:
        narrow = "Choose fewer tracks." if export.activity_ids is not None else "Narrow the area or the map's filters."
        raise ClientException(f"{len(ids)} tracks: exports are limited to {EXPORT_TRACKS_LIMIT}. {narrow}")

    slots = asyncio.Semaphore(EXPORT_READ_CONCURRENCY)
    points = 0

    async def build(activity_id: str) -> tuple[ActivitySummary, list[np.ndarray]] | None:
        nonlocal points
        summary = index.get(activity_id)
        async with slots:
            archived = await services.blob_store.read_activity(info.athlete_id, activity_id)
            if summary is None or archived is None:  # deleted since the map's archive was built
                return None
            activity, streams = archived
            lines = await asyncio.to_thread(lambda: export_lines(export_points(activity, streams), bbox, export.mode))
        points += sum(len(line) for line in lines)
        if points > EXPORT_POINTS_LIMIT:
            raise ExportTooLarge(points, EXPORT_POINTS_LIMIT)
        return (summary, lines) if lines else None

    document = CaltopoDocument(info.athlete_id, export.folder, EXPORT_POINTS_LIMIT)
    with fail_with("failed to export tracks", **context):
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(build(activity_id)) for activity_id in ids]
            built = [result for task in tasks if (result := task.result())]
            for summary, lines in sorted(built, key=lambda b: (b[0].start_date_local, b[0].id)):
                document.add_track(
                    summary,
                    lines,
                    export.styles.get(summary.type),
                    units=export.units,
                    stroke_width=export.stroke_width,
                    clipped_to=bbox if export.mode == "clip" else None,
                )
        except* ExportTooLarge:
            raise ClientException(
                f"This export is too large (more than {EXPORT_POINTS_LIMIT:,} points). Clip to the area, or narrow it "
                "or the filters."
            ) from None
    if not document.tracks:
        raise ClientException(NO_TRACKS)

    body = document.to_bytes()
    logger.info(
        "exported tracks",
        extra={
            **context,
            "tracks": document.tracks,
            "points": document.points,
            "bytes": len(body),
            "seconds": round(time.monotonic() - started, 2),
        },
    )
    filename = f"skintrackr-tracks-{datetime.now(UTC).date().isoformat()}.json"
    return Response(
        body,
        media_type=GEOJSON_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Track-Count": str(document.tracks),
        },
    )


api_handlers = [
    export_track,
    persist_activity,
    import_start,
    import_status,
    vert_stats,
    list_activities,
    map_tracks,
    export_preview,
    export_tracks,
]
