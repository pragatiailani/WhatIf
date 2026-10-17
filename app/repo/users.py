from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.constants import USERS_COLLECTION
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
