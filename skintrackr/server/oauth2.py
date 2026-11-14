"""Connecting a Strava account: `/oauth2/connect` and its callback.

The callback stores the athlete's tokens and hands the access token to the static confirmation page in the
URL fragment, which browsers never send back to a server.
"""

from urllib.parse import urlencode

from litestar import Router, get
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import FromQuery
from litestar.response import Redirect

from skintrackr.logging import get_logger
from skintrackr.models import TokenResponse
from skintrackr.server.errors import fail_with
from skintrackr.server.services import Services, get_services

logger = get_logger(__name__)

CALLBACK_PATH = "oauth2/callback"
CONFIRMATION_PAGE = "/static/confirmation.html"


def confirmation_url(token: TokenResponse) -> str:
    """The confirmation page, with the Strava token in its fragment."""
    fragment = urlencode(
        {"access_token": token.access_token, "athlete_id": token.athlete.id, "expires_at": token.expires_at}
    )
    return f"{CONFIRMATION_PAGE}#{fragment}"


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
    """Store the athlete's tokens and show the confirmation page."""
    token = await exchange_and_save(get_services(state), code, "oauth2 callback")
    return Redirect(confirmation_url(token))


oauth2_router = Router("/oauth2", route_handlers=[connect, callback])
