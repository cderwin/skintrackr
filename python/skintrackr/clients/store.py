"""Store: every Redis operation (intervals.icu API keys, API tokens, activity index, imports).

| Key | Contents |
| --- | --- |
| `athlete:{id}:intervals-key` | the athlete's intervals.icu API key, encrypted |
| `athletes:connected` | set of athlete ids with a stored API key (synced periodically) |
| `jwt:jti:{jti}` | hash of `athlete_id`, `issued_at`, `expires_at`; expires with the token |
| `jwt:revoked:{jti}` | revocation time; expires with the token |
| `athlete:{id}:activities` | hash of activity id -> `ActivitySummary` JSON (rebuildable from the bucket) |
| `athlete:{id}:strava` | hash of activity id -> `StravaMatch` JSON, from the athlete's Strava export (rebuildable from the bucket) |
| `athlete:{id}:import` | `ImportStatus` JSON |
| `imports:running` | set of athlete ids with an import running (resumed at startup) |
"""

import time
from collections.abc import Iterable

from attrs import field, frozen
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from skintrackr.crypto import TokenClaims, decrypt, encrypt
from skintrackr.logging import get_logger
from skintrackr.models import ActivitySummary, ImportStatus, converter, loads
from skintrackr.strava import StravaMatch

logger = get_logger(__name__)

# every key skintrackr writes (or wrote, in earlier versions) matches one of these
APP_KEY_PATTERNS = ("athlete:*", "athletes:*", "jwt:*", "oauth:*", "imports:*")


class NotFoundError(LookupError):
    """What was asked for isn't stored (or has expired)."""


def _text(value: bytes | str) -> str:
    # the client decodes responses, but redis-py types them as bytes or str
    return value.decode() if isinstance(value, bytes) else value


def _api_key_key(athlete_id: str) -> str:
    return f"athlete:{athlete_id}:intervals-key"


def _activities_key(athlete_id: str) -> str:
    return f"athlete:{athlete_id}:activities"


def _strava_key(athlete_id: str) -> str:
    return f"athlete:{athlete_id}:strava"


def _import_key(athlete_id: str) -> str:
    return f"athlete:{athlete_id}:import"


CONNECTED_KEY = "athletes:connected"
RUNNING_IMPORTS_KEY = "imports:running"


def _jwt_key(jti: str) -> str:
    return f"jwt:jti:{jti}"


def _revoked_key(jti: str) -> str:
    return f"jwt:revoked:{jti}"


@frozen
class Store:
    """Redis-backed storage.

    Attributes:
        redis: Client created with `decode_responses=True`.
        secret: Hex-encoded key stored API keys are encrypted with (`APP_SECRET`).
    """

    redis: Redis
    secret: str = field(repr=False)

    # --- intervals.icu API keys

    async def fetch_api_key(self, athlete_id: str) -> str:
        """An athlete's intervals.icu API key.

        Raises:
            NotFoundError: No key is stored for the athlete.
            DecryptionError: The stored key can't be decrypted with this secret.
        """
        stored = await self.redis.get(_api_key_key(athlete_id))
        if stored is None:
            logger.error("fetch API key error: athlete not found", extra={"athlete_id": athlete_id})
            raise NotFoundError(f"no intervals.icu API key stored for athlete {athlete_id}")
        return decrypt(_text(stored), self.secret)

    async def save_api_key(self, athlete_id: str, api_key: str) -> None:
        """Store an athlete's intervals.icu API key, encrypted, and add them to the connected athletes."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(_api_key_key(athlete_id), encrypt(api_key, self.secret))
            pipe.sadd(CONNECTED_KEY, athlete_id)
            await pipe.execute()
        logger.info("saved API key", extra={"athlete_id": athlete_id})

    async def connected_athletes(self) -> list[str]:
        """Athletes with a stored API key."""
        return sorted(map(_text, await self.redis.smembers(CONNECTED_KEY)))

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

    # --- activity index

    async def save_activities(self, athlete_id: str, activities: Iterable[ActivitySummary]) -> None:
        """Add or replace activities in an athlete's index."""
        mapping: dict[FieldT, EncodableT] = {a.id: converter.dumps(a) for a in activities}
        if mapping:
            await self.redis.hset(_activities_key(athlete_id), mapping=mapping)

    async def activities(self, athlete_id: str) -> list[ActivitySummary]:
        """Every activity in an athlete's index, in no particular order."""
        rows = await self.redis.hvals(_activities_key(athlete_id))
        return [loads(row, ActivitySummary) for row in rows]

    async def delete_activity(self, athlete_id: str, activity_id: str) -> None:
        """Remove an activity from an athlete's index, and its Strava match."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hdel(_activities_key(athlete_id), activity_id)
            pipe.hdel(_strava_key(athlete_id), activity_id)
            await pipe.execute()

    # --- Strava matches

    async def save_strava_matches(self, athlete_id: str, matches: dict[str, StravaMatch]) -> None:
        """Replace an athlete's Strava matches (activity id -> match)."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(_strava_key(athlete_id))
            if matches:
                mapping: dict[FieldT, EncodableT] = {k: converter.dumps(m) for k, m in matches.items()}
                pipe.hset(_strava_key(athlete_id), mapping=mapping)
            await pipe.execute()

    async def strava_matches(self, athlete_id: str) -> dict[str, StravaMatch]:
        """An athlete's Strava matches, by activity id."""
        rows = await self.redis.hgetall(_strava_key(athlete_id))
        return {_text(k): loads(v, StravaMatch) for k, v in rows.items()}

    # --- imports

    async def save_import(self, athlete_id: str, status: ImportStatus) -> None:
        """Record an athlete's import progress, and whether it's running (so it resumes after a restart)."""
        status.updated_at = int(time.time())
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(_import_key(athlete_id), converter.dumps(status))
            if status.state == "running":
                pipe.sadd(RUNNING_IMPORTS_KEY, athlete_id)
            else:
                pipe.srem(RUNNING_IMPORTS_KEY, athlete_id)
            await pipe.execute()

    async def load_import(self, athlete_id: str) -> ImportStatus:
        """An athlete's import progress (`idle` if they never imported)."""
        saved = await self.redis.get(_import_key(athlete_id))
        return loads(saved, ImportStatus) if saved else ImportStatus()

    async def running_imports(self) -> list[str]:
        """Athletes whose import was running (e.g. when the server last stopped)."""
        return sorted(map(_text, await self.redis.smembers(RUNNING_IMPORTS_KEY)))

    # --- athletes

    async def delete_athlete(self, athlete_id: str) -> None:
        """Forget an athlete: their API key, activity index, Strava matches and import progress."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(
                _api_key_key(athlete_id), _activities_key(athlete_id), _strava_key(athlete_id), _import_key(athlete_id)
            )
            pipe.srem(CONNECTED_KEY, athlete_id)
            pipe.srem(RUNNING_IMPORTS_KEY, athlete_id)
            await pipe.execute()
        logger.info("deleted athlete data from redis", extra={"athlete_id": athlete_id})

    # --- everything (migrations)

    async def app_keys(self) -> list[str]:
        """Every key skintrackr has written (`APP_KEY_PATTERNS`); other keys in the database are left out."""
        keys: set[str] = set()
        for pattern in APP_KEY_PATTERNS:
            keys.update([_text(key) async for key in self.redis.scan_iter(match=pattern)])
        return sorted(keys)

    async def delete_everything(self) -> int:
        """Delete every key skintrackr has written; returns how many were deleted."""
        keys = await self.app_keys()
        for start in range(0, len(keys), 500):
            await self.redis.delete(*keys[start : start + 500])
        logger.info("deleted every skintrackr key from redis", extra={"count": len(keys)})
        return len(keys)
