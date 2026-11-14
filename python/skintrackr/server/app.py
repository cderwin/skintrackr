"""The Litestar app: routes, the frontend, startup and shutdown.

Routes:

| Route | |
| --- | --- |
| `GET /assets/*` | the frontend's hashed bundles (cached for a year) |
| any other `GET` | a file from the frontend build, else its `index.html` (the SPA routes the path) |
| `GET /healthcheck` | `{"ok": true}` |
| `GET /oauth2/connect`, `GET /oauth2/callback` | connect a Strava account (`oauth2.py`) |
| `GET/POST /subscriptions/callback` | Strava webhooks (`webhooks.py`) |
| `GET /token/new`, `GET /token/callback`, `POST /token/verify`, `POST /token/revoke`, `GET /api/strava-token` | API tokens (`token_api.py`) |
| `GET /api/activity/{id}/export`, `POST /api/activity/{id}/persist` | activities (`api.py`) |
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
from skintrackr.clients.strava import StravaClient
from skintrackr.config import Config
from skintrackr.logging import configure_logging, get_logger
from skintrackr.server.api import api_handlers
from skintrackr.server.errors import http_exception_handler, internal_error_handler
from skintrackr.server.oauth2 import oauth2_router
from skintrackr.server.services import Services
from skintrackr.server.token_api import token_api_handlers
from skintrackr.server.webhooks import establish_subscriptions, webhook_handlers

logger = get_logger(__name__)

# the frontend's production build (`just web-build`)
DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"
# Vite names bundles under assets/ by content hash, so they never change
ASSETS_CACHE_CONTROL = CacheControlHeader(max_age=365 * 24 * 3600, public=True, immutable=True)
STRAVA_TIMEOUT = httpx.Timeout(30.0)


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
    subscribe: bool = True,
) -> Litestar:
    """Build the app. Anything not passed is built from the environment at startup.

    Args:
        config: Server settings; `Config.from_env()` if omitted.
        redis: Redis client (with `decode_responses=True`); connects to `config.redis_url` if omitted.
        http: HTTP client for Strava requests.
        blob_store: Activity archive; built from `config.blob_storage` if omitted.
        frontend_dir: The frontend build to serve; `$SKINTRACKR_FRONTEND_DIR` or the repo's `frontend/dist/`.
        subscribe: Make sure a Strava webhook subscription exists, in the background, at startup.
    """
    configure_logging()
    frontend_dir = (frontend_dir or Path(os.environ.get("SKINTRACKR_FRONTEND_DIR", DEFAULT_FRONTEND_DIR))).resolve()

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncGenerator[None]:
        app_config = config or Config.from_env()
        redis_client = redis or Redis.from_url(app_config.redis_url, decode_responses=True)
        http_client = http or httpx.AsyncClient(timeout=STRAVA_TIMEOUT)
        strava = StravaClient(
            http=http_client,
            client_id=app_config.strava_client_id,
            client_secret=app_config.strava_client_secret,
            debug_dir=Path.cwd() if app_config.debug_strava_responses else None,
        )
        services = Services(
            config=app_config,
            store=Store(redis=redis_client, secret=app_config.secret, strava=strava),
            strava=strava,
            blob_store=blob_store or BlobStore.build(app_config.blob_storage),
        )
        app.state.services = services
        if subscribe:
            logger.info("Establishing subscriptions in background")
            services.spawn(establish_subscriptions(services), name="establish-subscriptions")
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
            oauth2_router,
            *webhook_handlers,
            *token_api_handlers,
            *api_handlers,
        ],
        lifespan=[lifespan],
        exception_handlers={HTTPException: http_exception_handler, Exception: internal_error_handler},
        logging_config=None,
        openapi_config=None,
    )
