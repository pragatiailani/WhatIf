"""LangGraph checkpointer backed by MongoDB (collections `checkpoints` and `checkpoint_writes`)."""

from functools import lru_cache

from langgraph.checkpoint.mongodb import MongoDBSaver

from app.config import get_settings
from app.db.mongo import get_client


@lru_cache
def get_checkpointer() -> MongoDBSaver:
    return MongoDBSaver(get_client(), db_name=get_settings().mongo_db)
