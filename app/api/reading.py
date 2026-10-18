"""The reading page's API: a book's graph, its storylines, and asking questions about it.

Shapes follow the web app's contract (`web/src/api/types.ts`). Each signed-in user has one
conversation per book, and one per storyline they own; asking streams Server-Sent Events: `phase`
{label} while the agent works, `step` {label, detail, why, found} as each tool returns, `token`
{text} as the answer is written, `reasoning` {chain, conclusion} (why the answer holds, when the
graph backs it), then `done` with the saved turn, or `error` {detail}. A `phase` after tokens means
those tokens were the model's reasoning (now in a step's `why`), not the answer.

A reader's "what if" is an edit the agent makes with its storyline tools. The first one made in a
canon chat creates a storyline and binds the chat to it, so `done` carries that `storyline`, and
the `graph` after the edit and a `diff` of what changed. Not built yet, and answered with 501:
forking, starring, and saving or restoring checkpoints.
"""

import json
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import book_qa
from app.api.auth import current_user
from app.logging_config import fields
from app.repo import GraphScope, books, communities, graph, sessions, storylines, users
from app.repo.models import Book, Message, Storyline, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/books", tags=["reading"], dependencies=[Depends(current_user)])
storylines_router = APIRouter(
    prefix="/storylines", tags=["reading"], dependencies=[Depends(current_user)]
)

HISTORY_LIMIT = 200
ANSWER_FAILED = "Something went wrong while answering. Please try again."


class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    storyline_id: str | None = None


class NewStoryline(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    premise: str = Field(default="", max_length=4000)


class StorylinePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    visibility: Literal["public", "private"] | None = None


def _readable(book_id: str, user: User) -> Book:
    """The book if `user` may read it (public, theirs, or shared with them); 404 otherwise."""
    book = books.get_book(book_id)
    allowed = book is not None and (
        book.visibility == "public" or book.owner_id == user.id or user.id in book.shared_with
    )
    if not allowed:
        raise HTTPException(404, "book not found")
    return book


def _ready(book: Book) -> None:
    if book.status != "ready":
        raise HTTPException(409, "This book is still being read. Try again when it is ready.")


def _storyline(storyline_id: str, user: User, *, write: bool = False) -> Storyline:
    """The storyline if `user` may see it (theirs, or made public); 404 otherwise. With `write`,
    only its owner may use it: anyone else is told to fork it."""
    sl = storylines.get_storyline(storyline_id)
    mine = sl is not None and sl.owner_id == user.id
    if sl is None or not (mine or sl.visibility == "public"):
        raise HTTPException(404, "storyline not found")
    if write and not mine:
        raise HTTPException(403, "This storyline belongs to someone else. Fork it to edit.")
    return sl


def _of_book(book_id: str, user: User, storyline_id: str | None) -> Storyline | None:
    """The requested storyline, which must belong to this book. None for canon."""
    if not storyline_id:
        return None
    sl = _storyline(storyline_id, user)
    if sl.book_id != book_id:
        raise HTTPException(404, "storyline not found")
    return sl


def _not_built(what: str) -> HTTPException:
    return HTTPException(501, f"{what} is not available yet.")


def storyline_out(sl: Storyline) -> dict[str, Any]:
    """A storyline as the web app's Storyline."""
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


# --- graph and storylines -------------------------------------------------


def graph_out(book_id: str, sl: Storyline | None) -> dict[str, Any]:
    """The graph a reader sees: canon, or canon plus the storyline's changes. What the storyline
    hid is still listed (in `hidden`), so the page can show it greyed out."""
    hidden: list[str] = []
    if sl is None:
        scope = GraphScope.canon(book_id)
    else:
        scope = GraphScope(book_id=book_id, sid=sl.id, max_op=sl.head_seq)  # hidden items included
        ops = storylines.scope(sl)
        hidden = sorted(ops.hidden_nodes | ops.hidden_edges)
    entities, events, relations = graph.book_graph(scope)
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
        "hidden": hidden,
    }


@router.get("/{book_id}/graph")
def get_graph(
    book_id: str,
    user: Annotated[User, Depends(current_user)],
    storyline_id: str | None = None,
) -> dict[str, Any]:
    book = _readable(book_id, user)
    sl = _of_book(book_id, user, storyline_id)
    _ready(book)
    return graph_out(book_id, sl)


@router.get("/{book_id}/storylines")
def list_storylines(
    book_id: str, user: Annotated[User, Depends(current_user)]
) -> list[dict[str, Any]]:
    """The reader's own storylines on this book plus other people's public ones."""
    _readable(book_id, user)
    found = {s.id: s for s in storylines.list_public(book_id)}
    found.update({s.id: s for s in storylines.list_for_user(user.id, book_id)})
    return [storyline_out(s) for s in sorted(found.values(), key=lambda s: s.updated_at, reverse=True)]


@router.post("/{book_id}/storylines", status_code=201)
def create_storyline(
    book_id: str, body: NewStoryline, user: Annotated[User, Depends(current_user)]
) -> dict[str, Any]:
    """Start an empty storyline. The reader's first message in it (the UI sends the premise) is
    where the agent explores the change and drafts it."""
    _ready(_readable(book_id, user))
    sl = storylines.create_storyline(
        Storyline(book_id=book_id, owner_id=user.id, name=body.title.strip(), premise=body.premise.strip())
    )
    log.info("storyline created storyline=%s book=%s user=%s", sl.id, book_id, user.id)
    return storyline_out(sl)


@storylines_router.get("")
def list_all_storylines(
    user: Annotated[User, Depends(current_user)],
    scope: Literal["public", "mine"] = "public",
) -> list[dict[str, Any]]:
    if scope == "mine":
        return [storyline_out(s) for s in storylines.list_for_user(user.id)]
    return [storyline_out(s) for s in storylines.list_public()]


@storylines_router.get("/{storyline_id}")
def get_storyline(
    storyline_id: str, user: Annotated[User, Depends(current_user)]
) -> dict[str, Any]:
    return storyline_out(_storyline(storyline_id, user))


@storylines_router.patch("/{storyline_id}")
def update_storyline(
    storyline_id: str, patch: StorylinePatch, user: Annotated[User, Depends(current_user)]
) -> dict[str, Any]:
    sl = _storyline(storyline_id, user, write=True)
    changes = {k: v for k, v in (("name", patch.title), ("visibility", patch.visibility)) if v is not None}
    if changes:
        storylines.update_fields(sl.id, **changes)
        log.info("storyline updated storyline=%s %s", sl.id, fields(changes))
    return storyline_out(storylines.get_storyline(sl.id))


@storylines_router.get("/{storyline_id}/checkpoints")
def list_checkpoints(
    storyline_id: str, user: Annotated[User, Depends(current_user)]
) -> list[dict[str, Any]]:
    _storyline(storyline_id, user)
    return []  # none can be saved yet


@storylines_router.post("/{storyline_id}/checkpoints")
def save_checkpoint(storyline_id: str) -> None:
    raise _not_built("Saving checkpoints")


@storylines_router.post("/{storyline_id}/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(storyline_id: str, checkpoint_id: str) -> None:
    raise _not_built("Restoring checkpoints")


@storylines_router.post("/{storyline_id}/fork")
def fork_storyline(storyline_id: str) -> None:
    raise _not_built("Forking")


@storylines_router.post("/{storyline_id}/star")
def star_storyline(storyline_id: str) -> None:
    raise _not_built("Starring")


# --- chat ------------------------------------------------------------------


def _step(t: dict[str, Any]) -> dict[str, str]:
    """A trace entry as the web app's step. Turns saved before `why`/`found` have them empty."""
    label = book_qa.PHASES.get(t["tool"], t["tool"]).rstrip("…")
    args = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in t["args"].items())
    return {
        "label": label + (" (failed)" if t["error"] else ""),
        "detail": args,
        "tool": t["tool"],
        "why": t.get("why", ""),
        "found": t.get("found", ""),
    }


def message_out(m: Message, question: str = "") -> dict[str, Any]:
    """A saved message as the web app's Message. `question` is the user message it answers."""
    out: dict[str, Any] = {
        "id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat(),
    }  # fmt: skip
    if m.role != "assistant":
        return out
    citations = []
    for c in m.citations:
        item = {"chapter": c.get("chapter"), "page": c.get("page_start"), "quote": c.get("quote", "")}
        if c.get("storyline_chunk"):  # text the storyline wrote, not a page of the book
            item["storyline_chunk"] = True
        citations.append(item)
    out["citations"] = citations
    tools = {t["tool"] for t in m.trace}
    out["route"] = m.route or ("global" if "search_communities" in tools else "local")
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
        "reasoning": m.reasoning,
    }
    if m.diff:
        out["diff"] = m.diff
    return out


@router.get("/{book_id}/messages")
def get_messages(
    book_id: str,
    user: Annotated[User, Depends(current_user)],
    storyline_id: str | None = None,
) -> list[dict[str, Any]]:
    """The user's conversation on this book (or in one of their storylines), oldest first."""
    _readable(book_id, user)
    sl = None
    if storyline_id:
        mine = storylines.get_storyline(storyline_id)
        if mine is None or mine.book_id != book_id or mine.owner_id != user.id:
            return []  # nobody else's chat is theirs to read
        sl = mine
    session = sessions.find_book_chat(user.id, book_id, sl.id if sl else None)
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
    sl = None
    if body.storyline_id:
        sl = _storyline(body.storyline_id, user, write=True)
        if sl.book_id != book_id:
            raise HTTPException(404, "storyline not found")
    _ready(book)
    session = sessions.book_chat(user.id, book_id, sl.id if sl else None)

    async def events():
        try:
            async for kind, value in book_qa.stream(book_id, session.id, body.text):
                if kind == "phase":
                    yield _sse("phase", {"label": value})
                elif kind == "step":
                    yield _sse("step", _step(value))
                elif kind == "reasoning":
                    yield _sse("reasoning", value)
                elif kind == "token":
                    yield _sse("token", {"text": value})
                else:
                    yield _sse("done", _turn(book_id, body.text, value))
        except Exception:
            log.exception("answering on book %s failed", book_id)
            yield _sse("error", {"detail": ANSWER_FAILED})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _turn(book_id: str, question: str, result: dict[str, Any]) -> dict[str, Any]:
    """The `done` payload. An edit changes the storyline, so the graph after it is sent too; a
    chat that just made its first edit is now bound to a new storyline, which is sent as well."""
    assistant = result["assistant_message"]
    line = storylines.get_storyline(result["storyline_id"]) if result.get("storyline_id") else None
    turn: dict[str, Any] = {
        "user": message_out(result["user_message"]),
        "assistant": message_out(assistant, question),
        "storyline": storyline_out(line) if line else None,
        "actions": result["actions"],
    }
    if line and assistant.diff:
        turn["graph"] = graph_out(book_id, line)
    return turn
