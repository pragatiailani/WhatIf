"""Chat endpoints for trying the agent: list books, open a session, ask, read the history.

There is no authentication yet; anyone who can reach the API can use any book and session.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents import get_agent
from app.repo import books, sessions
from app.repo.models import ChatSession

router = APIRouter(tags=["chat"])
AGENT = "book_qa"


class NewSession(BaseModel):
    book_id: str


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@router.get("/books")
def list_books() -> list[dict[str, Any]]:
    return [
        {"id": b.id, "title": b.title, "author": b.author, "status": b.status}
        for b in books.list_books()
    ]


@router.post("/sessions", status_code=201)
def create_session(body: NewSession) -> dict[str, Any]:
    if books.get_book(body.book_id) is None:
        raise HTTPException(404, f"unknown book '{body.book_id}'")
    session = sessions.create_session(ChatSession(book_id=body.book_id))
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
async def chat(session_id: str, body: Question) -> dict[str, Any]:
    session = sessions.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"unknown session '{session_id}'")
    result = await get_agent(AGENT).run(session.book_id, session_id, body.question)
    return {"answer": result["answer"], "citations": result["citations"], "trace": result["trace"]}
