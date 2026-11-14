"""API tokens: issuing them through Strava OAuth, verifying and revoking them, and reading the Strava token.

`/token/new` starts an OAuth flow protected by a single-use `state`; its callback stores the athlete's Strava
tokens and answers with a skintrackr bearer token (a JWT, valid for 30 days) for the `/api` routes.
"""

from datetime import timedelta
from typing import Annotated, Any

from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotAuthorizedException, PermissionDeniedException
from litestar.params import FromQuery, QueryParameter
from litestar.response import Redirect

from skintrackr.crypto import InvalidTokenError, generate_jwt, verify_jwt
from skintrackr.logging import get_logger
from skintrackr.models import converter
from skintrackr.server.auth import authenticate_request_401, bearer_token
from skintrackr.server.errors import fail_with
from skintrackr.server.oauth2 import exchange_and_save
from skintrackr.server.services import get_services

logger = get_logger(__name__)

TOKEN_LIFETIME = timedelta(days=30)
CALLBACK_PATH = "token/callback"


@get("/token/new")
async def token_start(state: State) -> Redirect:
    """Start the OAuth flow that issues an API token."""
    services = get_services(state)
    with fail_with("Failed to initiate OAuth flow"):
        oauth_state = await services.store.save_oauth_state()
    return Redirect(services.strava.authorization_url(services.config.url(CALLBACK_PATH), state=oauth_state))


@get("/token/callback")
async def token_callback(
    state: State, code: FromQuery[str | None] = None, oauth_state: Annotated[str | None, QueryParameter(name="state")] = None
) -> dict[str, Any]:
    """Finish the OAuth flow: store the Strava tokens and issue an API token."""
    # the `state` query parameter is `oauth_state` here: Litestar reserves `state` for the app state
    services = get_services(state)
    if not code:
        raise ClientException("No code in callback")
    if not oauth_state:
        raise ClientException("No state in callback")
    with fail_with("Failed to verify state token"):
        valid_state = await services.store.consume_oauth_state(oauth_state)
    if not valid_state:
        logger.error("invalid OAuth state")
        raise PermissionDeniedException("Invalid or expired state token")

    token = await exchange_and_save(services, code, "token API")
    with fail_with("Failed to generate access token"):
        jwt, claims = generate_jwt(token.athlete.id, services.config.secret, TOKEN_LIFETIME)
    with fail_with("Failed to save token metadata", athlete_id=token.athlete.id):
        await services.store.save_jwt(claims)
    return {"access_token": jwt, "token_type": "Bearer", "athlete_id": token.athlete.id}


@post("/token/verify", status_code=200)
async def token_verify(request: Request, state: State) -> dict[str, Any]:
    """Check the request's API token; answers with its details."""
    info = await authenticate_request_401(request, get_services(state))
    return {
        "valid": info.valid,
        "athlete_id": info.athlete_id,
        "issued_at": info.issued_at,
        "expires_at": info.expires_at,
        "jti": info.jti,
    }


@post("/token/revoke", status_code=200)
async def token_revoke(request: Request, state: State) -> dict[str, Any]:
    """Revoke the request's API token."""
    services = get_services(state)
    try:
        claims = verify_jwt(bearer_token(request), services.config.secret)
    except InvalidTokenError as e:
        logger.error("JWT verification failed for revocation", extra={"err": str(e)})
        raise NotAuthorizedException("Invalid or expired token") from e

    with fail_with("Failed to check token status", jti=claims.jti):
        revoked = await services.store.is_jwt_revoked(claims.jti)
    if revoked:
        raise ClientException("Token is already revoked")
    with fail_with("Failed to revoke token", jti=claims.jti):
        await services.store.revoke_jwt(claims.jti)
    return {"revoked": True, "jti": claims.jti, "athlete_id": claims.athlete_id}


@get("/api/strava-token")
async def strava_token(request: Request, state: State) -> dict[str, Any]:
    """The athlete's Strava access token (refreshed first if it had expired)."""
    services = get_services(state)
    info = await authenticate_request_401(request, services)
    with fail_with("failed to fetch Strava token", athlete_id=info.athlete_id):
        token = await services.store.fetch_token_info(info.athlete_id)
    return converter.unstructure(token)


token_api_handlers = [token_start, token_callback, token_verify, token_revoke, strava_token]
