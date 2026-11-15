"""Connecting a Strava account in the browser: `/oauth2/connect`, its callback, and `/oauth2/logout`.

The callback stores the athlete's Strava tokens, signs the browser in with a session cookie holding a
skintrackr API token, and sends it to the frontend's confirmation page.
"""

from litestar import Request, Response, Router, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import FromQuery
from litestar.response import Redirect

from skintrackr.logging import get_logger
from skintrackr.models import TokenResponse
from skintrackr.server.auth import (
    SESSION_COOKIE,
    AuthenticationError,
    authenticate_token,
    issue_token,
    session_cookie,
)
from skintrackr.server.errors import fail_with
from skintrackr.server.services import Services, get_services
from skintrackr.server.webhooks import ensure_subscription

logger = get_logger(__name__)

CALLBACK_PATH = "oauth2/callback"
# the frontend's confirmation page (an SPA route)
CONFIRMATION_PAGE = "/connected"


async def exchange_and_save(services: Services, code: str | None, flow: str) -> TokenResponse:
    """Exchange an OAuth callback's code for the athlete's tokens and store them.

    Shared by both OAuth flows (this one and the token API's).

    Args:
        services: Shared services.
        code: The `code` query parameter.
        flow: Which flow this is, for logging.

    Raises:
        ClientException: (400) No code.
        HTTPException: (500) The exchange or saving the tokens failed.
    """
    if not code:
        raise ClientException("No code in callback")
    with fail_with("Failed to exchange temporary code with strava"):
        token = await services.strava.exchange_code(code)
    logger.info(
        f"Token exchange completed for {flow}",
        extra={"athlete_id": token.athlete.id, "athlete_username": token.athlete.username},
    )
    with fail_with("failed to save token to redis", athlete_id=token.athlete.id):
        await services.store.save_token(token.athlete.id, token.token_info())
    return token


@get("/connect")
async def connect(state: State) -> Redirect:
    """Send the athlete to Strava to authorize skintrackr."""
    services = get_services(state)
    return Redirect(services.strava.authorization_url(services.config.url(CALLBACK_PATH)))


@get("/callback")
async def callback(state: State, code: FromQuery[str | None] = None) -> Redirect:
    """Store the athlete's tokens, sign the browser in, and show the confirmation page.

    The app's one webhook subscription delivers events for every athlete who authorized it, so connecting only
    needs it to exist.
    """
    services = get_services(state)
    token = await exchange_and_save(services, code, "oauth2 callback")
    jwt = await issue_token(services, token.athlete.id)
    ensure_subscription(services)
    return Redirect(CONFIRMATION_PAGE, cookies=[session_cookie(jwt)])


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


oauth2_router = Router("/oauth2", route_handlers=[connect, callback, logout])
