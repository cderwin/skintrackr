import asyncio
import time
from typing import Any

import pytest

from skintrackr.clients.store import NotFoundError, Store
from skintrackr.crypto import TokenClaims, decrypt
from skintrackr.models import ActivitySummary, ImportStatus
from tests.conftest import API_KEY, ATHLETE_ID, SECRET

OTHER_ATHLETE = "i9999"

# --- intervals.icu API keys


def test_save_and_fetch_api_key(store: Store) -> None:
    async def run() -> None:
        await store.save_api_key(ATHLETE_ID, API_KEY)
        assert await store.fetch_api_key(ATHLETE_ID) == API_KEY
        assert await store.connected_athletes() == [ATHLETE_ID]

    asyncio.run(run())


def test_api_keys_are_encrypted_at_rest(store: Store) -> None:
    async def run() -> Any:
        await store.save_api_key(ATHLETE_ID, API_KEY)
        return await store.redis.get(f"athlete:{ATHLETE_ID}:intervals-key")

    stored = asyncio.run(run())
    assert stored != API_KEY and decrypt(stored, SECRET) == API_KEY


def test_fetch_unknown_athlete(store: Store) -> None:
    with pytest.raises(NotFoundError):
        asyncio.run(store.fetch_api_key("i1"))


# --- API tokens


def claims(lifetime: int = 3600) -> TokenClaims:
    now = int(time.time())
    return TokenClaims(athlete_id=ATHLETE_ID, issued_at=now, expires_at=now + lifetime, jti="jti-1")


def test_save_jwt(store: Store) -> None:
    token = claims()

    async def run() -> tuple[dict[Any, Any], int]:
        await store.save_jwt(token)
        redis = store.redis
        return await redis.hgetall("jwt:jti:jti-1"), await redis.ttl("jwt:jti:jti-1")

    stored, ttl = asyncio.run(run())
    assert stored == {
        "athlete_id": ATHLETE_ID,
        "issued_at": str(token.issued_at),
        "expires_at": str(token.expires_at),
    }
    assert 3590 <= ttl <= 3600


def test_save_expired_jwt(store: Store) -> None:
    with pytest.raises(ValueError, match="already expired"):
        asyncio.run(store.save_jwt(claims(lifetime=-1)))


def test_revoke_jwt(store: Store) -> None:
    async def run() -> tuple[bool, bool, int]:
        await store.save_jwt(claims())
        before = await store.is_jwt_revoked("jti-1")
        await store.revoke_jwt("jti-1")
        return before, await store.is_jwt_revoked("jti-1"), await store.redis.ttl("jwt:revoked:jti-1")

    before, after, ttl = asyncio.run(run())
    assert (before, after) == (False, True)
    assert 3590 <= ttl <= 3600  # remembered until the token would have expired


def test_revoke_unknown_jwt(store: Store) -> None:
    with pytest.raises(NotFoundError, match="not found or already expired"):
        asyncio.run(store.revoke_jwt("unknown"))


# --- activity index


def test_activity_index(store: Store) -> None:
    first = ActivitySummary(id="i1", name="Skin", type="BackcountrySki")
    second = ActivitySummary(id="i2", total_elevation_gain=12.5)

    async def run() -> None:
        await store.save_activities(ATHLETE_ID, [first, second])
        await store.save_activities(ATHLETE_ID, [])  # nothing to write
        renamed = ActivitySummary(id="i1", name="Renamed")
        await store.save_activities(ATHLETE_ID, [renamed])
        assert sorted(await store.activities(ATHLETE_ID), key=lambda a: a.id) == [renamed, second]
        await store.delete_activity(ATHLETE_ID, "i2")
        assert await store.activities(ATHLETE_ID) == [renamed]
        assert await store.activities(OTHER_ATHLETE) == []

    asyncio.run(run())


# --- imports


def test_import_status_tracks_running_imports(store: Store) -> None:
    async def run() -> None:
        assert await store.load_import(ATHLETE_ID) == ImportStatus()
        await store.save_import(ATHLETE_ID, ImportStatus(state="running", activities=3))
        status = await store.load_import(ATHLETE_ID)
        assert (status.state, status.activities) == ("running", 3)
        assert status.updated_at >= int(time.time()) - 5
        assert await store.running_imports() == [ATHLETE_ID]
        await store.save_import(ATHLETE_ID, ImportStatus(state="done"))
        assert await store.running_imports() == []

    asyncio.run(run())


def test_delete_athlete(store: Store) -> None:
    async def run() -> None:
        await store.save_api_key(ATHLETE_ID, API_KEY)
        await store.save_activities(ATHLETE_ID, [ActivitySummary(id="i1")])
        await store.save_import(ATHLETE_ID, ImportStatus(state="running"))
        await store.save_api_key(OTHER_ATHLETE, "other-key")

        await store.delete_athlete(ATHLETE_ID)
        assert await store.activities(ATHLETE_ID) == []
        assert await store.load_import(ATHLETE_ID) == ImportStatus()
        assert await store.running_imports() == []
        assert await store.connected_athletes() == [OTHER_ATHLETE]
        with pytest.raises(NotFoundError):
            await store.fetch_api_key(ATHLETE_ID)
        assert await store.fetch_api_key(OTHER_ATHLETE) == "other-key"

    asyncio.run(run())



# --- everything (migrations)


def test_delete_everything_leaves_other_keys(store: Store) -> None:
    async def run() -> tuple[list[str], int, list[Any]]:
        await store.save_api_key(ATHLETE_ID, API_KEY)
        await store.save_import(ATHLETE_ID, ImportStatus(state="running"))
        await store.redis.hset(f"athlete:{ATHLETE_ID}:old-token", mapping={"access_token": "old"})  # an earlier version's
        await store.redis.set("oauth:state:abc", 1)
        await store.redis.set("someone-elses-key", 1)
        keys = await store.app_keys()
        deleted = await store.delete_everything()
        return keys, deleted, await store.redis.keys("*")

    keys, deleted, left = asyncio.run(run())
    assert keys == [
        f"athlete:{ATHLETE_ID}:import",
        f"athlete:{ATHLETE_ID}:intervals-key",
        f"athlete:{ATHLETE_ID}:old-token",
        "athletes:connected",
        "imports:running",
        "oauth:state:abc",
    ]
    assert deleted == 6
    assert left == ["someone-elses-key"]
