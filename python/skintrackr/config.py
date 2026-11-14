"""Configuration from environment variables (see the README)."""

import os
import secrets
from collections.abc import Mapping
from typing import Self

from attrs import field, frozen

from skintrackr.crypto import secret_key_from_hex

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_BUCKET_NAME = "skintrackr-storage"
DEFAULT_S3_ENDPOINT = "https://t3.storage.dev"


class ConfigError(Exception):
    """A required setting is missing or invalid."""


def _require(env: Mapping[str, str], *names: str) -> list[str]:
    values = [env.get(name, "") for name in names]
    if not all(values):
        raise ConfigError(f"{' and '.join(names)} must be set")
    return values


def debug_strava_responses(env: Mapping[str, str] | None = None) -> bool:
    """Whether `DEBUG_STRAVA_RESPONSE_BODY` asks for Strava response bodies to be saved (any value but "false")."""
    value = (os.environ if env is None else env).get("DEBUG_STRAVA_RESPONSE_BODY", "")
    return value.lower() not in ("", "false")


@frozen
class BlobStorageConfig:
    """S3-compatible blob storage settings.

    Attributes:
        access_key_id: `AWS_ACCESS_KEY_ID`.
        secret_access_key: `AWS_SECRET_ACCESS_KEY`.
        bucket_name: Bucket activities are archived to.
        endpoint_url: S3 API endpoint.
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
        return cls(access_key_id=access_key_id, secret_access_key=secret_access_key)


@frozen
class Config:
    """Server settings.

    Attributes:
        blob_storage: Where activities are archived.
        base_url: Public URL of the server, for OAuth and webhook callbacks (`APP_BASE_URL`).
        strava_client_id: Strava API application id (`STRAVA_CLIENT_ID`).
        strava_client_secret: Strava API application secret (`STRAVA_CLIENT_SECRET`).
        redis_url: Redis connection URL (`UPSTASH_REDIS_URL`).
        secret: Hex-encoded key, at least 32 bytes, that signs JWTs and encrypts stored tokens (`APP_SECRET`).
        verify_token: Random token Strava echoes back when verifying the webhook subscription; new each start.
        debug_strava_responses: Save every Strava response body to the working directory
            (`DEBUG_STRAVA_RESPONSE_BODY`).
    """

    blob_storage: BlobStorageConfig
    strava_client_id: str
    strava_client_secret: str = field(repr=False)
    redis_url: str = field(repr=False)
    secret: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    verify_token: str = field(factory=lambda: secrets.token_hex(16), repr=False)
    debug_strava_responses: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Load from the environment; raises ConfigError if a required setting is missing or invalid."""
        env = os.environ if env is None else env
        (secret,) = _require(env, "APP_SECRET")
        try:
            secret_key_from_hex(secret)
        except ValueError as e:
            raise ConfigError(f"APP_SECRET is invalid: {e}") from e
        client_id, client_secret = _require(env, "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET")
        (redis_url,) = _require(env, "UPSTASH_REDIS_URL")
        return cls(
            blob_storage=BlobStorageConfig.from_env(env),
            base_url=env.get("APP_BASE_URL") or DEFAULT_BASE_URL,
            strava_client_id=client_id,
            strava_client_secret=client_secret,
            redis_url=redis_url,
            secret=secret,
            debug_strava_responses=debug_strava_responses(env),
        )

    def url(self, path: str) -> str:
        """An absolute URL on this server, e.g. `url("oauth2/callback")`."""
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
