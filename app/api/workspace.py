"""The routes the web UI uses to read a book as a graph, manage storylines and chat.

Chat streams Server-Sent Events: `phase` (what the agent is doing), `token` (pieces of the reply),
then `done` with the whole turn (both messages, the graph after the turn, the storyline), or
`error`. Messages, graphs and storylines are shaped here to the UI's wire types
(`web/src/api/types.ts`); the repo models stay free of UI concerns.

Not built yet, and answered with 501: forking, starring, and saving or restoring checkpoints.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import get_agent
from app.agents.progress import label_for
from app.api.auth import current_user, optional_user
from app.logging_config import fields, request_id
from app.repo import GraphScope, books, chunks, communities, graph, sessions, storylines, users
from app.repo.models import Book, ChatSession, Message, Storyline, User

router = APIRouter(tags=["workspace"])
log = logging.getLogger(__name__)

AGENT = "whatif"
QUOTE_CHARS = 200
TOKEN_WORDS = 6  # words per `token` event; the reply is complete before it is streamed


# --- access --------------------------------------------------------------


def _book_for(book_id: str, user: User | None) -> Book:
    book = books.get_book(book_id)
    if book is None or (book.visibility == "private" and (user is None or book.owner_id != user.id)):
        raise HTTPException(404, "book not found")
    return book


def _storyline_for(storyline_id: str, user: User | None, *, write: bool = False) -> Storyline:
    sl = storylines.get_storyline(storyline_id)
    mine = sl is not None and user is not None and sl.owner_id == user.id
    if sl is None or not (mine or sl.visibility == "public"):
        raise HTTPException(404, "storyline not found")
    if write and not mine:
        raise HTTPException(403, "this storyline belongs to someone else; fork it to edit")
    return sl


def _not_built(what: str) -> HTTPException:
    return HTTPException(501, f"{what} is not available yet")


# --- shaping -------------------------------------------------------------


def storyline_out(sl: Storyline) -> dict[str, Any]:
    owner = users.get_user(sl.owner_id) if sl.owner_id else None
    return {
        "id": sl.id,
        "book_id": sl.book_id,
        "owner_id": sl.owner_id or "",
        "owner_name": owner.name if owner else "Unknown",
        "title": sl.name,
        "premise": sl.premise,
        "visibility": sl.visibility,
        "parent_storyline_id": None,
        "fork_op_seq": None,
        "head_op_seq": sl.head_seq,
        "forks": 0,
        "stars": 0,
        "created_at": sl.created_at.isoformat(),
        "updated_at": sl.updated_at.isoformat(),
    }


def graph_out(book: Book, sl: Storyline | None) -> dict[str, Any]:
    """The whole graph a reader sees, with the storyline's hidden items listed (not removed), so
    the UI can show or grey them out."""
    empty = {"entities": [], "events": [], "relations": [], "communities": [], "hidden": []}
    if book.status != "ready":
        return empty
    hidden: list[str] = []
    if sl is None:
        scope = GraphScope.canon(book.id)
    else:
        scope = GraphScope(book_id=book.id, sid=sl.id, max_op=sl.head_seq)
        ops = storylines.scope(sl)
        hidden = sorted(ops.hidden_nodes | ops.hidden_edges)
    return {
        "entities": [
            {"key": e.key, "name": e.name, "type": e.type, "aliases": e.aliases,
             "description": e.description, "first_page": e.first_page, "last_page": e.last_page,
             "mention_count": e.mention_count, "community_id": e.community_id, "sid": e.sid}
            for e in graph.all_entities(scope)
        ],  # fmt: skip
        "events": [
            {"key": e.key, "summary": e.summary, "story_pos": e.story_pos, "chapter": e.chapter,
             "first_page": e.first_page, "sid": e.sid, "involves": involved}
            for e, involved in graph.all_events(scope)
        ],  # fmt: skip
        "relations": [
            {"id": r.id, "src": r.src, "dst": r.dst, "rel": r.rel, "description": r.description,
             "weight": r.weight, "sid": r.sid}
            for r in graph.all_relations(scope)
        ],  # fmt: skip
        "communities": [
            {"cid": c.cid, "title": c.title, "summary": c.summary}
            for c in communities.list_communities(book.id)
        ],
        "hidden": hidden,
    }


def _quote(chunk_id: str, sl: Storyline | None) -> tuple[str, bool]:
    """The opening of a cited passage, and whether it is a rewritten chapter of the storyline."""
    chunk = chunks.get_chunk(chunk_id)
    if chunk is not None:
        return chunk.text[:QUOTE_CHARS], False
    if sl is not None and (found := storylines.get_passages(sl.id, [chunk_id])):
        return found[0].text[:QUOTE_CHARS], True
    return "", False


def citations_out(raw: list[dict[str, Any]], sl: Storyline | None) -> list[dict[str, Any]]:
    out = []
    for c in raw:
        quote, rewritten = _quote(c["chunk_id"], sl)
        item = {"chapter": c.get("chapter"), "page": c.get("page_start"), "quote": quote}
        if rewritten:
            item["storyline_chunk"] = True
        out.append(item)
    return out


def trace_out(question: str, tool_calls: list[dict], retrieval: dict[str, Any]) -> dict[str, Any] | None:
    """The reasoning panel's data. None when the turn used no tools."""
    if not tool_calls:
        return None
    return {
        "rewritten_question": question,
        "seeds": retrieval.get("seeds", []),
        "expanded": retrieval.get("expanded", []),
        "edges": [],
        "hops": retrieval.get("hops", 0),
        "communities": [],
        "steps": [
            {"label": label_for(t["tool"]).rstrip("…"), "detail": fields(t.get("args", {}))}
            for t in tool_calls
        ],
    }


def message_out(m: Message, sl: Storyline | None, question: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat(),
    }
    if m.role == "assistant":
        out["citations"] = citations_out(m.citations, sl)
        if m.route:
            out["route"] = m.route
        if trace := trace_out(question, m.trace, m.retrieval):
            out["trace"] = trace
        if m.diff:
            out["diff"] = m.diff
    return out


def diff_out(sl: Storyline, before: int) -> dict[str, Any] | None:
    """What the ops written since `before` changed, in the UI's terms. None if nothing changed."""
    ops = storylines.list_ops(sl.id, after_seq=before, upto_seq=sl.head_seq)
    if not ops:
        return None
    added = [o.payload["event"] for o in ops if o.kind == "add_event"]
    positions = [e["story_pos"] for e in added]
    divergence = sl.draft.divergence_pos if sl.draft else (min(positions) if positions else 0)
    return {
        "divergence_pos": divergence,
        "added": [e["key"] for e in added],
        "hidden": [o.payload["key"] for o in ops if o.kind == "hide_node"],
        "modified": [],
        "added_edges": [],
        "passage": "",
        "op_seq": sl.head_seq,
    }


# --- graph ---------------------------------------------------------------


@router.get("/books/{book_id}/graph")
def get_graph(
    book_id: str,
    user: Annotated[User | None, Depends(optional_user)],
    storyline_id: str | None = None,
) -> dict[str, Any]:
    book = _book_for(book_id, user)
    sl = _storyline_for(storyline_id, user) if storyline_id else None
    if sl is not None and sl.book_id != book_id:
        raise HTTPException(404, "storyline not found")
    return graph_out(book, sl)


# --- storylines ----------------------------------------------------------


class NewStoryline(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    premise: str = Field(default="", max_length=4000)


class StorylinePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    visibility: Literal["public", "private"] | None = None


@router.get("/books/{book_id}/storylines")
def list_book_storylines(
    book_id: str, user: Annotated[User | None, Depends(optional_user)]
) -> list[dict[str, Any]]:
    _book_for(book_id, user)
    found = {s.id: s for s in storylines.list_public(book_id)}
    if user:
        found.update({s.id: s for s in storylines.list_for_user(user.id, book_id)})
    return [storyline_out(s) for s in sorted(found.values(), key=lambda s: s.updated_at, reverse=True)]


@router.post("/books/{book_id}/storylines", status_code=201)
def create_storyline(
    book_id: str, body: NewStoryline, user: Annotated[User, Depends(current_user)]
) -> dict[str, Any]:
    book = _book_for(book_id, user)
    if book.status != "ready":
        raise HTTPException(409, "this book is still being read; try again when it is ready")
    sl = storylines.create_storyline(
        Storyline(book_id=book_id, owner_id=user.id, name=body.title.strip(), premise=body.premise.strip())
    )
    log.info("storyline created storyline=%s book=%s user=%s", sl.id, book_id, user.id)
    return storyline_out(sl)


@router.get("/storylines")
def list_storylines(
    user: Annotated[User | None, Depends(optional_user)],
    scope: Literal["public", "mine"] = "public",
) -> list[dict[str, Any]]:
    if scope == "mine":
        if user is None:
            raise HTTPException(401, "sign in required")
        return [storyline_out(s) for s in storylines.list_for_user(user.id)]
    return [storyline_out(s) for s in storylines.list_public()]


@router.get("/storylines/{storyline_id}")
def get_storyline(
    storyline_id: str, user: Annotated[User | None, Depends(optional_user)]
) -> dict[str, Any]:
    return storyline_out(_storyline_for(storyline_id, user))


@router.patch("/storylines/{storyline_id}")
def update_storyline(
    storyline_id: str, patch: StorylinePatch, user: Annotated[User, Depends(current_user)]
) -> dict[str, Any]:
    sl = _storyline_for(storyline_id, user, write=True)
    changes = {k: v for k, v in (("name", patch.title), ("visibility", patch.visibility)) if v is not None}
    if changes:
        storylines.update_fields(sl.id, **changes)
        log.info("storyline updated storyline=%s %s", sl.id, fields(changes))
    return storyline_out(storylines.get_storyline(sl.id))


@router.get("/storylines/{storyline_id}/checkpoints")
def list_checkpoints(
    storyline_id: str, user: Annotated[User | None, Depends(optional_user)]
) -> list[dict[str, Any]]:
    _storyline_for(storyline_id, user)
    return []  # none can be saved yet


@router.post("/storylines/{storyline_id}/checkpoints")
def save_checkpoint(storyline_id: str) -> None:
    raise _not_built("Saving checkpoints")


@router.post("/storylines/{storyline_id}/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(storyline_id: str, checkpoint_id: str) -> None:
    raise _not_built("Restoring checkpoints")


@router.post("/storylines/{storyline_id}/fork")
def fork_storyline(storyline_id: str) -> None:
    raise _not_built("Forking")


@router.post("/storylines/{storyline_id}/star")
def star_storyline(storyline_id: str) -> None:
    raise _not_built("Starring")


# --- chat ----------------------------------------------------------------


class ChatBody(BaseModel):
    storyline_id: str | None = None
    text: str = Field(min_length=1, max_length=4000)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _pieces(text: str) -> list[str]:
    words = text.split(" ")
    return [" ".join(words[i : i + TOKEN_WORDS]) + (" " if i + TOKEN_WORDS < len(words) else "")
            for i in range(0, len(words), TOKEN_WORDS)]  # fmt: skip


@router.get("/books/{book_id}/messages")
def get_messages(
    book_id: str,
    user: Annotated[User | None, Depends(optional_user)],
    storyline_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    book = _book_for(book_id, user)
    sl = _storyline_for(storyline_id, user) if storyline_id else None
    if user is None:
        return []
    session = sessions.find_session(user.id, book.id, sl.id if sl else None)
    if session is None:
        return []
    out, question = [], ""
    for m in sessions.last_messages(session.id, limit):
        out.append(message_out(m, sl, question))
        if m.role == "user":
            question = m.content
    return out


def _build_turn(book: Book, session_id: str, result: dict[str, Any], before: int) -> dict[str, Any]:
    """The `done` payload: both messages, the graph after the turn, and the storyline."""
    session = sessions.get_session(session_id)  # an edit may have bound the chat to a new storyline
    sl = storylines.get_storyline(session.storyline_id) if session and session.storyline_id else None
    ids = result["message_ids"]
    if sl and (diff := diff_out(sl, before)):
        sessions.update_message(ids["assistant"], diff=diff)
    asked, replied = sessions.get_message(ids["user"]), sessions.get_message(ids["assistant"])
    return {
        "user": message_out(asked, sl),
        "assistant": message_out(replied, sl, asked.content),
        "graph": graph_out(book, sl),
        "storyline": storyline_out(sl) if sl else None,
    }


async def _stream(book: Book, session: ChatSession, text: str, before: int) -> AsyncIterator[str]:
    events: asyncio.Queue = asyncio.Queue()

    async def work() -> None:
        try:
            result = await get_agent(AGENT).run(
                book.id, session.id, text, on_progress=lambda label: events.put_nowait(("phase", label))
            )
            await events.put(("result", result))
        except Exception as exc:  # already logged by the agent, with its traceback
            await events.put(("error", exc))

    task = asyncio.create_task(work())
    try:
        yield _sse("phase", {"label": "Opening the book…"})
        while True:
            kind, value = await events.get()
            if kind == "phase":
                yield _sse("phase", {"label": value})
            elif kind == "error":
                message = str(value) if isinstance(value, ValueError) else "Something went wrong while answering."
                yield _sse("error", {"detail": f"{message} (reference {request_id.get()})"})
                return
            else:
                break
        turn = await run_in_threadpool(_build_turn, book, session.id, value, before)
        for piece in _pieces(turn["assistant"]["content"]):
            yield _sse("token", {"text": piece})
        yield _sse("done", turn)
    finally:
        if not task.done():  # the reader went away
            task.cancel()
            log.info("chat stream closed early session=%s", session.id)


@router.post("/books/{book_id}/chat")
async def chat(
    book_id: str, body: ChatBody, user: Annotated[User, Depends(current_user)]
) -> StreamingResponse:
    book = _book_for(book_id, user)
    if book.status != "ready":
        raise HTTPException(409, "this book is still being read; try again when it is ready")
    sl = None
    if body.storyline_id:
        sl = _storyline_for(body.storyline_id, user, write=True)
        if sl.book_id != book_id:
            raise HTTPException(404, "storyline not found")
    sid = sl.id if sl else None
    session = sessions.find_session(user.id, book_id, sid) or sessions.create_session(
        ChatSession(book_id=book_id, user_id=user.id, storyline_id=sid)
    )
    log.info("chat user=%s book=%s storyline=%s session=%s", user.id, book_id, sid, session.id)
    return StreamingResponse(
        _stream(book, session, body.text.strip(), sl.head_seq if sl else 0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
