from collections.abc import Sequence

from pymongo import ASCENDING

from app.constants import MENTIONS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import Mention

_ORDER = [("chunk_seq", ASCENDING), ("char_start", ASCENDING)]


def _col():
    return collection(MENTIONS_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("book_id", ASCENDING), ("node_key", ASCENDING), *_ORDER])
    _col().create_index([("chunk_id", ASCENDING)])


def insert_mentions(mentions: Sequence[Mention]) -> None:
    if mentions:
        _col().insert_many([m.to_mongo() for m in mentions])


def list_for_node(book_id: str, node_key: str, limit: int = 0) -> list[Mention]:
    """Mentions of one graph node in narrative order. `limit=0` means no limit."""
    cursor = _col().find({"book_id": book_id, "node_key": node_key}).sort(_ORDER).limit(limit)
    return [Mention.model_validate(d) for d in cursor]


def list_for_chunk(chunk_id: str) -> list[Mention]:
    cursor = _col().find({"chunk_id": chunk_id}).sort("char_start", ASCENDING)
    return [Mention.model_validate(d) for d in cursor]


def rekey(book_id: str, old_keys: Sequence[str], new_key: str) -> int:
    """Point mentions of merged nodes at the surviving key (entity resolution)."""
    result = _col().update_many(
        {"book_id": book_id, "node_key": {"$in": list(old_keys)}},
        {"$set": {"node_key": new_key}},
    )
    return result.modified_count


def delete_for_chunk(chunk_id: str) -> int:
    """Drop a chunk's mentions so re-extracting it cannot leave duplicates."""
    return _col().delete_many({"chunk_id": chunk_id}).deleted_count


def delete_mentions(book_id: str) -> int:
    return _col().delete_many({"book_id": book_id}).deleted_count


def rekey_by_sources(book_id: str, mappings: Sequence[tuple[int, str, str]]) -> int:
    """Rekey mentions using (chunk_seq, old_key, surviving_key) mappings.

    The chunk sequence is important: identical page-level keys can refer to different
    entities in different chunks before the merge resolves them.
    """
    changed = 0
    for chunk_seq, old_key, new_key in mappings:
        if old_key == new_key:
            continue
        result = _col().update_many(
            {"book_id": book_id, "chunk_seq": chunk_seq, "node_key": old_key},
            {"$set": {"node_key": new_key}},
        )
        changed += result.modified_count
    return changed
