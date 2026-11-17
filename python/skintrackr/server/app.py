"""The Litestar app: routes, the frontend, startup and shutdown.

Routes:

| Route | |
| --- | --- |
| `GET /assets/*` | the frontend's hashed bundles (cached for a year) |
| any other `GET` | a file from the frontend build, else its `index.html` (the SPA routes the path) |
| `GET /healthcheck` | `{"ok": true}` |
| `POST /auth/connect`, `POST /auth/logout`, `POST /auth/disconnect` | connect an intervals.icu account by its API key, sign the browser in and out, disconnect (`connect.py`) |
| `POST /token/new`, `POST /token/verify`, `POST /token/revoke` | API tokens (`token_api.py`) |
| `GET /api/activity/{id}/export`, `POST /api/activity/{id}/persist`, `GET/POST /api/import`, `GET /api/stats/vert` | activities (`api.py`) |
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from litestar import Litestar, Request, get
from litestar.datastructures import CacheControlHeader
from litestar.exceptions import HTTPException
from litestar.response import File
from litestar.static_files import create_static_files_router
from redis.asyncio import Redis

from skintrackr.clients.blob_store import BlobStore
from skintrackr.clients.store import Store
from skintrackr.clients.intervals import HTTP_TIMEOUT
from skintrackr.config import Config
from skintrackr.logging import configure_logging, get_logger
from skintrackr.server.api import api_handlers
from skintrackr.server.connect import connect_router
from skintrackr.server.errors import http_exception_handler, internal_error_handler
from skintrackr.server.services import Services, build_intervals
from skintrackr.server.token_api import token_api_handlers
from skintrackr.server.ingest import resume_imports, start_sync_loop

logger = get_logger(__name__)

# the frontend's production build (`just web-build`)
DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"
# Vite names bundles under assets/ by content hash, so they never change
ASSETS_CACHE_CONTROL = CacheControlHeader(max_age=365 * 24 * 3600, public=True, immutable=True)


@get("/healthcheck")
async def healthcheck() -> dict[str, bool]:
    """Liveness check."""
    return {"ok": True}


def create_app(
    config: Config | None = None,
    *,
    redis: Redis | None = None,
    http: httpx.AsyncClient | None = None,
    blob_store: BlobStore | None = None,
    frontend_dir: Path | None = None,
    startup_tasks: bool = True,
) -> Litestar:
    """Build the app. Anything not passed is built from the environment at startup.

    Args:
        config: Server settings; `Config.from_env()` if omitted.
        redis: Redis client (with `decode_responses=True`); connects to `config.redis_url` if omitted.
        http: HTTP client for intervals.icu requests.
        blob_store: Activity archive; built from `config.blob_storage` if omitted.
        frontend_dir: The frontend build to serve; `$SKINTRACKR_FRONTEND_DIR` or the repo's `frontend/dist/`.
        startup_tasks: At startup, in the background: start the periodic sync of connected athletes, and resume
            the imports that were running when the server stopped.
    """
    configure_logging()
    frontend_dir = (frontend_dir or Path(os.environ.get("SKINTRACKR_FRONTEND_DIR", DEFAULT_FRONTEND_DIR))).resolve()

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncGenerator[None]:
        app_config = config or Config.from_env()
        redis_client = redis or Redis.from_url(app_config.redis_url, decode_responses=True)
        http_client = http or httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        services = Services(
            config=app_config,
            store=Store(redis=redis_client, secret=app_config.secret),
            intervals=build_intervals(app_config, http_client),
            blob_store=blob_store or BlobStore.build(app_config.blob_storage),
        )
        app.state.services = services
        if startup_tasks:
            logger.info("Starting the sync loop and resuming imports in background")
            start_sync_loop(services)
            services.spawn(resume_imports(services), name="resume-imports")
        try:
            yield
        finally:
            await services.cancel_background()
            await http_client.aclose()
            await redis_client.aclose()

    @get(["/", "/{path:path}"], include_in_schema=False)
    async def frontend(request: Request) -> File:
        """A file from the frontend build (e.g. favicon.svg), or the SPA's index.html for any other path."""
        file = (frontend_dir / request.path_params.get("path", "").lstrip("/")).resolve()
        if not (file.is_relative_to(frontend_dir) and file.is_file()):
            file = frontend_dir / "index.html"
        # no-cache: browsers revalidate, so they pick up a new build's index.html (and its new bundles)
        return File(file, filename=file.name, content_disposition_type="inline", headers={"Cache-Control": "no-cache"})

    # logging_config=None keeps the JSON format from skintrackr.logging
    return Litestar(
        route_handlers=[
            healthcheck,
            frontend,
            create_static_files_router(
                "/assets", directories=[frontend_dir / "assets"], cache_control=ASSETS_CACHE_CONTROL
            ),
            connect_router,
            *token_api_handlers,
            *api_handlers,
        ],
        lifespan=[lifespan],
        exception_handlers={HTTPException: http_exception_handler, Exception: internal_error_handler},
        logging_config=None,
        openapi_config=None,
    )
