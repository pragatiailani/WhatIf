"""The reading page's API: a book's graph, its storylines, and asking questions about it.

Shapes follow the web app's contract (`web/src/api/types.ts`). Each signed-in user can keep many
chats on a book, and in each storyline they own; a question goes to the chat named by `chat_id`, a
new one with `new_chat`, else their most recently active one. Asking streams Server-Sent Events: `phase`
{label} while the agent works, `step` {label, detail, why, found} as each tool returns, `token`
{text} as the answer is written, `reasoning` {chain, conclusion} (why the answer holds, when the
graph backs it), then `done` with the saved turn and its `chat`, or `error` {detail}. A `phase` after tokens means
those tokens were the model's reasoning (now in a step's `why`), not the answer.

A reader's "what if" is an edit the agent makes with its storyline tools. The first one made in a
canon chat creates a storyline and binds the chat to it, so `done` carries that `storyline`. An edit
is drafted first: once the draft is verified, the latest answer carries a `proposal` (what would
change, and the graph it would give) for the reader to accept or discuss. Others see only what
was committed; the storyline's owner also sees their open draft as it is worked out, so every turn
in a storyline carries the `graph` after it, and the turn that commits a `diff` of what changed.
Not built yet, and answered with 501:
forking, starring, and saving or restoring checkpoints.
"""

import json
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import book_qa
from app.storyline import draft
from app.storyline.diff import proposal_diff
from app.api.auth import current_user
from app.logging_config import fields
from app.repo import GraphScope, books, communities, graph, sessions, storylines, users
from app.repo.models import Book, ChatSession, Message, Storyline, User

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
    chat_id: str | None = None  # the chat to continue
    new_chat: bool = False  # start a new chat instead of continuing the latest one
    auto: bool = False  # accept all: settle the whole draft without asking (see book_qa.stream)


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
    only its owner may use it."""
    sl = storylines.get_storyline(storyline_id)
    mine = sl is not None and sl.owner_id == user.id
    if sl is None or not (mine or sl.visibility == "public"):
        raise HTTPException(404, "storyline not found")
    if write and not mine:
        raise HTTPException(403, "This storyline belongs to someone else. Only its owner can change it.")
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
        "head_op_seq": sl.committed_seq,
        "forks": 0,
        "stars": 0,
        "created_at": sl.created_at.isoformat(),
        "updated_at": sl.updated_at.isoformat(),
    }


# --- graph and storylines -------------------------------------------------


def graph_out(book_id: str, sl: Storyline | None, *, with_draft: bool = False) -> dict[str, Any]:
    """The graph a reader sees: canon, or canon plus the storyline's committed changes (and its
    open draft's, `with_draft`). What the storyline hid is still listed (in `hidden`), so the page
    can show it greyed out."""
    hidden: list[str] = []
    if sl is None:
        scope = GraphScope.canon(book_id)
    else:
        ops = storylines.scope(sl, committed_only=not with_draft)
        scope = GraphScope(book_id=book_id, sid=sl.id, max_op=ops.max_op)  # hidden items included
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
    return graph_out(book_id, sl, with_draft=sl is not None and sl.owner_id == user.id)


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


def chat_out(s: ChatSession) -> dict[str, Any]:
    """A chat session as the web app's ChatSummary."""
    return {
        "id": s.id,
        "storyline_id": s.storyline_id,
        "title": s.title,
        "created_at": s.created_at.isoformat(),
        "last_active": s.last_active.isoformat(),
    }


def _chats(book_id: str, user: User, storyline_id: str | None) -> list[ChatSession]:
    """The user's chats on this book, in canon or one of their storylines, most recently active
    first. Nobody else's storyline holds chats of theirs."""
    _readable(book_id, user)
    if storyline_id:
        sl = storylines.get_storyline(storyline_id)
        if sl is None or sl.book_id != book_id or sl.owner_id != user.id:
            return []
    return sessions.list_sessions(user.id, book_id, storyline_id or None)


@router.get("/{book_id}/chats")
def list_chats(
    book_id: str,
    user: Annotated[User, Depends(current_user)],
    storyline_id: str | None = None,
) -> list[dict[str, Any]]:
    return [chat_out(s) for s in _chats(book_id, user, storyline_id)]


@router.get("/{book_id}/messages")
def get_messages(
    book_id: str,
    user: Annotated[User, Depends(current_user)],
    storyline_id: str | None = None,
    chat_id: str | None = None,
) -> list[dict[str, Any]]:
    """One of the user's chats on this book (or in one of their storylines), oldest first: the one
    named by `chat_id`, else the most recently active."""
    chats = _chats(book_id, user, storyline_id)
    if chat_id:
        session = next((c for c in chats if c.id == chat_id), None)
        if session is None:
            raise HTTPException(404, "chat not found")
    elif not chats:
        return []
    else:
        session = chats[0]
    out, question = [], ""
    for m in sessions.last_messages(session.id, HISTORY_LIMIT):
        out.append(message_out(m, question))
        question = m.content if m.role == "user" else question
    line = storylines.get_storyline(session.storyline_id) if session.storyline_id else None
    _with_proposal(out, proposal_out(book_id, line))
    return out


def proposal_out(book_id: str, sl: Storyline | None) -> dict[str, Any] | None:
    """The storyline's verified draft, waiting for the reader to accept or discuss it: what it
    would change and the graph it would give. None when nothing is waiting."""
    if sl is None or not draft.awaiting_approval(sl):
        return None
    return {
        "storyline_id": sl.id,
        "premise": sl.draft.premise,
        "diff": proposal_diff(sl),
        "graph": graph_out(book_id, sl, with_draft=True),
    }


def _with_proposal(messages: list[dict[str, Any]], proposal: dict[str, Any] | None) -> None:
    """Put the waiting proposal on the latest answer, where the reader accepts or discusses it."""
    last = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
    if proposal and last:
        last["proposal"] = proposal


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
    session = _chat_for(body, user, book_id, sl.id if sl else None)

    async def events():
        try:
            async for kind, value in book_qa.stream(book_id, session.id, body.text, auto=body.auto):
                if kind == "phase":
                    yield _sse("phase", {"label": value})
                elif kind == "step":
                    yield _sse("step", _step(value))
                elif kind == "reasoning":
                    yield _sse("reasoning", value)
                elif kind == "token":
                    yield _sse("token", {"text": value})
                else:
                    yield _sse("done", _turn(book_id, session.id, body.text, value))
        except Exception:
            log.exception("answering on book %s failed", book_id)
            yield _sse("error", {"detail": ANSWER_FAILED})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _chat_for(body: Ask, user: User, book_id: str, storyline_id: str | None) -> ChatSession:
    """The chat a question goes to: the one named, a new one, or the user's latest there."""
    if body.chat_id:
        s = sessions.get_session(body.chat_id)
        if s is None or s.user_id != user.id or s.book_id != book_id or s.storyline_id != storyline_id:
            raise HTTPException(404, "chat not found")
        return s
    latest = None if body.new_chat else sessions.find_session(user.id, book_id, storyline_id)
    return latest or sessions.create_session(
        ChatSession(user_id=user.id, book_id=book_id, storyline_id=storyline_id)
    )


def _turn(book_id: str, chat_id: str, question: str, result: dict[str, Any]) -> dict[str, Any]:
    """The `done` payload. A turn in a storyline may have changed it, so the graph after it
    (open draft included) is sent too; a chat that just made its first edit is now bound to a new
    storyline, which is sent as well."""
    assistant = result["assistant_message"]
    line = storylines.get_storyline(result["storyline_id"]) if result.get("storyline_id") else None
    chat = sessions.get_session(chat_id)  # reread: the turn may have titled it or bound a storyline
    answer = message_out(assistant, question)
    _with_proposal([answer], proposal_out(book_id, line))
    turn: dict[str, Any] = {
        "user": message_out(result["user_message"]),
        "assistant": answer,
        "storyline": storyline_out(line) if line else None,
        "chat": chat_out(chat) if chat else None,
        "actions": result["actions"],
    }
    if line:  # the asker owns the storyline, so their open draft shows as it grows
        turn["graph"] = graph_out(book_id, line, with_draft=True)
    return turn
