"""Which Inkitt story each book or storyline was sent to, so it is never sent twice."""

from typing import Literal

from pymongo import ASCENDING

from app.constants import INKITT_STORIES_COLLECTION
from app.repo._mongo import collection
from app.repo.models import utcnow

SourceKind = Literal["book", "storyline"]


def _col():
    return collection(INKITT_STORIES_COLLECTION)


def _id(kind: SourceKind, source_id: str) -> str:
    return f"{kind}:{source_id}"


def ensure_indexes() -> None:
    _col().create_index([("user_id", ASCENDING)])


def get_story_id(kind: SourceKind, source_id: str) -> int | None:
    doc = _col().find_one({"_id": _id(kind, source_id)})
    return doc["story_id"] if doc else None


def save(kind: SourceKind, source_id: str, user_id: str | None, story_id: int) -> None:
    _col().update_one(
        {"_id": _id(kind, source_id)},
        {"$set": {"story_id": story_id, "user_id": user_id, "created_at": utcnow()}},
        upsert=True,
    )
