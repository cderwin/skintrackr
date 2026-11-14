"""Store: every Redis operation (Strava tokens, OAuth state, API token revocation).

Keys match those the former Go server wrote, so existing Redis data keeps working:

| Key | Contents |
| --- | --- |
| `athlete:{id}:strava-token` | hash of encrypted `access_token`/`refresh_token` and `expires_at` |
| `oauth:state:{state}` | issue time; expires after 10 minutes |
| `jwt:jti:{jti}` | hash of `athlete_id`, `issued_at`, `expires_at`; expires with the token |
| `jwt:revoked:{jti}` | revocation time; expires with the token |
"""

import secrets
import time

from attrs import field, frozen
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from skintrackr.clients.strava import StravaClient
from skintrackr.crypto import TokenClaims, decrypt, encrypt
from skintrackr.logging import get_logger
from skintrackr.models import TokenInfo

logger = get_logger(__name__)

OAUTH_STATE_TTL_SECONDS = 10 * 60
_TOKEN_FIELDS = ("access_token", "refresh_token", "expires_at")


class NotFoundError(LookupError):
    """What was asked for isn't stored (or has expired)."""


def _text(value: bytes | str) -> str:
    # the client decodes responses, but redis-py types them as bytes or str
    return value.decode() if isinstance(value, bytes) else value


def _token_key(athlete_id: int) -> str:
    return f"athlete:{athlete_id}:strava-token"


def _jwt_key(jti: str) -> str:
    return f"jwt:jti:{jti}"


def _revoked_key(jti: str) -> str:
    return f"jwt:revoked:{jti}"


@frozen
class Store:
    """Redis-backed storage.

    Attributes:
        redis: Client created with `decode_responses=True`.
        secret: Hex-encoded key stored tokens are encrypted with (`APP_SECRET`).
        strava: App-level Strava client, used to refresh expired tokens.
    """

    redis: Redis
    secret: str = field(repr=False)
    strava: StravaClient

    # --- Strava tokens

    async def fetch_token(self, athlete_id: int) -> str:
        """An athlete's current Strava access token, refreshed first if it has expired."""
        return (await self.fetch_token_info(athlete_id)).access_token

    async def fetch_token_info(self, athlete_id: int) -> TokenInfo:
        """An athlete's Strava tokens, refreshed (and saved) first if the access token has expired.

        Raises:
            NotFoundError: No token is stored for the athlete.
            DecryptionError: The stored token can't be decrypted with this secret.
            StravaError: The token had expired and refreshing it failed.
        """
        access_token, refresh_token, expires_at = await self.redis.hmget(_token_key(athlete_id), list(_TOKEN_FIELDS))
        if access_token is None or refresh_token is None or expires_at is None:
            logger.error("fetch token error: athlete not found", extra={"athlete_id": athlete_id})
            raise NotFoundError(f"no Strava token stored for athlete {athlete_id}")

        token = TokenInfo(
            access_token=decrypt(_text(access_token), self.secret),
            refresh_token=decrypt(_text(refresh_token), self.secret),
            expires_at=int(expires_at),
        )
        if token.expires_at < time.time():
            logger.info("token expired, refreshing token", extra={"athlete_id": athlete_id})
            token = (await self.strava.refresh_token(token.refresh_token)).token_info()
            try:
                await self.save_token(athlete_id, token)
            except Exception:
                # the new token still works for this request; the next one refreshes again
                logger.exception("error saving refreshed token", extra={"athlete_id": athlete_id})
        return token

    async def save_token(self, athlete_id: int, token: TokenInfo) -> None:
        """Store an athlete's Strava tokens, encrypted."""
        mapping: dict[FieldT, EncodableT] = {
            "access_token": encrypt(token.access_token, self.secret),
            "refresh_token": encrypt(token.refresh_token, self.secret),
            "expires_at": str(token.expires_at),
        }
        await self.redis.hset(_token_key(athlete_id), mapping=mapping)
        logger.info("saved new token", extra={"athlete_id": athlete_id})

    # --- OAuth state (CSRF protection)

    async def save_oauth_state(self) -> str:
        """Create a single-use OAuth state token, valid for 10 minutes."""
        state = secrets.token_hex(32)
        await self.redis.set(f"oauth:state:{state}", int(time.time()), ex=OAUTH_STATE_TTL_SECONDS)
        return state

    async def consume_oauth_state(self, state: str) -> bool:
        """Check a state token from an OAuth callback and delete it; False if unknown or expired."""
        return await self.redis.getdel(f"oauth:state:{state}") is not None

    # --- API tokens

    async def save_jwt(self, claims: TokenClaims) -> None:
        """Record an issued API token, so it can be revoked; the record expires with the token.

        Raises:
            ValueError: The token has already expired.
        """
        ttl = claims.expires_at - int(time.time())
        if ttl <= 0:
            raise ValueError("token already expired")
        mapping: dict[FieldT, EncodableT] = {"athlete_id": claims.athlete_id, "issued_at": claims.issued_at, "expires_at": claims.expires_at}
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(_jwt_key(claims.jti), mapping=mapping)
            pipe.expire(_jwt_key(claims.jti), ttl)
            await pipe.execute()
        logger.info("saved JWT token metadata", extra={"jti": claims.jti, "athlete_id": claims.athlete_id})

    async def revoke_jwt(self, jti: str) -> None:
        """Revoke an issued API token until it would have expired.

        Raises:
            NotFoundError: The token was never issued, or has expired.
        """
        ttl = await self.redis.ttl(_jwt_key(jti))
        if ttl == -2:  # no such key
            raise NotFoundError("token not found or already expired")
        await self.redis.set(_revoked_key(jti), int(time.time()), ex=ttl if ttl > 0 else None)
        logger.info("revoked JWT token", extra={"jti": jti})

    async def is_jwt_revoked(self, jti: str) -> bool:
        """Whether an API token has been revoked."""
        return await self.redis.exists(_revoked_key(jti)) > 0
