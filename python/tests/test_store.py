import asyncio
import time
from typing import Any

import pytest

from skintrackr.clients.store import NotFoundError, Store
from skintrackr.clients.strava import StravaError
from skintrackr.crypto import TokenClaims, decrypt
from skintrackr.models import ActivityMap, ActivitySummary, ImportStatus, TokenInfo
from tests.conftest import ATHLETE_ID, SECRET, FakeStrava
from tests.test_crypto import GO_CIPHERTEXT

TOKEN = TokenInfo(access_token="access", refresh_token="refresh", expires_at=int(time.time()) + 3600)


# --- Strava tokens


def test_save_and_fetch_token(store: Store) -> None:
    async def run() -> None:
        await store.save_token(ATHLETE_ID, TOKEN)
        assert await store.fetch_token_info(ATHLETE_ID) == TOKEN
        assert await store.fetch_token(ATHLETE_ID) == "access"

    asyncio.run(run())


def test_tokens_are_encrypted_at_rest(store: Store) -> None:
    async def run() -> dict[Any, Any]:
        await store.save_token(ATHLETE_ID, TOKEN)
        return await store.redis.hgetall(f"athlete:{ATHLETE_ID}:strava-token")

    stored = asyncio.run(run())
    assert set(stored) == {"access_token", "refresh_token", "expires_at"}
    assert stored["access_token"] != "access" and decrypt(stored["access_token"], SECRET) == "access"
    assert stored["expires_at"] == str(TOKEN.expires_at)


def test_reads_tokens_written_by_go(store: Store) -> None:
    async def run() -> TokenInfo:
        await store.redis.hset(
            f"athlete:{ATHLETE_ID}:strava-token",
            mapping={"access_token": GO_CIPHERTEXT, "refresh_token": GO_CIPHERTEXT, "expires_at": str(TOKEN.expires_at)},
        )
        return await store.fetch_token_info(ATHLETE_ID)

    assert asyncio.run(run()).access_token == "strava-access-token"


def test_fetch_unknown_athlete(store: Store) -> None:
    with pytest.raises(NotFoundError):
        asyncio.run(store.fetch_token(1))


def test_expired_token_is_refreshed_and_saved(store: Store, strava: FakeStrava) -> None:
    new_expiry = int(time.time()) + 21600
    strava.add(
        "POST",
        "/oauth/token",
        {"access_token": "new-access", "refresh_token": "new-refresh", "expires_at": new_expiry},
    )

    async def run() -> tuple[TokenInfo, TokenInfo]:
        await store.save_token(ATHLETE_ID, TokenInfo("old-access", "old-refresh", int(time.time()) - 1))
        return await store.fetch_token_info(ATHLETE_ID), await store.fetch_token_info(ATHLETE_ID)

    refreshed, stored = asyncio.run(run())
    assert refreshed == stored == TokenInfo("new-access", "new-refresh", new_expiry)
    assert len(strava.requests) == 1  # the saved token is used the second time
    assert "refresh_token=old-refresh" in strava.requests[0].content.decode()


def test_failed_refresh_raises(store: Store) -> None:
    async def run() -> None:
        await store.save_token(ATHLETE_ID, TokenInfo("old-access", "old-refresh", int(time.time()) - 1))
        await store.fetch_token(ATHLETE_ID)

    with pytest.raises(StravaError):
        asyncio.run(run())


# --- OAuth state


def test_oauth_state_is_single_use(store: Store) -> None:
    async def run() -> tuple[str, int, bool, bool]:
        state = await store.save_oauth_state()
        ttl = await store.redis.ttl(f"oauth:state:{state}")
        return state, ttl, await store.consume_oauth_state(state), await store.consume_oauth_state(state)

    state, ttl, first, second = asyncio.run(run())
    assert len(state) == 64
    assert 590 <= ttl <= 600
    assert (first, second) == (True, False)


def test_unknown_oauth_state(store: Store) -> None:
    assert asyncio.run(store.consume_oauth_state("nope")) is False


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
        "athlete_id": str(ATHLETE_ID),
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
    first = ActivitySummary(id=1, name="Skin", sport_type="BackcountrySki", map=ActivityMap(summary_polyline="abc"))
    second = ActivitySummary(id=2, total_elevation_gain=12.5)

    async def run() -> None:
        await store.save_activities(ATHLETE_ID, [first, second])
        await store.save_activities(ATHLETE_ID, [])  # nothing to write
        renamed = ActivitySummary(id=1, name="Renamed")
        await store.save_activities(ATHLETE_ID, [renamed])
        assert sorted(await store.activities(ATHLETE_ID), key=lambda a: a.id) == [renamed, second]
        await store.delete_activity(ATHLETE_ID, 2)
        assert await store.activities(ATHLETE_ID) == [renamed]
        assert await store.activities(ATHLETE_ID + 1) == []

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
        await store.save_token(ATHLETE_ID, TOKEN)
        await store.save_activities(ATHLETE_ID, [ActivitySummary(id=1)])
        await store.save_import(ATHLETE_ID, ImportStatus(state="running"))
        await store.save_token(ATHLETE_ID + 1, TOKEN)

        await store.delete_athlete(ATHLETE_ID)
        assert await store.activities(ATHLETE_ID) == []
        assert await store.load_import(ATHLETE_ID) == ImportStatus()
        assert await store.running_imports() == []
        with pytest.raises(NotFoundError):
            await store.fetch_token(ATHLETE_ID)
        assert await store.fetch_token(ATHLETE_ID + 1) == "access"

    asyncio.run(run())

