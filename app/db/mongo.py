from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

from app.config import get_settings


@lru_cache
def get_client() -> MongoClient:
    return MongoClient(get_settings().mongo_uri, tz_aware=True)


def get_db() -> Database:
    return get_client()[get_settings().mongo_db]
