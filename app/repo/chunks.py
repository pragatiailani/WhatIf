import re
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


def search(
    book_id: str, terms: Sequence[str], chapter: str | None = None, limit: int = 300
) -> list[Chunk]:
    """Chunks containing at least one of `terms` (case-insensitive, literal), optionally limited to
    a chapter title. Unranked: the caller scores them. Capped at `limit` chunks."""
    if not terms:
        return []
    query: dict = {
        "book_id": book_id,
        "$or": [{"text": {"$regex": re.escape(t), "$options": "i"}} for t in terms],
    }
    if chapter is not None:
        query["chapter"] = chapter
    # Do not take the first N matches in narrative order: that systematically favors the
    # beginning of a book. Rank the matching candidates by distinct term coverage and frequency.
    candidates = []
    for doc in _col().find(query):
        text = str(doc.get("text", "")).lower()
        coverage = sum(1 for term in terms if term.lower() in text)
        frequency = sum(text.count(term.lower()) for term in terms)
        candidates.append((coverage, frequency, -int(doc.get("seq", 0)), doc))
    candidates.sort(key=lambda row: (-row[0], -row[1], -row[2]))
    return [Chunk.model_validate(row[3]) for row in candidates[: max(1, limit)]]


def list_chapters(book_id: str) -> list[dict]:
    """Chapters in book order: {title, seq_start, seq_end, page_start, page_end}."""
    chapters: dict[str, dict] = {}
    for c in list_chunks(book_id):
        if c.chapter is None:
            continue
        pages = [s.page for s in c.page_spans]
        info = chapters.setdefault(
            c.chapter,
            {"title": c.chapter, "seq_start": c.seq, "seq_end": c.seq, "page_start": None, "page_end": None},
        )
        info["seq_end"] = c.seq
        if pages:
            info["page_start"] = min(pages + ([info["page_start"]] if info["page_start"] else []))
            info["page_end"] = max(pages + ([info["page_end"]] if info["page_end"] else []))
    return list(chapters.values())


def ids_by_seq(book_id: str, seqs: Sequence[int]) -> dict[int, str]:
    """Chunk id for each seq that exists."""
    docs = _col().find({"book_id": book_id, "seq": {"$in": list(seqs)}}, projection=["seq"])
    return {d["seq"]: d["_id"] for d in docs}
