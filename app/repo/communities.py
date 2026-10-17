from pymongo import ASCENDING

from app.constants import COMMUNITIES_COLLECTION
from app.repo._mongo import collection
from app.repo.models import Community


def _col():
    return collection(COMMUNITIES_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("book_id", ASCENDING), ("cid", ASCENDING)], unique=True)


def insert_communities(communities: list[Community]) -> None:
    if communities:
        _col().insert_many([c.to_mongo() for c in communities])


def list_communities(book_id: str) -> list[Community]:
    """All canon communities of a book (a few dozen at most, so callers rank them in memory)."""
    return [Community.model_validate(d) for d in _col().find({"book_id": book_id}).sort("cid", ASCENDING)]
