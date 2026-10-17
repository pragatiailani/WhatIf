from pymongo.collection import Collection

from app.db.mongo import get_db


def collection(name: str) -> Collection:
    return get_db()[name]
