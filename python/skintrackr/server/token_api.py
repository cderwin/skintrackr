"""API tokens: issuing them for an intervals.icu API key, and verifying and revoking them.

`POST /token/new` checks the athlete's intervals.icu API key, stores it, and answers with a skintrackr bearer
token (a JWT, valid for 30 days) for the `/api` routes.
"""

from typing import Any

from litestar import Request, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotAuthorizedException
from litestar.params import JSONBody

from skintrackr.crypto import InvalidTokenError, verify_jwt
from skintrackr.logging import get_logger
from skintrackr.server.auth import authenticate_request_401, issue_token, request_token
from skintrackr.server.connect import connect_athlete
from skintrackr.server.errors import fail_with
from skintrackr.server.services import get_services

logger = get_logger(__name__)


@post("/token/new", status_code=200)
async def token_new(state: State, data: JSONBody[dict[str, Any]]) -> dict[str, Any]:
    """Connect an intervals.icu account by its API key and issue an API token."""
    services = get_services(state)
    athlete = await connect_athlete(services, data, "token API")
    jwt = await issue_token(services, athlete.id)
    return {"access_token": jwt, "token_type": "Bearer", "athlete_id": athlete.id}


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
        claims = verify_jwt(request_token(request), services.config.secret)
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



token_api_handlers = [token_new, token_verify, token_revoke]
