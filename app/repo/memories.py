"""MongoDB persistence for LLM-proposed cross-session user memories."""
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection

from app.repo._mongo import collection
from app.repo.models import UserMemory, utcnow

COLLECTION = "user_memories"


def _col() -> Collection:
    return collection(COLLECTION)


def ensure_indexes() -> None:
    _col().create_index([("user_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    _col().create_index(
        [("user_id", ASCENDING), ("key", ASCENDING), ("scope_type", ASCENDING),
         ("book_id", ASCENDING), ("storyline_id", ASCENDING), ("status", ASCENDING)],
        name="user_memory_identity_status",
    )


def list_memories(user_id: str, *, include_inactive: bool = False) -> list[UserMemory]:
    query = {"user_id": user_id}
    if not include_inactive:
        query["status"] = "active"
    docs = _col().find(query).sort([("updated_at", DESCENDING)])
    return [UserMemory.model_validate(doc) for doc in docs]


def relevant_memories(user_id: str, book_id: str, storyline_id: str | None = None,
                      limit: int = 30) -> list[UserMemory]:
    query = {"user_id": user_id, "status": "active", "$or": [
        {"scope_type": "global"},
        {"scope_type": "book", "book_id": book_id},
        {"scope_type": "storyline", "book_id": book_id, "storyline_id": storyline_id},
    ]}
    docs = list(_col().find(query).sort([("updated_at", DESCENDING)]).limit(limit))
    found = [UserMemory.model_validate(doc) for doc in docs]
    found.sort(key=lambda m: (m.source != "explicit", -m.updated_at.timestamp()))
    return found


def memory_candidates(user_id: str, book_id: str, storyline_id: str | None = None,
                      limit: int = 30) -> list[UserMemory]:
    """Active and pending memories used only to assess repeated evidence."""
    query = {"user_id": user_id, "status": {"$in": ["active", "pending"]}, "$or": [
        {"scope_type": "global"},
        {"scope_type": "book", "book_id": book_id},
        {"scope_type": "storyline", "book_id": book_id, "storyline_id": storyline_id},
    ]}
    docs = _col().find(query).sort([("updated_at", DESCENDING)]).limit(limit)
    return [UserMemory.model_validate(doc) for doc in docs]


def _identity(memory: UserMemory) -> dict:
    return {"user_id": memory.user_id, "key": memory.key, "scope_type": memory.scope_type,
            "book_id": memory.book_id, "storyline_id": memory.storyline_id}


def apply_memory_delta(memory: UserMemory, operation: str = "add") -> UserMemory | None:
    """Apply an LLM proposal with state transitions and evidence thresholds.

    Inferred signals stay pending until at least two observations and confidence >= .75. Explicit
    statements activate immediately. Inference cannot overwrite an explicit preference.
    """
    now = utcnow()
    identity = _identity(memory)
    existing_doc = _col().find_one({**identity, "status": {"$in": ["active", "pending"]}})
    existing = UserMemory.model_validate(existing_doc) if existing_doc else None
    if operation not in {"add", "reinforce", "replace"}:
        return None

    if existing:
        if existing.source == "explicit" and memory.source != "explicit":
            _col().update_one({"_id": existing.id}, {"$inc": {"evidence_count": 1},
                "$set": {"updated_at": now, "last_session_id": memory.last_session_id}})
            return UserMemory.model_validate(_col().find_one({"_id": existing.id}))
        if memory.source != "explicit" and existing.value != memory.value and operation != "replace":
            return existing
        count = existing.evidence_count + 1
        if memory.source == "explicit" or existing.status == "active":
            status = "active"
        elif count >= 2 and memory.confidence >= 0.75:
            status = "active"
        else:
            status = "pending"
        _col().update_one({"_id": existing.id}, {"$set": {
            "type": memory.type, "value": memory.value,
            "source": "explicit" if memory.source == "explicit" else existing.source,
            "confidence": max(existing.confidence, memory.confidence), "evidence_count": count,
            "status": status, "updated_at": now, "last_session_id": memory.last_session_id,
        }})
        return UserMemory.model_validate(_col().find_one({"_id": existing.id}))

    memory.created_at = now
    memory.updated_at = now
    memory.status = "active" if memory.source == "explicit" else "pending"
    _col().insert_one(memory.to_mongo())
    return memory


def forget_by_key(user_id: str, key: str, scope_type: str, book_id: str | None,
                  storyline_id: str | None) -> bool:
    result = _col().update_many({"user_id": user_id, "key": key, "scope_type": scope_type,
        "book_id": book_id, "storyline_id": storyline_id,
        "status": {"$in": ["active", "pending"]}},
        {"$set": {"status": "deleted", "updated_at": utcnow()}})
    return result.modified_count > 0


def set_memory(user_id: str, memory_id: str, key: str, value: str) -> UserMemory | None:
    result = _col().find_one_and_update(
        {"_id": memory_id, "user_id": user_id, "status": "active"},
        {"$set": {"key": key, "value": value, "source": "explicit", "confidence": 1.0,
                  "updated_at": utcnow()}}, return_document=ReturnDocument.AFTER)
    return UserMemory.model_validate(result) if result else None


def delete_memory(user_id: str, memory_id: str) -> bool:
    result = _col().update_one({"_id": memory_id, "user_id": user_id, "status": "active"},
        {"$set": {"status": "deleted", "updated_at": utcnow()}})
    return result.modified_count > 0


def clear_memories(user_id: str) -> int:
    result = _col().update_many({"user_id": user_id, "status": {"$in": ["active", "pending"]}},
        {"$set": {"status": "deleted", "updated_at": utcnow()}})
    return result.modified_count
