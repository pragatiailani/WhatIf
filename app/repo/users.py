from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.constants import USERS_COLLECTION
from app.crypto import decrypt, encrypt
from app.repo._mongo import collection
from app.repo.models import User


class EmailTaken(Exception):
    """A user with that email already exists."""


def _col():
    return collection(USERS_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("email", ASCENDING)], unique=True)


def create_user(user: User) -> User:
    try:
        _col().insert_one(user.to_mongo())
    except DuplicateKeyError as exc:
        raise EmailTaken(user.email) from exc
    return user


def get_user(user_id: str) -> User | None:
    doc = _col().find_one({"_id": user_id})
    return User.model_validate(doc) if doc else None


def find_by_email(email: str) -> User | None:
    doc = _col().find_one({"email": email})
    return User.model_validate(doc) if doc else None


def set_inkitt_pat(user_id: str, pat: str | None) -> None:
    """Store the user's Inkitt token encrypted, or forget it with None."""
    _col().update_one({"_id": user_id}, {"$set": {"inkitt_pat": encrypt(pat) if pat else None}})


def get_inkitt_pat(user_id: str) -> str | None:
    """The user's Inkitt token in plain text, or None if unset or no longer decryptable."""
    doc = _col().find_one({"_id": user_id}, projection=["inkitt_pat"])
    stored = doc.get("inkitt_pat") if doc else None
    return decrypt(stored) if stored else None
