"""Password hashing and sign-in tokens. Standard library only.

Passwords are hashed with scrypt and a random salt. A token is `<user_id>.<expiry>.<signature>`,
signed with HMAC-SHA256 over `AUTH_SECRET`, so checking one needs no database lookup.
"""

import hashlib
import hmac
import secrets
import time

from app.config import get_settings
from app.constants import AUTH_TOKEN_TTL_SECONDS

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(actual.hex(), digest)


def _sign(payload: str) -> str:
    key = get_settings().auth_secret.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: str) -> str:
    payload = f"{user_id}.{int(time.time()) + AUTH_TOKEN_TTL_SECONDS}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """The token's user id, or None if it is malformed, forged or expired."""
    payload, _, signature = token.rpartition(".")
    user_id, _, expiry = payload.partition(".")
    if not (user_id and expiry.isdigit() and hmac.compare_digest(_sign(payload), signature)):
        return None
    return user_id if int(expiry) > time.time() else None
