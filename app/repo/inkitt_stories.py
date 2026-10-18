"""Which Inkitt story each book or storyline was sent to, so it is never sent twice, and how many
of its chapters got there, so an upload that broke off carries on instead of starting over."""

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
    doc = get(kind, source_id)
    return doc["story_id"] if doc else None


def get(kind: SourceKind, source_id: str) -> dict | None:
    """{story_id, chapters_sent, uploaded}. Records from before progress was kept count as
    uploaded."""
    doc = _col().find_one({"_id": _id(kind, source_id)})
    if doc is None:
        return None
    return {"story_id": doc["story_id"], "chapters_sent": doc.get("chapters_sent", 0),
            "uploaded": doc.get("uploaded", True)}  # fmt: skip


def save(kind: SourceKind, source_id: str, user_id: str | None, story_id: int) -> None:
    """A story just created on Inkitt, before any chapter is sent."""
    _col().update_one(
        {"_id": _id(kind, source_id)},
        {"$set": {"story_id": story_id, "user_id": user_id, "created_at": utcnow(),
                  "chapters_sent": 0, "uploaded": False}},  # fmt: skip
        upsert=True,
    )


def set_progress(kind: SourceKind, source_id: str, chapters_sent: int, *, uploaded: bool = False) -> None:
    _col().update_one(
        {"_id": _id(kind, source_id)},
        {"$set": {"chapters_sent": chapters_sent, "uploaded": uploaded}},
    )
