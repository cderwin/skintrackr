import time
from datetime import timedelta

import jwt
import pytest

from skintrackr.crypto import (
    DecryptionError,
    InvalidTokenError,
    decrypt,
    encrypt,
    generate_jwt,
    secret_key_from_hex,
    verify_jwt,
)
from tests.conftest import SECRET

OTHER_SECRET = "f" * 64

# Made by the Go server (app.Encrypt / app.GenerateJWT) with SECRET, to check the two interoperate.
GO_CIPHERTEXT = "YOMgslYTlfnKzcRSzbrJelz4nSvWeqqBFqxwyP6VtC8dMEjZNkJRhUaQo3cR0Ii2n8cwVLkNqATMfmw="
GO_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdGhsZXRlX2lkIjo0MjQyLCJleHBpcmVzX2F0Ijo0OTQ4MjI4MDA1LCJqdGkiOiIzN2Y3Y2Q2"
    "ZmM3NDYwOWE1MGU1OWNkODdiMGRmODg5Yjg5YWQ2Y2FiNWE5NGM5NjcxNDIyZTc2OTJkZTczNjY5IiwiZXhwIjo0OTQ4MjI4MDA1LCJuYmYiOjE3"
    "OTQ2MjgwMDUsImlhdCI6MTc5NDYyODAwNX0.2EJlkEz9xwQAmoZCxQLoBaj9bbNrn6lEMpfICX2OSp8"
)
GO_JTI = "37f7cd6fc74609a50e59cd87b0df889b89ad6cab5a94c9671422e7692de73669"


# --- encryption


@pytest.mark.parametrize("plaintext", ["", "strava-token", "unicode ✓ ünïcödé", "x" * 10_000])
def test_encrypt_round_trip(plaintext: str) -> None:
    assert decrypt(encrypt(plaintext, SECRET), SECRET) == plaintext


def test_encrypt_uses_a_fresh_nonce() -> None:
    assert encrypt("same", SECRET) != encrypt("same", SECRET)


def test_decrypts_go_ciphertext() -> None:
    assert decrypt(GO_CIPHERTEXT, SECRET) == "strava-access-token"


def test_decrypt_with_wrong_secret_fails() -> None:
    with pytest.raises(DecryptionError, match="invalid secret or corrupted data"):
        decrypt(encrypt("secret", SECRET), OTHER_SECRET)


@pytest.mark.parametrize(
    ("ciphertext", "message"),
    [("not base64!", "failed to decode base64"), ("c2hvcnQ=", "ciphertext too short")],
)
def test_decrypt_rejects_malformed_ciphertext(ciphertext: str, message: str) -> None:
    with pytest.raises(DecryptionError, match=message):
        decrypt(ciphertext, SECRET)


def test_secret_key_uses_first_32_bytes() -> None:
    assert secret_key_from_hex(SECRET + "aa") == bytes.fromhex(SECRET)


@pytest.mark.parametrize(
    ("secret", "message"),
    [("ab" * 31, "at least 32 bytes, got 31"), ("zz" * 32, "failed to decode hex")],
)
def test_secret_key_rejects_invalid_secrets(secret: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        secret_key_from_hex(secret)


# --- JWTs


def test_generate_jwt_claims() -> None:
    token, claims = generate_jwt(ATHLETE := 7, SECRET, timedelta(hours=1), now=1_700_000_000)
    assert claims.athlete_id == ATHLETE
    assert claims.issued_at == 1_700_000_000
    assert claims.expires_at == 1_700_003_600
    assert len(claims.jti) == 64
    payload = jwt.decode(token, options={"verify_signature": False})
    # the same claims the Go server signs
    assert payload == {
        "athlete_id": 7,
        "expires_at": 1_700_003_600,
        "jti": claims.jti,
        "exp": 1_700_003_600,
        "iat": 1_700_000_000,
        "nbf": 1_700_000_000,
    }
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_jwt_round_trip() -> None:
    token, claims = generate_jwt(7, SECRET, timedelta(hours=1))
    assert verify_jwt(token, SECRET) == claims


def test_jtis_are_unique() -> None:
    assert generate_jwt(7, SECRET, timedelta(hours=1))[1].jti != generate_jwt(7, SECRET, timedelta(hours=1))[1].jti


def test_verifies_go_jwt() -> None:
    claims = verify_jwt(GO_JWT, SECRET)
    assert (claims.athlete_id, claims.jti) == (4242, GO_JTI)


def test_verify_rejects_wrong_secret() -> None:
    token, _ = generate_jwt(7, SECRET, timedelta(hours=1))
    with pytest.raises(InvalidTokenError):
        verify_jwt(token, OTHER_SECRET)


def test_verify_rejects_expired_token() -> None:
    token, _ = generate_jwt(7, SECRET, timedelta(hours=1), now=time.time() - 7200)
    with pytest.raises(InvalidTokenError, match="expired"):
        verify_jwt(token, SECRET)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", GO_JWT[:-4]])
def test_verify_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        verify_jwt(token, SECRET)


def test_verify_rejects_unsupported_algorithm() -> None:
    token = jwt.encode({"athlete_id": 7, "expires_at": 1, "jti": "x"}, "", algorithm="none")
    with pytest.raises(InvalidTokenError):
        verify_jwt(token, SECRET)


def test_verify_requires_claims() -> None:
    now = int(time.time())
    token = jwt.encode({"exp": now + 60, "iat": now}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError, match="missing required claims"):
        verify_jwt(token, SECRET)
