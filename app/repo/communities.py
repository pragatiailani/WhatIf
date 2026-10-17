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


def replace_book(book_id: str, found: list[Community]) -> None:
    """Make `found` the book's communities, dropping earlier ones."""
    _col().delete_many({"book_id": book_id})
    insert_communities(found)


def get_communities(book_id: str, cids: list[int]) -> list[Community]:
    """The given communities, in the order of `cids`."""
    docs = {d["cid"]: d for d in _col().find({"book_id": book_id, "cid": {"$in": list(cids)}})}
    return [Community.model_validate(docs[c]) for c in cids if c in docs]


def for_entities(book_id: str, keys: list[str]) -> list[Community]:
    """Level-0 communities containing any of `keys`, those with more of them first."""
    docs = _col().find({"book_id": book_id, "level": 0, "entity_keys": {"$in": list(keys)}})
    found = [Community.model_validate(d) for d in docs]
    wanted = set(keys)
    return sorted(found, key=lambda c: (-len(wanted & set(c.entity_keys)), c.cid))
