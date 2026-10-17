"""Session tools: chat history, the rolling summary and bookmarked notes."""

from typing import Any

from app.constants import TOOL_MAX_HISTORY
from app.repo import sessions
from app.repo.models import ChatSession, Note


def _session(session_id: str) -> ChatSession:
    session = sessions.get_session(session_id)
    if session is None:
        raise ValueError(f"unknown session '{session_id}'")
    return session


def get_history(session_id: str, n: int = 10) -> dict[str, Any]:
    _session(session_id)
    messages = sessions.last_messages(session_id, min(max(n, 1), TOOL_MAX_HISTORY))
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]
    }


def get_session_summary(session_id: str) -> dict[str, Any]:
    session = _session(session_id)
    return {"title": session.title, "summary": session.summary}


def save_note(session_id: str, text: str, message_id: str | None = None) -> dict[str, Any]:
    session = _session(session_id)
    text = text.strip()
    if not text:
        raise ValueError("note text is empty")
    if message_id is not None:
        message = sessions.get_message(message_id)
        if message is None or message.session_id != session_id:
            raise ValueError(f"message '{message_id}' is not in this session")
    note = sessions.add_note(
        Note(session_id=session_id, book_id=session.book_id, message_id=message_id, text=text)
    )
    return {"note_id": note.id, "saved": True}
