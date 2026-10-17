"""Encryption for third-party tokens stored in MongoDB (e.g. a user's Inkitt access token).

Fernet (AES-128-CBC + HMAC-SHA256). The key comes from `SECRETS_KEY`, or from `AUTH_SECRET` when
that is unset; changing it makes stored tokens unreadable, and users have to enter them again.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.secrets_key or settings.auth_secret
    key = hashlib.sha256(b"whatif-token-encryption:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    """The plain text, or None if `token` was encrypted with another key or tampered with."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
