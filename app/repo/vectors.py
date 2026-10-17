"""Embedding vectors per book: chunks, entities, events and communities, for semantic search.

One document per vector, stored as float32 bytes and unit length so a dot product is the cosine
similarity. Each book also has a version document; it changes whenever the book is re-indexed, so
readers can cache a book's matrix until then.
"""

from typing import Literal

import numpy as np
from bson import Binary
from pymongo import ASCENDING

from app.constants import VECTORS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import new_id

VectorKind = Literal["chunk", "entity", "event", "community"]
_VERSION = "version"


def _col():
    return collection(VECTORS_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("book_id", ASCENDING), ("kind", ASCENDING)])


def replace_book(book_id: str, kind: VectorKind, refs: list[str], matrix: np.ndarray) -> None:
    """Store `matrix` (one row per ref) as the book's `kind` vectors, replacing earlier ones."""
    if len(refs) != len(matrix):
        raise ValueError("one vector per ref")
    _col().delete_many({"book_id": book_id, "kind": kind})
    unit = _normalize(np.asarray(matrix, dtype=np.float32))
    if refs:
        _col().insert_many(
            [
                {"_id": f"{kind}:{book_id}:{ref}", "book_id": book_id, "kind": kind, "ref": ref,
                 "vec": Binary(row.tobytes())}
                for ref, row in zip(refs, unit, strict=True)
            ]
        )  # fmt: skip
    _col().update_one(
        {"_id": f"{_VERSION}:{book_id}"},
        {"$set": {"book_id": book_id, "kind": _VERSION, "version": new_id()}},
        upsert=True,
    )


def load(book_id: str, kind: VectorKind) -> tuple[list[str], np.ndarray]:
    """(refs, matrix) of the book's `kind` vectors; an empty matrix if it was never indexed."""
    refs, rows = [], []
    for doc in _col().find({"book_id": book_id, "kind": kind}, projection=["ref", "vec"]):
        refs.append(doc["ref"])
        rows.append(np.frombuffer(doc["vec"], dtype=np.float32))
    return refs, (np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.float32))


def version(book_id: str) -> str | None:
    """Changes on every re-index of the book; None if it was never indexed."""
    doc = _col().find_one({"_id": f"{_VERSION}:{book_id}"})
    return doc["version"] if doc else None


def delete_book(book_id: str) -> int:
    return _col().delete_many({"book_id": book_id}).deleted_count


def _normalize(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)
