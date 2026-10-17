"""The reading page's API: a book's graph, its storylines, and asking questions about it.

Shapes follow the web app's contract (`web/src/api/types.ts`). Each signed-in user has one
conversation per book; asking streams Server-Sent Events: `phase` {label} while the agent works,
`token` {text} as the answer is written, then `done` with the saved turn, or `error` {detail}.
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import book_qa
from app.api.auth import current_user, optional_user
from app.repo import GraphScope, books, communities, graph, sessions
from app.repo.models import Book, Message, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/books", tags=["reading"])

HISTORY_LIMIT = 200
NO_STORYLINES = "Storylines are not available yet."
ANSWER_FAILED = "Something went wrong while answering. Please try again."


class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    storyline_id: str | None = None


def _readable(book_id: str, user: User | None) -> Book:
    """The book if `user` may read it (public, theirs, or shared with them); 404 otherwise."""
    book = books.get_book(book_id)
    allowed = book is not None and (
        book.visibility == "public"
        or (user is not None and (book.owner_id == user.id or user.id in book.shared_with))
    )
    if not allowed:
        raise HTTPException(404, "book not found")
    return book


def _ready(book: Book) -> None:
    if book.status != "ready":
        raise HTTPException(409, "This book is still being read. Try again when it is ready.")


# --- graph and storylines -------------------------------------------------


@router.get("/{book_id}/graph")
def get_graph(
    book_id: str,
    user: Annotated[User | None, Depends(optional_user)],
    storyline_id: str | None = None,
) -> dict[str, Any]:
    book = _readable(book_id, user)
    if storyline_id:
        raise HTTPException(404, NO_STORYLINES)
    _ready(book)
    entities, events, relations = graph.book_graph(GraphScope.canon(book_id))
    return {
        "entities": [e.model_dump(exclude={"embedding", "created_by_op"}) for e in entities],
        "events": [
            {**e.model_dump(include={"key", "summary", "story_pos", "chapter", "first_page", "sid"}),
             "involves": keys}
            for e, keys in events
        ],  # fmt: skip
        "relations": [
            r.model_dump(include={"id", "src", "dst", "rel", "description", "weight", "sid"})
            for r in relations
        ],
        "communities": [
            {"cid": c.cid, "title": c.title, "summary": c.summary}
            for c in communities.list_communities(book_id)
        ],
        "hidden": [],
    }


@router.get("/{book_id}/storylines")
def list_storylines(
    book_id: str, user: Annotated[User | None, Depends(optional_user)]
) -> list[dict[str, Any]]:
    """Storylines are not stored yet, so every book has none."""
    _readable(book_id, user)
    return []


# --- chat ------------------------------------------------------------------


def _step(t: dict[str, Any]) -> dict[str, str]:
    label = book_qa.PHASES.get(t["tool"], t["tool"]).rstrip("…")
    args = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in t["args"].items())
    return {"label": label + (" (failed)" if t["error"] else ""), "detail": args}


def message_out(m: Message, question: str = "") -> dict[str, Any]:
    """A saved message as the web app's Message. `question` is the user message it answers."""
    out: dict[str, Any] = {
        "id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat(),
    }  # fmt: skip
    if m.role != "assistant":
        return out
    out["citations"] = [
        {"chapter": c.get("chapter"), "page": c.get("page_start"), "quote": c.get("quote", "")}
        for c in m.citations
    ]
    tools = {t["tool"] for t in m.trace}
    out["route"] = "global" if "search_communities" in tools else "local"
    refs = m.graph_refs
    # The UI reads `communities` as "answered from the big picture", so only global answers set it.
    communities = refs.get("communities", []) if out["route"] == "global" else []
    out["trace"] = {
        "rewritten_question": question,
        "seeds": refs.get("seeds", []),
        "expanded": refs.get("expanded", []),
        "edges": refs.get("edges", []),
        "hops": 1 if refs.get("expanded") else 0,
        "communities": communities,
        "steps": [_step(t) for t in m.trace],
    }
    return out


@router.get("/{book_id}/messages")
def get_messages(
    book_id: str,
    user: Annotated[User, Depends(current_user)],
    storyline_id: str | None = None,
) -> list[dict[str, Any]]:
    """The user's conversation on this book, oldest first."""
    _readable(book_id, user)
    session = None if storyline_id else sessions.find_book_chat(user.id, book_id)
    if session is None:
        return []
    out, question = [], ""
    for m in sessions.last_messages(session.id, HISTORY_LIMIT):
        out.append(message_out(m, question))
        question = m.content if m.role == "user" else question
    return out


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{book_id}/chat")
async def ask(
    book_id: str, body: Ask, user: Annotated[User, Depends(current_user)]
) -> StreamingResponse:
    """Ask a question about the book. Streams the answer as it is worked out (see module doc)."""
    book = _readable(book_id, user)
    if body.storyline_id:
        raise HTTPException(400, NO_STORYLINES)
    _ready(book)
    session = sessions.book_chat(user.id, book_id)

    async def events():
        try:
            async for kind, value in book_qa.stream(book_id, session.id, body.text):
                if kind == "phase":
                    yield _sse("phase", {"label": value})
                elif kind == "token":
                    yield _sse("token", {"text": value})
                else:
                    yield _sse("done", {
                        "user": message_out(value["user_message"]),
                        "assistant": message_out(value["assistant_message"], body.text),
                        "storyline": None,
                        "actions": value["actions"],
                    })  # fmt: skip
        except Exception:
            log.exception("answering on book %s failed", book_id)
            yield _sse("error", {"detail": ANSWER_FAILED})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
