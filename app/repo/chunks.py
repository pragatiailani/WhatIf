from collections.abc import Sequence

from pymongo import ASCENDING

from app.constants import CHUNKS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import Chunk


def _col():
    return collection(CHUNKS_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("book_id", ASCENDING), ("seq", ASCENDING)], unique=True)


def insert_chunks(chunks: Sequence[Chunk]) -> None:
    if chunks:
        _col().insert_many([c.to_mongo() for c in chunks])


def get_chunk(chunk_id: str) -> Chunk | None:
    doc = _col().find_one({"_id": chunk_id})
    return Chunk.model_validate(doc) if doc else None


def get_chunks(chunk_ids: Sequence[str]) -> list[Chunk]:
    """Fetch chunks by id, returned in the order of `chunk_ids` (missing ids are skipped)."""
    by_id = {d["_id"]: d for d in _col().find({"_id": {"$in": list(chunk_ids)}})}
    return [Chunk.model_validate(by_id[i]) for i in chunk_ids if i in by_id]


def list_chunks(book_id: str, seq_from: int | None = None, seq_to: int | None = None) -> list[Chunk]:
    """Chunks of a book in `seq` order, optionally limited to `seq_from..seq_to` (inclusive)."""
    query: dict = {"book_id": book_id}
    seq: dict = {}
    if seq_from is not None:
        seq["$gte"] = seq_from
    if seq_to is not None:
        seq["$lte"] = seq_to
    if seq:
        query["seq"] = seq
    return [Chunk.model_validate(d) for d in _col().find(query).sort("seq", ASCENDING)]


def count_chunks(book_id: str) -> int:
    return _col().count_documents({"book_id": book_id})


def delete_chunks(book_id: str) -> int:
    return _col().delete_many({"book_id": book_id}).deleted_count
