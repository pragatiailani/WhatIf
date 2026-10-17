import mongomock
import pytest

from app.repo import _mongo


@pytest.fixture
def mongo(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["whatif_test"]
    monkeypatch.setattr(_mongo, "get_db", lambda: db)
    return db


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch):
    """Semantic search is off unless a test turns it on: tests never call an embedding API."""
    from app import retrieval

    monkeypatch.setattr(retrieval, "get_embeddings", lambda: None)
    retrieval._cache.clear()
