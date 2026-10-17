from pymongo import ASCENDING

from app.constants import BOOKS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import Book, BookStatus, utcnow


def _col():
    return collection(BOOKS_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("content_hash", ASCENDING)])
    _col().create_index([("owner_id", ASCENDING)])


def create_book(book: Book) -> Book:
    _col().insert_one(book.to_mongo())
    return book


def get_book(book_id: str) -> Book | None:
    doc = _col().find_one({"_id": book_id})
    return Book.model_validate(doc) if doc else None


def find_by_content_hash(content_hash: str, owner_id: str | None = None) -> Book | None:
    doc = _col().find_one({"content_hash": content_hash, "owner_id": owner_id})
    return Book.model_validate(doc) if doc else None


def set_status(book_id: str, status: BookStatus, error: str | None = None) -> None:
    _col().update_one(
        {"_id": book_id},
        {"$set": {"status": status, "error": error, "updated_at": utcnow()}},
    )


def update_stats(book_id: str, stats: dict[str, int]) -> None:
    """Merge `stats` into the book's stats (existing keys are overwritten)."""
    fields = {f"stats.{k}": v for k, v in stats.items()}
    _col().update_one({"_id": book_id}, {"$set": {**fields, "updated_at": utcnow()}})
