"""Chat sessions, their messages and the notes users bookmark from them."""

from pymongo import ASCENDING, DESCENDING

from app.constants import MESSAGES_COLLECTION, NOTES_COLLECTION, SESSIONS_COLLECTION
from app.repo._mongo import collection
from app.repo.models import ChatSession, Message, Note, utcnow


def ensure_indexes() -> None:
    collection(MESSAGES_COLLECTION).create_index([("session_id", ASCENDING), ("seq", ASCENDING)])
    collection(NOTES_COLLECTION).create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
    collection(SESSIONS_COLLECTION).create_index(
        [("user_id", ASCENDING), ("book_id", ASCENDING), ("storyline_id", ASCENDING)]
    )


def create_session(session: ChatSession) -> ChatSession:
    collection(SESSIONS_COLLECTION).insert_one(session.to_mongo())
    return session


def get_session(session_id: str) -> ChatSession | None:
    doc = collection(SESSIONS_COLLECTION).find_one({"_id": session_id})
    return ChatSession.model_validate(doc) if doc else None


def list_sessions(user_id: str, book_id: str, storyline_id: str | None) -> list[ChatSession]:
    """The user's chats about a book in one storyline (None = canon), most recently active first."""
    cursor = collection(SESSIONS_COLLECTION).find(
        {"user_id": user_id, "book_id": book_id, "storyline_id": storyline_id},
        sort=[("last_active", DESCENDING)],
    )
    return [ChatSession.model_validate(d) for d in cursor]


def set_summary(session_id: str, summary: str) -> None:
    collection(SESSIONS_COLLECTION).update_one({"_id": session_id}, {"$set": {"summary": summary}})


def set_title(session_id: str, title: str) -> None:
    collection(SESSIONS_COLLECTION).update_one({"_id": session_id}, {"$set": {"title": title}})


def add_message(message: Message) -> Message:
    """Append to the session. `seq` orders messages; timestamps can tie within a millisecond."""
    last = collection(MESSAGES_COLLECTION).find_one(
        {"session_id": message.session_id}, sort=[("seq", DESCENDING)], projection=["seq"]
    )
    message.seq = last["seq"] + 1 if last else 0
    collection(MESSAGES_COLLECTION).insert_one(message.to_mongo())
    collection(SESSIONS_COLLECTION).update_one(
        {"_id": message.session_id}, {"$set": {"last_active": utcnow()}}
    )
    return message


def last_messages(session_id: str, n: int) -> list[Message]:
    """The last `n` messages of a session, oldest first."""
    if n <= 0:
        return []
    cursor = (
        collection(MESSAGES_COLLECTION)
        .find({"session_id": session_id})
        .sort("seq", DESCENDING)
        .limit(n)
    )
    return [Message.model_validate(d) for d in reversed(list(cursor))]


def get_message(message_id: str) -> Message | None:
    doc = collection(MESSAGES_COLLECTION).find_one({"_id": message_id})
    return Message.model_validate(doc) if doc else None


def add_note(note: Note) -> Note:
    collection(NOTES_COLLECTION).insert_one(note.to_mongo())
    return note


def list_notes(session_id: str) -> list[Note]:
    cursor = collection(NOTES_COLLECTION).find({"session_id": session_id}).sort("created_at", ASCENDING)
    return [Note.model_validate(d) for d in cursor]


def set_storyline(session_id: str, storyline_id: str | None) -> None:
    """Bind the session to a storyline (None = canon)."""
    collection(SESSIONS_COLLECTION).update_one(
        {"_id": session_id}, {"$set": {"storyline_id": storyline_id}}
    )


def find_session(user_id: str, book_id: str, storyline_id: str | None) -> ChatSession | None:
    """The user's most recently active chat about a book in one storyline (None = canon)."""
    doc = collection(SESSIONS_COLLECTION).find_one(
        {"user_id": user_id, "book_id": book_id, "storyline_id": storyline_id},
        sort=[("last_active", DESCENDING)],
    )
    return ChatSession.model_validate(doc) if doc else None


def update_message(message_id: str, **fields) -> None:
    """Set extra fields (route, retrieval, diff) on a saved message."""
    collection(MESSAGES_COLLECTION).update_one({"_id": message_id}, {"$set": fields})
