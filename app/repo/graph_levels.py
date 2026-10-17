from pymongo import ASCENDING, DESCENDING

from app.constants import GRAPH_LEVELS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import GraphLevel


def _col():
    return collection(GRAPH_LEVELS_COLLECTION)


def ensure_indexes() -> None:
    _col().create_index(
        [("book_id", ASCENDING), ("level", ASCENDING), ("index", ASCENDING)], unique=True
    )


def save_level_graph(level_graph: GraphLevel) -> GraphLevel:
    """Upsert on (book_id, level, index) so a resumed merge can rewrite a slot safely."""
    doc = level_graph.to_mongo()
    _id = doc.pop("_id")
    _col().update_one(
        {"book_id": level_graph.book_id, "level": level_graph.level, "index": level_graph.index},
        {"$set": doc, "$setOnInsert": {"_id": _id}},
        upsert=True,
    )
    return get_level_graph(level_graph.book_id, level_graph.level, level_graph.index)  # type: ignore[return-value]


def get_level_graph(book_id: str, level: int, index: int) -> GraphLevel | None:
    doc = _col().find_one({"book_id": book_id, "level": level, "index": index})
    return GraphLevel.model_validate(doc) if doc else None


def list_level(book_id: str, level: int) -> list[GraphLevel]:
    cursor = _col().find({"book_id": book_id, "level": level}).sort("index", ASCENDING)
    return [GraphLevel.model_validate(d) for d in cursor]


def count_level(book_id: str, level: int) -> int:
    return _col().count_documents({"book_id": book_id, "level": level})


def max_level(book_id: str) -> int | None:
    """Highest level stored for a book, or None if no levels exist."""
    doc = _col().find_one({"book_id": book_id}, sort=[("level", DESCENDING)], projection=["level"])
    return doc["level"] if doc else None


def delete_levels(book_id: str, from_level: int = 0) -> int:
    return _col().delete_many({"book_id": book_id, "level": {"$gte": from_level}}).deleted_count
