"""Authenticating API requests with skintrackr's bearer tokens (JWTs from `/token/callback`)."""

import time
from typing import Self

from attrs import frozen
from litestar import Request
from litestar.exceptions import HTTPException, NotAuthorizedException, PermissionDeniedException

from skintrackr.crypto import InvalidTokenError, TokenClaims, verify_jwt
from skintrackr.logging import get_logger
from skintrackr.server.services import Services

logger = get_logger(__name__)


class AuthenticationError(Exception):
    """A bearer token is invalid, expired or revoked."""


@frozen
class AuthTokenInfo:
    """A verified API token.

    Attributes:
        athlete_id: Athlete the token acts for.
        issued_at: Unix time the token was issued.
        expires_at: Unix time the token expires.
        jti: Unique token id.
        valid: Always True: invalid tokens raise instead.
    """

    athlete_id: int
    issued_at: int
    expires_at: int
    jti: str
    valid: bool = True

    @classmethod
    def from_claims(cls, claims: TokenClaims) -> Self:
        """The token info for verified claims."""
        return cls(athlete_id=claims.athlete_id, issued_at=claims.issued_at, expires_at=claims.expires_at, jti=claims.jti)


def bearer_token(request: Request) -> str:
    """The token in a request's `Authorization: Bearer <token>` header.

    Raises:
        NotAuthorizedException: (401) The header is missing or isn't a bearer token.
    """
    header = request.headers.get("authorization", "")
    if not header:
        raise NotAuthorizedException("Authorization header required")
    if not header.startswith("Bearer ") or len(header) <= len("Bearer "):
        raise NotAuthorizedException("Invalid authorization format")
    return header.removeprefix("Bearer ")


async def authenticate_token(services: Services, token: str) -> AuthTokenInfo:
    """Verify a token's signature, expiry and revocation status.

    Raises:
        AuthenticationError: The token is invalid, expired or revoked, or its status can't be checked.
    """
    try:
        claims = verify_jwt(token, services.config.secret)
    except InvalidTokenError as e:
        raise AuthenticationError("Invalid or expired token") from e
    if claims.expires_at < time.time():
        raise AuthenticationError("token has expired")
    try:
        revoked = await services.store.is_jwt_revoked(claims.jti)
    except Exception as e:
        logger.error("error checking revocation status", exc_info=e, extra={"jti": claims.jti})
        raise AuthenticationError("failed to verify token revocation status") from e
    if revoked:
        raise AuthenticationError("token has been revoked")
    return AuthTokenInfo.from_claims(claims)


async def authenticate_request(request: Request, services: Services) -> AuthTokenInfo:
    """Authenticate a request by its bearer token; every protected handler calls this first.

    Raises:
        NotAuthorizedException: (401) No bearer token.
        PermissionDeniedException: (403) The token is invalid, expired or revoked.
    """
    token = bearer_token(request)
    try:
        return await authenticate_token(services, token)
    except AuthenticationError as e:
        raise PermissionDeniedException(str(e)) from e


async def authenticate_request_401(request: Request, services: Services) -> AuthTokenInfo:
    """`authenticate_request`, but answering every failure with 401 (as the Go token endpoints do)."""
    try:
        return await authenticate_request(request, services)
    except HTTPException as e:
        raise NotAuthorizedException(e.detail) from e
