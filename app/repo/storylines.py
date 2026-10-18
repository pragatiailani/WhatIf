"""Storylines: the branch record, its op log and the ripple queue of an open draft.

The op log is the source of truth. Ops are written here before they reach FalkorDB. Ops up to
`committed_seq` passed verification; later ops are the draft. `version` changes with every draft
change, and a verification only counts for the version it ran on.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.constants import (
    STORYLINE_CHUNKS_COLLECTION,
    STORYLINE_ITEMS_COLLECTION,
    STORYLINE_OPS_COLLECTION,
    STORYLINES_COLLECTION,
)
from app.repo._mongo import collection
from app.repo.graph import GraphScope
from app.repo.models import (
    DraftState,
    Storyline,
    StorylineChunk,
    StorylineItem,
    StorylineOp,
    utcnow,
)


def ensure_indexes() -> None:
    collection(STORYLINE_OPS_COLLECTION).create_index(
        [("storyline_id", ASCENDING), ("seq", ASCENDING)], unique=True
    )
    collection(STORYLINE_ITEMS_COLLECTION).create_index(
        [("storyline_id", ASCENDING), ("ref", ASCENDING)], unique=True
    )
    collection(STORYLINE_CHUNKS_COLLECTION).create_index(
        [("storyline_id", ASCENDING), ("chapter", ASCENDING)], unique=True
    )
    collection(STORYLINES_COLLECTION).create_index([("book_id", ASCENDING), ("owner_id", ASCENDING)])


def _storylines():
    return collection(STORYLINES_COLLECTION)


def _ops():
    return collection(STORYLINE_OPS_COLLECTION)


def _items():
    return collection(STORYLINE_ITEMS_COLLECTION)


def _passages():
    return collection(STORYLINE_CHUNKS_COLLECTION)


# --- storyline -----------------------------------------------------------


def create_storyline(storyline: Storyline) -> Storyline:
    _storylines().insert_one(storyline.to_mongo())
    return storyline


def get_storyline(storyline_id: str) -> Storyline | None:
    doc = _storylines().find_one({"_id": storyline_id})
    return Storyline.model_validate(doc) if doc else None


def list_storylines(book_id: str, owner_id: str | None = None) -> list[Storyline]:
    query: dict[str, Any] = {"book_id": book_id}
    if owner_id is not None:
        query["owner_id"] = owner_id
    return [Storyline.model_validate(d) for d in _storylines().find(query).sort("created_at", DESCENDING)]


def list_for_user(owner_id: str, book_id: str | None = None) -> list[Storyline]:
    """The user's own storylines, newest activity first, optionally for one book."""
    query: dict[str, Any] = {"owner_id": owner_id}
    if book_id:
        query["book_id"] = book_id
    return [Storyline.model_validate(d) for d in _storylines().find(query).sort("updated_at", DESCENDING)]


def list_public(book_id: str | None = None) -> list[Storyline]:
    """Storylines their owners made public, newest activity first, optionally for one book."""
    query: dict[str, Any] = {"visibility": "public"}
    if book_id:
        query["book_id"] = book_id
    return [Storyline.model_validate(d) for d in _storylines().find(query).sort("updated_at", DESCENDING)]


def update_fields(storyline_id: str, **fields: Any) -> None:
    """Set plain fields (name, visibility) on a storyline."""
    _storylines().update_one({"_id": storyline_id}, {"$set": {**fields, "updated_at": utcnow()}})


def _bump(storyline_id: str, **inc: int) -> dict[str, Any]:
    """Increment `version` (plus any extra counters) and return the updated document."""
    doc = _storylines().find_one_and_update(
        {"_id": storyline_id},
        {"$inc": {"version": 1, **inc}, "$set": {"updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise ValueError(f"unknown storyline '{storyline_id}'")
    return doc


def touch(storyline_id: str) -> None:
    """Record a draft change that is not an op (a queue item resolved, items added)."""
    _bump(storyline_id)


def set_draft(storyline_id: str, draft: DraftState | None) -> None:
    _storylines().update_one(
        {"_id": storyline_id},
        {"$set": {"draft": draft.model_dump() if draft else None, "last_verification": {}}},
    )
    touch(storyline_id)


def add_affected(storyline_id: str, keys: Sequence[str]) -> list[str]:
    """Add entity keys to the draft's affected set; returns the ones that were new."""
    sl = get_storyline(storyline_id)
    if sl is None or sl.draft is None:
        raise ValueError("no open draft")
    new = [k for k in dict.fromkeys(keys) if k not in sl.draft.affected]
    if new:
        _storylines().update_one(
            {"_id": storyline_id}, {"$push": {"draft.affected": {"$each": new}}}
        )
    return new


def record_verification(storyline_id: str, version: int, result: dict[str, Any]) -> None:
    """Store a verification result; it counts as passed only if nothing changed since `version`."""
    passed = bool(result.get("passed"))
    _storylines().update_one(
        {"_id": storyline_id, "version": version},
        {"$set": {"verified_version": version if passed else None, "last_verification": result}},
    )


def mark_committed(storyline_id: str) -> None:
    doc = _storylines().find_one({"_id": storyline_id}, projection=["head_seq"])
    _storylines().update_one(
        {"_id": storyline_id},
        {"$set": {"committed_seq": doc["head_seq"], "draft": None, "last_verification": {}}},
    )


# --- ops -----------------------------------------------------------------


def append_op(storyline_id: str, kind: str, payload: dict[str, Any]) -> StorylineOp:
    """Append to the log. Bumps `version`, which invalidates any earlier verification."""
    doc = _bump(storyline_id, head_seq=1)
    op = StorylineOp(storyline_id=storyline_id, seq=doc["head_seq"], kind=kind, payload=payload)
    _ops().insert_one(op.to_mongo())
    return op


def list_ops(
    storyline_id: str, upto_seq: int | None = None, after_seq: int | None = None
) -> list[StorylineOp]:
    """Applied ops in `seq` order, limited to `after_seq < seq <= upto_seq`."""
    seq: dict[str, int] = {}
    if upto_seq is not None:
        seq["$lte"] = upto_seq
    if after_seq is not None:
        seq["$gt"] = after_seq
    query: dict[str, Any] = {"storyline_id": storyline_id, "status": "applied"}
    if seq:
        query["seq"] = seq
    return [StorylineOp.model_validate(d) for d in _ops().find(query).sort("seq", ASCENDING)]


def drop_draft_ops(storyline_id: str) -> int:
    """Delete the uncommitted ops and rewind `head_seq` to `committed_seq`. Returns the committed
    seq, the point the graph must be rewound to."""
    sl = get_storyline(storyline_id)
    if sl is None:
        raise ValueError(f"unknown storyline '{storyline_id}'")
    _ops().delete_many({"storyline_id": storyline_id, "seq": {"$gt": sl.committed_seq}})
    _storylines().update_one({"_id": storyline_id}, {"$set": {"head_seq": sl.committed_seq}})
    return sl.committed_seq


def scope(sl: Storyline, committed_only: bool = False) -> GraphScope:
    """The graph view of a storyline: canon plus its ops, up to the head or the last commit."""
    upto = sl.committed_seq if committed_only else sl.head_seq
    hidden_nodes: set[str] = set()
    hidden_edges: set[str] = set()
    for op in list_ops(sl.id, upto_seq=upto):
        if op.kind == "hide_node":
            hidden_nodes.add(op.payload["key"])
        elif op.kind == "hide_edge":
            hidden_edges.add(op.payload["id"])
    return GraphScope(
        book_id=sl.book_id,
        sid=sl.id,
        max_op=upto,
        hidden_nodes=frozenset(hidden_nodes),
        hidden_edges=frozenset(hidden_edges),
    )


# --- ripple queue --------------------------------------------------------


def add_items(items: Sequence[StorylineItem]) -> int:
    """Queue items, skipping refs already queued. Returns how many were new."""
    if not items:
        return 0
    storyline_id = items[0].storyline_id
    known = {d["ref"] for d in _items().find({"storyline_id": storyline_id}, projection=["ref"])}
    fresh = [i for i in items if i.ref not in known]
    if fresh:
        _items().insert_many([i.to_mongo() for i in fresh])
        touch(storyline_id)
    return len(fresh)


def get_item(storyline_id: str, ref: str) -> StorylineItem | None:
    doc = _items().find_one({"storyline_id": storyline_id, "ref": ref})
    return StorylineItem.model_validate(doc) if doc else None


def list_items(storyline_id: str, state: str | None = None, limit: int = 0) -> list[StorylineItem]:
    """Queue items in narrative order, optionally only `state`."""
    query: dict[str, Any] = {"storyline_id": storyline_id}
    if state:
        query["state"] = state
    cursor = _items().find(query).sort([("pos", ASCENDING), ("ref", ASCENDING)])
    if limit:
        cursor = cursor.limit(limit)
    return [StorylineItem.model_validate(d) for d in cursor]


def count_items(storyline_id: str, state: str | None = None) -> int:
    query: dict[str, Any] = {"storyline_id": storyline_id}
    if state:
        query["state"] = state
    return _items().count_documents(query)


def resolve_item(storyline_id: str, ref: str, action: str) -> None:
    _items().update_one(
        {"storyline_id": storyline_id, "ref": ref}, {"$set": {"state": "resolved", "action": action}}
    )
    touch(storyline_id)


def delete_items(storyline_id: str) -> None:
    _items().delete_many({"storyline_id": storyline_id})


# --- rewritten chapters --------------------------------------------------


def save_passage(passage: StorylineChunk) -> StorylineChunk:
    """Store a chapter's passage, replacing any earlier one for the same chapter."""
    _passages().delete_one({"storyline_id": passage.storyline_id, "chapter": passage.chapter})
    _passages().insert_one(passage.to_mongo())
    return passage


def get_passage(storyline_id: str, chapter: str) -> StorylineChunk | None:
    doc = _passages().find_one({"storyline_id": storyline_id, "chapter": chapter})
    return StorylineChunk.model_validate(doc) if doc else None


def get_passages(storyline_id: str, ids: Sequence[str]) -> list[StorylineChunk]:
    """Passages of the storyline by id, in the order of `ids` (missing ids are skipped)."""
    by_id = {d["_id"]: d for d in _passages().find({"storyline_id": storyline_id, "_id": {"$in": list(ids)}})}
    return [StorylineChunk.model_validate(by_id[i]) for i in ids if i in by_id]


def list_passages(storyline_id: str) -> list[StorylineChunk]:
    cursor = _passages().find({"storyline_id": storyline_id}).sort("seq_start", ASCENDING)
    return [StorylineChunk.model_validate(d) for d in cursor]


def delete_passages_after(storyline_id: str, op_seq: int) -> None:
    """Drop passages written after `op_seq`, i.e. during a draft that is being discarded."""
    _passages().delete_many({"storyline_id": storyline_id, "op_seq": {"$gt": op_seq}})


# --- publishing ----------------------------------------------------------


class StorylinesUnavailable(Exception):
    """A storyline cannot be read as a book right now (an open draft, or chapters not written)."""


@dataclass(frozen=True)
class StorylineText:
    """A storyline as the Inkitt publisher reads it."""

    id: str
    book_id: str  # the base book
    owner_id: str
    title: str
    # Title of the first base-book chapter the storyline changes: base chapters before it are kept
    # as they are. None keeps every base chapter (nothing in the storyline changes the book).
    diverges_at: str | None
    chapters: list[tuple[str, str]] = field(default_factory=list)  # (title, text) from there on


def get_storyline_text(storyline_id: str) -> StorylineText | None:
    """The storyline's metadata and its chapters from the point it diverges, or None if there is
    no such storyline. Raises `StorylinesUnavailable` if it cannot be read yet."""
    from app.storyline.publish import storyline_text  # the engine sits above the repo layer

    return storyline_text(storyline_id)
