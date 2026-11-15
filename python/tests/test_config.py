from datetime import timedelta

import pytest

from skintrackr.clients.intervals import DEFAULT_LIMITS
from skintrackr.config import DEFAULT_BUCKET_NAME, DEFAULT_S3_ENDPOINT, Config, ConfigError
from tests.conftest import SECRET

ENV = {
    "APP_SECRET": SECRET,
    "UPSTASH_REDIS_URL": "redis://localhost:6379",
    "AWS_ACCESS_KEY_ID": "key",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
}


def test_from_env() -> None:
    config = Config.from_env(ENV)
    assert config.redis_url == "redis://localhost:6379"
    assert config.secret == SECRET
    assert config.blob_storage.access_key_id == "key"
    assert config.blob_storage.secret_access_key == "aws-secret"
    assert config.blob_storage.bucket_name == DEFAULT_BUCKET_NAME
    assert config.blob_storage.endpoint_url == DEFAULT_S3_ENDPOINT
    assert config.rate_limits == DEFAULT_LIMITS
    assert (config.sync_interval, config.sync_window) == (timedelta(minutes=15), timedelta(days=30))
    assert not config.debug_responses


def test_optional_settings() -> None:
    config = Config.from_env(
        {
            **ENV,
            "INTERVALS_RATE_LIMIT_15MIN": "100",
            "INTERVALS_RATE_LIMIT_DAILY": "1000",
            "SYNC_INTERVAL_MINUTES": "5",
            "SYNC_WINDOW_DAYS": "7",
        }
    )
    assert config.rate_limits == (100, 1000)
    assert (config.sync_interval, config.sync_window) == (timedelta(minutes=5), timedelta(days=7))


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_rejects_invalid_numbers(value: str) -> None:
    with pytest.raises(ConfigError, match="SYNC_WINDOW_DAYS must be a positive integer"):
        Config.from_env({**ENV, "SYNC_WINDOW_DAYS": value})


@pytest.mark.parametrize("missing", list(ENV))
def test_from_env_requires(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        Config.from_env(env)


def test_from_env_rejects_short_secret() -> None:
    with pytest.raises(ConfigError, match="APP_SECRET is invalid"):
        Config.from_env({**ENV, "APP_SECRET": "abcd"})


def test_secrets_are_not_in_repr() -> None:
    text = repr(Config.from_env(ENV))
    assert SECRET not in text and "aws-secret" not in text and "redis://" not in text


@pytest.mark.parametrize(("value", "expected"), [("", False), ("false", False), ("FALSE", False), ("true", True), ("1", True)])
def test_debug_responses(value: str, expected: bool) -> None:
    assert Config.from_env({**ENV, "DEBUG_INTERVALS_RESPONSE_BODY": value}).debug_responses is expected
