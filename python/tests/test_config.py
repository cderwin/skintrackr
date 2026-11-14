import pytest

from skintrackr.config import (
    DEFAULT_BASE_URL,
    DEFAULT_BUCKET_NAME,
    DEFAULT_S3_ENDPOINT,
    Config,
    ConfigError,
    debug_strava_responses,
)
from tests.conftest import SECRET

ENV = {
    "APP_SECRET": SECRET,
    "STRAVA_CLIENT_ID": "id",
    "STRAVA_CLIENT_SECRET": "client-secret",
    "UPSTASH_REDIS_URL": "redis://localhost:6379",
    "AWS_ACCESS_KEY_ID": "key",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
}


def test_from_env() -> None:
    config = Config.from_env({**ENV, "APP_BASE_URL": "https://skintrackr.fly.dev"})
    assert config.base_url == "https://skintrackr.fly.dev"
    assert (config.strava_client_id, config.strava_client_secret) == ("id", "client-secret")
    assert config.redis_url == "redis://localhost:6379"
    assert config.secret == SECRET
    assert config.blob_storage.access_key_id == "key"
    assert config.blob_storage.secret_access_key == "aws-secret"
    assert config.blob_storage.bucket_name == DEFAULT_BUCKET_NAME
    assert config.blob_storage.endpoint_url == DEFAULT_S3_ENDPOINT
    assert len(config.verify_token) == 32
    assert not config.debug_strava_responses


def test_defaults_and_random_verify_token() -> None:
    first, second = Config.from_env(ENV), Config.from_env(ENV)
    assert first.base_url == DEFAULT_BASE_URL
    assert first.verify_token != second.verify_token


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
    assert SECRET not in text and "client-secret" not in text and "aws-secret" not in text


@pytest.mark.parametrize(("value", "expected"), [("", False), ("false", False), ("FALSE", False), ("true", True), ("1", True)])
def test_debug_strava_responses(value: str, expected: bool) -> None:
    assert debug_strava_responses({"DEBUG_STRAVA_RESPONSE_BODY": value}) is expected


@pytest.mark.parametrize("base_url", ["https://x.test", "https://x.test/"])
def test_url(base_url: str) -> None:
    config = Config.from_env({**ENV, "APP_BASE_URL": base_url})
    assert config.url("oauth2/callback") == "https://x.test/oauth2/callback"
    assert config.url("/subscriptions/callback") == "https://x.test/subscriptions/callback"
