"""Password hashing and access tokens.

Passwords are hashed with scrypt and a random salt. An access token is a JWT (HS256, signed with
`AUTH_SECRET`) that lives `ACCESS_TOKEN_TTL_SECONDS`, so checking one needs no database lookup.
Longer sessions come from refresh tokens (`app.repo.refresh_tokens`), not from long-lived JWTs.
"""

import hashlib
import hmac
import secrets
import time

import jwt

from app.config import get_settings
from app.constants import ACCESS_TOKEN_TTL_SECONDS, JWT_ALGORITHM, JWT_ISSUER

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(actual.hex(), digest)


def issue_token(user_id: str) -> str:
    now = int(time.time())
    claims = {"sub": user_id, "iss": JWT_ISSUER, "iat": now, "exp": now + ACCESS_TOKEN_TTL_SECONDS}
    return jwt.encode(claims, get_settings().auth_secret, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> str | None:
    """The token's user id, or None if it is malformed, forged or expired."""
    try:
        claims = jwt.decode(
            token,
            get_settings().auth_secret,
            algorithms=[JWT_ALGORITHM],  # pinned: never trust the token's own `alg`
            issuer=JWT_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
    except jwt.InvalidTokenError:
        return None
    sub = claims["sub"]
    return sub if isinstance(sub, str) and sub else None
