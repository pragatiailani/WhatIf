import mongomock
import pytest

from app.repo import _mongo


@pytest.fixture
def mongo(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["whatif_test"]
    monkeypatch.setattr(_mongo, "get_db", lambda: db)
    return db
