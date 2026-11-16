"""Configuration from environment variables (see the README)."""

import os
from collections.abc import Mapping
from datetime import timedelta
from typing import Self

from attrs import field, frozen

from skintrackr.clients.intervals import BACKGROUND_SPACING, DEFAULT_LIMITS
from skintrackr.crypto import secret_key_from_hex

DEFAULT_BUCKET_NAME = "skintrackr-storage"
DEFAULT_S3_ENDPOINT = "https://t3.storage.dev"
DEFAULT_SYNC_INTERVAL = timedelta(minutes=15)
DEFAULT_SYNC_WINDOW = timedelta(days=30)


class ConfigError(Exception):
    """A required setting is missing or invalid."""


def _require(env: Mapping[str, str], *names: str) -> list[str]:
    values = [env.get(name, "") for name in names]
    if not all(values):
        raise ConfigError(f"{' and '.join(names)} must be set")
    return values


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name, "")
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return number


@frozen
class BlobStorageConfig:
    """S3-compatible blob storage settings.

    Attributes:
        access_key_id: `AWS_ACCESS_KEY_ID`.
        secret_access_key: `AWS_SECRET_ACCESS_KEY`.
        bucket_name: Bucket activities are archived to (`S3_BUCKET_NAME`).
        endpoint_url: S3 API endpoint (`S3_ENDPOINT_URL`; e.g. the local SeaweedFS in docker-compose.yml).
    """

    access_key_id: str
    secret_access_key: str = field(repr=False)
    bucket_name: str = DEFAULT_BUCKET_NAME
    endpoint_url: str = DEFAULT_S3_ENDPOINT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Load from the environment; raises ConfigError if the credentials are missing."""
        env = os.environ if env is None else env
        access_key_id, secret_access_key = _require(env, "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
        return cls(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=env.get("S3_BUCKET_NAME") or DEFAULT_BUCKET_NAME,
            endpoint_url=env.get("S3_ENDPOINT_URL") or DEFAULT_S3_ENDPOINT,
        )


@frozen
class Config:
    """Server settings.

    Attributes:
        blob_storage: Where activities are archived.
        redis_url: Redis connection URL (`UPSTASH_REDIS_URL`).
        secret: Hex-encoded key, at least 32 bytes, that signs JWTs and encrypts stored API keys (`APP_SECRET`).
        rate_limits: intervals.icu requests allowed per 15 minutes and per day, until a response reports them
            (`INTERVALS_RATE_LIMIT_15MIN`, `INTERVALS_RATE_LIMIT_DAILY`).
        background_spacing: Minimum seconds between background intervals.icu requests.
        sync_interval: How often connected athletes' recent activities are synced (`SYNC_INTERVAL_MINUTES`).
        sync_window: How far back each sync looks, by start date (`SYNC_WINDOW_DAYS`).
        debug_responses: Save every intervals.icu response body to the working directory
            (`DEBUG_INTERVALS_RESPONSE_BODY`).
    """

    blob_storage: BlobStorageConfig
    redis_url: str = field(repr=False)
    secret: str = field(repr=False)
    rate_limits: tuple[int, int] = DEFAULT_LIMITS
    background_spacing: float = BACKGROUND_SPACING
    sync_interval: timedelta = DEFAULT_SYNC_INTERVAL
    sync_window: timedelta = DEFAULT_SYNC_WINDOW
    debug_responses: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Load from the environment; raises ConfigError if a required setting is missing or invalid."""
        env = os.environ if env is None else env
        (secret,) = _require(env, "APP_SECRET")
        try:
            secret_key_from_hex(secret)
        except ValueError as e:
            raise ConfigError(f"APP_SECRET is invalid: {e}") from e
        (redis_url,) = _require(env, "UPSTASH_REDIS_URL")
        return cls(
            blob_storage=BlobStorageConfig.from_env(env),
            redis_url=redis_url,
            secret=secret,
            rate_limits=(
                _positive_int(env, "INTERVALS_RATE_LIMIT_15MIN", DEFAULT_LIMITS[0]),
                _positive_int(env, "INTERVALS_RATE_LIMIT_DAILY", DEFAULT_LIMITS[1]),
            ),
            sync_interval=timedelta(
                minutes=_positive_int(env, "SYNC_INTERVAL_MINUTES", int(DEFAULT_SYNC_INTERVAL.total_seconds() // 60))
            ),
            sync_window=timedelta(days=_positive_int(env, "SYNC_WINDOW_DAYS", DEFAULT_SYNC_WINDOW.days)),
            debug_responses=env.get("DEBUG_INTERVALS_RESPONSE_BODY", "").lower() not in ("", "false"),
        )
