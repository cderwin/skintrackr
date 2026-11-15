"""Issuing skintrackr's API tokens (JWTs) and authenticating requests with them.

A request carries its token as a bearer token (`/token/callback` issues those, for the CLI) or in the
session cookie (set by `/oauth2/callback`, for the browser).
"""

import time
from datetime import timedelta
from typing import Self

from attrs import frozen
from litestar import Request
from litestar.datastructures import Cookie
from litestar.exceptions import HTTPException, NotAuthorizedException, PermissionDeniedException

from skintrackr.crypto import InvalidTokenError, TokenClaims, generate_jwt, verify_jwt
from skintrackr.logging import get_logger
from skintrackr.server.errors import fail_with
from skintrackr.server.services import Services

logger = get_logger(__name__)

TOKEN_LIFETIME = timedelta(days=30)
SESSION_COOKIE = "skintrackr_session"


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


def request_token(request: Request) -> str:
    """The request's API token: its bearer token, or else its session cookie.

    Raises:
        NotAuthorizedException: (401) Neither is present, or the header isn't a bearer token.
    """
    if "authorization" not in request.headers and (cookie := request.cookies.get(SESSION_COOKIE)):
        return cookie
    return bearer_token(request)


def session_cookie(token: str | None) -> Cookie:
    """The session cookie holding `token`; `None` makes a cookie that clears it.

    HttpOnly keeps page scripts from reading it, and SameSite=Lax keeps other sites' POSTs from sending it.
    """
    max_age = int(TOKEN_LIFETIME.total_seconds()) if token else 0
    return Cookie(key=SESSION_COOKIE, value=token or "", max_age=max_age, secure=True, httponly=True, samesite="lax")


async def issue_token(services: Services, athlete_id: int) -> str:
    """Sign a new API token for an athlete and record it (so it can be revoked); returns the JWT.

    Raises:
        HTTPException: (500) Signing or recording it failed.
    """
    with fail_with("Failed to generate access token"):
        jwt, claims = generate_jwt(athlete_id, services.config.secret, TOKEN_LIFETIME)
    with fail_with("Failed to save token metadata", athlete_id=athlete_id):
        await services.store.save_jwt(claims)
    return jwt


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
    """Authenticate a request by its API token (`request_token`); every protected handler calls this first.

    Raises:
        NotAuthorizedException: (401) No token.
        PermissionDeniedException: (403) The token is invalid, expired or revoked.
    """
    token = request_token(request)
    try:
        return await authenticate_token(services, token)
    except AuthenticationError as e:
        raise PermissionDeniedException(str(e)) from e


async def authenticate_request_401(request: Request, services: Services) -> AuthTokenInfo:
    """`authenticate_request`, but answering every failure with 401 (the token endpoints' contract)."""
    try:
        return await authenticate_request(request, services)
    except HTTPException as e:
        raise NotAuthorizedException(e.detail) from e
