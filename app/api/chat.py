"""Chat endpoints for trying the agent: open a session, ask, read the history. Books are listed by
`app.api.books`.

Signing in is optional. A session opened while signed in belongs to that user: only they can chat
in it, and its agent can use their Inkitt account. Anonymous sessions are open to anyone.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agents import get_agent
from app.api.auth import optional_user
from app.repo import books, sessions
from app.repo.models import ChatSession, User

router = APIRouter(tags=["chat"])
AGENT = "book_qa"
log = logging.getLogger(__name__)


class NewSession(BaseModel):
    book_id: str


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@router.post("/sessions", status_code=201)
def create_session(
    body: NewSession, user: Annotated[User | None, Depends(optional_user)]
) -> dict[str, Any]:
    if books.get_book(body.book_id) is None:
        raise HTTPException(404, f"unknown book '{body.book_id}'")
    session = sessions.create_session(
        ChatSession(book_id=body.book_id, user_id=user.id if user else None)
    )
    log.info("chat session created session=%s book=%s", session.id, session.book_id)
    return {"session_id": session.id, "book_id": session.book_id}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    if sessions.get_session(session_id) is None:
        raise HTTPException(404, f"unknown session '{session_id}'")
    return [
        {
            "role": m.role,
            "content": m.content,
            "citations": m.citations,
            "created_at": m.created_at.isoformat(),
        }
        for m in sessions.last_messages(session_id, min(max(limit, 1), 200))
    ]


@router.post("/sessions/{session_id}/chat")
async def chat(
    session_id: str, body: Question, user: Annotated[User | None, Depends(optional_user)]
) -> dict[str, Any]:
    session = sessions.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"unknown session '{session_id}'")
    if session.user_id is not None and (user is None or user.id != session.user_id):
        raise HTTPException(403, "this chat belongs to another user")
    result = await get_agent(AGENT).run(session.book_id, session_id, body.question)
    return {
        "answer": result["answer"],
        "citations": result["citations"],
        "trace": result["trace"],
        "actions": result.get("actions", []),
        # Internal structured memory changes are returned separately from answer text.
        "memory_updates": result.get("memory_updates", []),
    }
