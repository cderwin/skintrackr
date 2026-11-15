"""Connecting an intervals.icu account in the browser: `/auth/connect`, `/auth/logout` and `/auth/disconnect`.

Connecting checks the athlete's API key with intervals.icu, stores it encrypted, and signs the browser in with a
session cookie holding a skintrackr API token. The token API (`/token/new`) connects the same way.
"""

from typing import Any

from litestar import Request, Response, Router, post
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import JSONBody

from skintrackr.clients.intervals import IntervalsError
from skintrackr.logging import get_logger
from skintrackr.models import Athlete
from skintrackr.server.auth import (
    SESSION_COOKIE,
    AuthenticationError,
    authenticate_request,
    authenticate_token,
    issue_token,
    session_cookie,
)
from skintrackr.server.errors import fail_with
from skintrackr.server.ingest import remove_athlete
from skintrackr.server.services import Services, get_services

logger = get_logger(__name__)

INVALID_KEY_MESSAGE = "intervals.icu rejected the API key"


async def connect_athlete(services: Services, data: dict[str, Any], flow: str) -> Athlete:
    """Check a request's intervals.icu API key (`{"api_key": ...}`) and store it for its athlete.

    Shared by both ways of connecting (this one and the token API's).

    Args:
        services: Shared services.
        data: The request body.
        flow: Which flow this is, for logging.

    Raises:
        ClientException: (400) No API key, or intervals.icu rejected it.
        HTTPException: (500) Checking or saving the key failed.
    """
    api_key = data.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ClientException("api_key is required")
    api_key = api_key.strip()
    with fail_with("Failed to check the API key with intervals.icu"):
        try:
            athlete = await services.intervals.with_api_key(api_key).get_athlete()
        except IntervalsError as e:
            if e.unauthorized:
                raise ClientException(INVALID_KEY_MESSAGE) from e
            raise
    if not athlete.id:
        raise ClientException(INVALID_KEY_MESSAGE)
    logger.info(f"API key checked for {flow}", extra={"athlete_id": athlete.id})
    with fail_with("Failed to save the API key", athlete_id=athlete.id):
        await services.store.save_api_key(athlete.id, api_key)
    return athlete


@post("/connect", status_code=200)
async def connect(state: State, data: JSONBody[dict[str, Any]]) -> Response[dict[str, str]]:
    """Connect an intervals.icu account by its API key and sign the browser in."""
    services = get_services(state)
    athlete = await connect_athlete(services, data, "browser")
    jwt = await issue_token(services, athlete.id)
    return Response({"athlete_id": athlete.id, "name": athlete.name}, cookies=[session_cookie(jwt)])


@post("/logout", status_code=200)
async def logout(request: Request, state: State) -> Response[dict[str, bool]]:
    """Sign the browser out: revoke its session token (if it's still valid) and clear the cookie."""
    services = get_services(state)
    if token := request.cookies.get(SESSION_COOKIE):
        try:
            info = await authenticate_token(services, token)
        except AuthenticationError:
            info = None  # already invalid: nothing to revoke
        if info is not None:
            with fail_with("Failed to revoke token", jti=info.jti):
                await services.store.revoke_jwt(info.jti)
    return Response({"ok": True}, cookies=[session_cookie(None)])


@post("/disconnect", status_code=200)
async def disconnect(request: Request, state: State) -> Response[dict[str, bool]]:
    """Disconnect the athlete: delete everything stored for them (API key, archive, index), revoke the request's
    token and clear the session cookie. Their other API tokens stay valid but can't reach intervals.icu any more.
    """
    services = get_services(state)
    info = await authenticate_request(request, services)
    with fail_with("Failed to delete athlete data", athlete_id=info.athlete_id):
        await remove_athlete(services, info.athlete_id)
    with fail_with("Failed to revoke token", jti=info.jti):
        await services.store.revoke_jwt(info.jti)
    return Response({"ok": True}, cookies=[session_cookie(None)])


connect_router = Router("/auth", route_handlers=[connect, logout, disconnect])
