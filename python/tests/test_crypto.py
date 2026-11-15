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

# Fixed outputs (made with SECRET) of the formats that are stored or handed out: they must keep working.
STORED_CIPHERTEXT = "xf3tNgVZs3cAJL8H9eGxn++KP7OdBxygteiJmWkgzMH1SFC3BVzPdec6vw3Fjq4n9ts4iDMcr/mS"
ISSUED_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdGhsZXRlX2lkIjoiaTQyNDIiLCJleHBpcmVzX2F0Ijo0OTQ4MjI4MDA1LCJqdGkiOiJkN2M3"
    "OTVlNzhiOGJkMjgwMjU4ZmE3NTkzYTA5Zjg3Yzc0ZmUwNDQ3OGU5YTE1YjlmNzU4ZmIyMWQ0MDcwYjYyIiwiZXhwIjo0OTQ4MjI4MDA1LCJpYXQi"
    "OjE3OTQ2MjgwMDUsIm5iZiI6MTc5NDYyODAwNX0.8LTaK0EtrK8UYQmH5uYbF6eqrJaJ-3ZGbcGde-55Igs"
)
ISSUED_JTI = "d7c795e78b8bd280258fa7593a09f87c74fe04478e9a15b9f758fb21d4070b62"

# --- encryption


@pytest.mark.parametrize("plaintext", ["", "intervals-api-key", "unicode ✓ ünïcödé", "x" * 10_000])
def test_encrypt_round_trip(plaintext: str) -> None:
    assert decrypt(encrypt(plaintext, SECRET), SECRET) == plaintext


def test_encrypt_uses_a_fresh_nonce() -> None:
    assert encrypt("same", SECRET) != encrypt("same", SECRET)


def test_decrypts_stored_ciphertext() -> None:
    assert decrypt(STORED_CIPHERTEXT, SECRET) == "intervals-api-key"


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
    token, claims = generate_jwt(ATHLETE := "i7", SECRET, timedelta(hours=1), now=1_700_000_000)
    assert claims.athlete_id == ATHLETE
    assert claims.issued_at == 1_700_000_000
    assert claims.expires_at == 1_700_003_600
    assert len(claims.jti) == 64
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload == {
        "athlete_id": "i7",
        "expires_at": 1_700_003_600,
        "jti": claims.jti,
        "exp": 1_700_003_600,
        "iat": 1_700_000_000,
        "nbf": 1_700_000_000,
    }
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_jwt_round_trip() -> None:
    token, claims = generate_jwt("i7", SECRET, timedelta(hours=1))
    assert verify_jwt(token, SECRET) == claims


def test_jtis_are_unique() -> None:
    assert generate_jwt("i7", SECRET, timedelta(hours=1))[1].jti != generate_jwt("i7", SECRET, timedelta(hours=1))[1].jti


def test_verifies_issued_jwt() -> None:
    claims = verify_jwt(ISSUED_JWT, SECRET)
    assert (claims.athlete_id, claims.jti) == ("i4242", ISSUED_JTI)


def test_verify_rejects_wrong_secret() -> None:
    token, _ = generate_jwt("i7", SECRET, timedelta(hours=1))
    with pytest.raises(InvalidTokenError):
        verify_jwt(token, OTHER_SECRET)


def test_verify_rejects_expired_token() -> None:
    token, _ = generate_jwt("i7", SECRET, timedelta(hours=1), now=time.time() - 7200)
    with pytest.raises(InvalidTokenError, match="expired"):
        verify_jwt(token, SECRET)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", ISSUED_JWT[:-4]])
def test_verify_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        verify_jwt(token, SECRET)


def test_verify_rejects_unsupported_algorithm() -> None:
    token = jwt.encode({"athlete_id": "i7", "expires_at": 1, "jti": "x"}, "", algorithm="none")
    with pytest.raises(InvalidTokenError):
        verify_jwt(token, SECRET)


@pytest.mark.parametrize("athlete_id", [None, "", 4242])
def test_verify_requires_claims(athlete_id: object) -> None:
    now = int(time.time())
    claims = {"exp": now + 60, "iat": now, "expires_at": now + 60, "jti": "x", "athlete_id": athlete_id}
    token = jwt.encode({k: v for k, v in claims.items() if v is not None}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError, match="missing required claims"):
        verify_jwt(token, SECRET)
