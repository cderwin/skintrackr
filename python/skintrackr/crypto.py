"""JWT signing/verification and symmetric encryption of stored tokens.

Both are wire-compatible with the Go server, so either can read what the other wrote:

- JWTs are HS256 over the raw `APP_SECRET` string, with the claims `athlete_id`, `expires_at`, `jti`, `exp`,
  `iat` and `nbf`.
- Encryption is NaCl secretbox keyed by the first 32 bytes of hex-decoded `APP_SECRET`; the ciphertext is
  base64(nonce || box), the layout of Go's `secretbox.Seal(nonce[:], ...)`.
"""

import base64
import binascii
import secrets
import time
from datetime import timedelta

import jwt
from attrs import frozen
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

JWT_ALGORITHM = "HS256"
# Go's verifier accepts any HMAC signing method
_ACCEPTED_ALGORITHMS = ["HS256", "HS384", "HS512"]


class InvalidTokenError(Exception):
    """A JWT is malformed, wrongly signed, expired or missing claims."""


class DecryptionError(ValueError):
    """Ciphertext could not be decrypted: wrong secret or corrupted data."""


@frozen
class TokenClaims:
    """The claims of a skintrackr API token.

    Attributes:
        athlete_id: Strava athlete the token acts for.
        issued_at: Unix time the token was issued.
        expires_at: Unix time the token expires.
        jti: Unique token id, the key for revocation.
    """

    athlete_id: int
    issued_at: int
    expires_at: int
    jti: str


def generate_jti() -> str:
    """A random JWT id (32 bytes, hex encoded)."""
    return secrets.token_hex(32)


def generate_jwt(athlete_id: int, secret: str, lifetime: timedelta, now: float | None = None) -> tuple[str, TokenClaims]:
    """Sign a new API token.

    Args:
        athlete_id: Athlete the token acts for.
        secret: Signing secret (`APP_SECRET`).
        lifetime: How long the token is valid for.
        now: Issue time (Unix seconds); defaults to the current time.

    Returns:
        The encoded token and its claims.
    """
    issued_at = int(time.time() if now is None else now)
    claims = TokenClaims(
        athlete_id=athlete_id,
        issued_at=issued_at,
        expires_at=issued_at + int(lifetime.total_seconds()),
        jti=generate_jti(),
    )
    payload = {
        "athlete_id": claims.athlete_id,
        "expires_at": claims.expires_at,
        "jti": claims.jti,
        "exp": claims.expires_at,
        "iat": claims.issued_at,
        "nbf": claims.issued_at,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), claims


def verify_jwt(token: str, secret: str) -> TokenClaims:
    """Check a token's signature and validity window, and return its claims.

    Raises:
        InvalidTokenError: The token is malformed, wrongly signed, expired, not yet valid or missing claims.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=_ACCEPTED_ALGORITHMS)
    except jwt.PyJWTError as e:
        raise InvalidTokenError(str(e)) from e
    athlete_id, expires_at, issued_at = (_int_claim(payload, k) for k in ("athlete_id", "expires_at", "iat"))
    jti = payload.get("jti")
    if athlete_id is None or expires_at is None or issued_at is None or not isinstance(jti, str) or not jti:
        raise InvalidTokenError("token is missing required claims")
    return TokenClaims(athlete_id=athlete_id, issued_at=issued_at, expires_at=expires_at, jti=jti)


def _int_claim(payload: dict[str, object], name: str) -> int | None:
    value = payload.get(name)
    # bool is an int subclass; never accept it as a number
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def secret_key_from_hex(hex_secret: str) -> bytes:
    """The 32-byte encryption key: the first 32 bytes of a hex-encoded secret.

    Raises:
        ValueError: The secret isn't hex, or decodes to fewer than 32 bytes.
    """
    try:
        decoded = bytes.fromhex(hex_secret)
    except ValueError as e:
        raise ValueError(f"failed to decode hex string: {e}") from e
    if len(decoded) < SecretBox.KEY_SIZE:
        raise ValueError(f"secret must be at least {SecretBox.KEY_SIZE} bytes, got {len(decoded)} bytes")
    return decoded[: SecretBox.KEY_SIZE]


def encrypt(plaintext: str, hex_secret: str) -> str:
    """Encrypt a string with a random nonce; returns base64(nonce || box)."""
    box = SecretBox(secret_key_from_hex(hex_secret))
    return base64.b64encode(box.encrypt(plaintext.encode())).decode()


def decrypt(ciphertext: str, hex_secret: str) -> str:
    """Decrypt a string from `encrypt` (or the Go server's `Encrypt`).

    Raises:
        DecryptionError: The ciphertext isn't valid base64, is too short, or fails authentication.
        ValueError: The secret is invalid.
    """
    try:
        data = base64.b64decode(ciphertext, validate=True)
    except binascii.Error as e:
        raise DecryptionError(f"failed to decode base64: {e}") from e
    if len(data) < SecretBox.NONCE_SIZE:
        raise DecryptionError("ciphertext too short")
    box = SecretBox(secret_key_from_hex(hex_secret))
    try:
        return box.decrypt(data).decode()
    except CryptoError as e:
        raise DecryptionError("decryption failed: invalid secret or corrupted data") from e
