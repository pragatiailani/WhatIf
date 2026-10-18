"""Refresh tokens: only a SHA-256 of each is stored, so a database leak cannot be replayed.

A token is single use: `consume` deletes it, and the caller issues a new one (rotation).
MongoDB removes expired rows itself through the TTL index.
"""

import hashlib
import secrets
from datetime import timedelta

from pymongo import ASCENDING

from app.constants import REFRESH_TOKEN_TTL_SECONDS, REFRESH_TOKENS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import utcnow


def _col():
    return collection(REFRESH_TOKENS_COLLECTION)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_indexes() -> None:
    _col().create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    _col().create_index([("user_id", ASCENDING)])


def issue(user_id: str) -> str:
    """A new refresh token for `user_id`; the plain value is returned once and never stored."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    _col().insert_one({
        "_id": _hash(token),
        "user_id": user_id,
        "created_at": now,
        "expires_at": now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
    })  # fmt: skip
    return token


def consume(token: str) -> str | None:
    """The token's user id, or None if it is unknown or expired. A valid token is used up."""
    doc = _col().find_one_and_delete({"_id": _hash(token)})
    if doc is None:
        return None
    expires_at = doc["expires_at"]
    if expires_at.tzinfo is None:  # Mongo returns naive UTC unless the client is tz-aware
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
    return doc["user_id"] if expires_at > utcnow() else None


def revoke(token: str) -> None:
    _col().delete_one({"_id": _hash(token)})
