"""Storyline tools: explore what depends on a part of the story, then edit a draft of an
alternate version. The draft tools are the storyline editor's; the two read tools serve both agents.

Tools take entity keys or names and resolve them against the session's own view of the book. All
writes go through `app.storyline`, which enforces the rules: every queued item decided, and a
passing verification before a commit.
"""

import logging
from typing import Any

from app.constants import OPEN_ITEMS_PER_CALL, TOOL_MAX_TIMELINE_EVENTS
from app.llm import get_chat_model
from app.mcp_server import text_tools
from app.mcp_server.common import branch_scope, resolve, session_storyline
from app.repo import chunks, graph, sessions
from app.repo.models import ChatSession, Storyline, StorylineItem
from app.storyline import draft, ledger, prose, verify
from app.storyline.schema import Fact

log = logging.getLogger(__name__)


def _session(book_id: str, session_id: str) -> ChatSession:
    session_storyline(book_id, session_id)  # validates the book and the session
    return sessions.get_session(session_id)


def _storyline(book_id: str, session_id: str) -> Storyline:
    sl = session_storyline(book_id, session_id)
    if sl is None:
        raise ValueError("this chat has no storyline yet; call start_draft first")
    return sl


def _facts(scope: graph.GraphScope, facts: list[Fact] | None) -> list[Fact]:
    return [f.model_copy(update={"entity": resolve(scope, f.entity)["key"]}) for f in facts or []]


def _item_out(item: StorylineItem, chunk_ids: dict[int, str]) -> dict[str, Any]:
    return {
        "ref": item.ref,
        "kind": item.kind,
        "summary": item.summary,
        "chapter": item.chapter,
        "story_pos": item.pos,
        "reason": item.reason,
        "state": item.state,
        "chunk_id": chunk_ids.get(int(item.pos // 1000)),
    }


# --- exploring -----------------------------------------------------------


def get_dependents(
    book_id: str,
    entity: str,
    from_event: str | None = None,
    from_pos: float | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    scope = branch_scope(book_id, session_id)
    found = resolve(scope, entity)
    if from_event is not None:
        event = graph.get_event(scope, from_event)
        if event is None:
            raise ValueError(f"no visible event '{from_event}'")
        from_pos = event.story_pos
    start = from_pos if from_pos is not None else 0.0
    rows = [(e, role) for e, role in graph.entity_events(scope, found["key"]) if e.story_pos > start]
    shown = rows[:TOOL_MAX_TIMELINE_EVENTS]
    rels = [
        (r, other)
        for r, other in graph.entity_relations(scope, found["key"], limit=200)
        if r.story_pos is not None and r.story_pos > start
    ]
    by_chapter: dict[str, int] = {}
    for e, _ in rows:
        by_chapter[e.chapter or "?"] = by_chapter.get(e.chapter or "?", 0) + 1
    return {
        "entity": {"key": found["key"], "name": found["name"]},
        "after_pos": start,
        "events": [
            {"key": e.key, "summary": e.summary, "chapter": e.chapter, "story_pos": e.story_pos,
             "role": role}
            for e, role in shown
        ],  # fmt: skip
        "total_events": len(rows),
        "truncated": len(rows) > len(shown),
        "events_per_chapter": by_chapter,
        "relations": [
            {"rel": r.rel, "other": other.name, "direction": "out" if r.src == found["key"] else "in",
             "story_pos": r.story_pos}
            for r, other in sorted(rels, key=lambda x: x[0].story_pos)[:TOOL_MAX_TIMELINE_EVENTS]
        ],  # fmt: skip
    }


def get_state_at(
    book_id: str,
    pos: float,
    entities: list[str] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    scope = branch_scope(book_id, session_id)
    sl = session_storyline(book_id, session_id)
    if entities:
        keys = [resolve(scope, e)["key"] for e in entities]
    elif sl and sl.draft:
        keys = sl.draft.affected
    else:
        raise ValueError("name the entities to look at")
    return {"story_pos": pos, "states": ledger.state_at(scope, sl, keys, pos)}


# --- drafting ------------------------------------------------------------


def start_draft(
    book_id: str,
    session_id: str,
    anchor_event: str,
    premise: str,
    facts: list[Fact] | None = None,
) -> dict[str, Any]:
    session = _session(book_id, session_id)
    scope = branch_scope(book_id, session_id)
    if not premise.strip():
        raise ValueError("premise is empty")
    out = draft.start_draft(session, anchor_event, premise.strip(), _facts(scope, facts))
    # The chat is in the storyline now (created on first use): answer from it from here on.
    out["note"] = ("This chat now works in the storyline and the read tools answer from it. "
                   "Answer the reader from the storyline, not the original book.")
    return out


def get_open_items(
    book_id: str, session_id: str, limit: int = OPEN_ITEMS_PER_CALL
) -> dict[str, Any]:
    sl = _storyline(book_id, session_id)
    out = draft.open_items(sl, min(max(limit, 1), OPEN_ITEMS_PER_CALL))
    ids = chunks.ids_by_seq(book_id, {int(i.pos // 1000) for i in out["items"]})
    return {
        "items": [_item_out(i, ids) for i in out["items"]],
        "open": out["open"],
        "total": out["total"],
        "premise": sl.draft.premise,
    }


def resolve_item(
    book_id: str,
    session_id: str,
    ref: str,
    action: str,
    new_summary: str | None = None,
    participants: list[str] | None = None,
    affects: list[str] | None = None,
    facts: list[Fact] | None = None,
) -> dict[str, Any]:
    sl = _storyline(book_id, session_id)
    scope = branch_scope(book_id, session_id)
    keys = lambda refs: [resolve(scope, r)["key"] for r in refs]  # noqa: E731
    return draft.resolve_item(
        sl,
        ref,
        action,
        new_summary,
        keys(participants) if participants is not None else None,
        keys(affects or []),
        _facts(scope, facts),
    )


def verify_draft(book_id: str, session_id: str) -> dict[str, Any]:
    sl = _storyline(book_id, session_id)
    return verify.verify(sl, get_chat_model())


def commit_draft(book_id: str, session_id: str) -> dict[str, Any]:
    """Commit the verified draft, then write the new text of every chapter it changed, so the
    story reads the new way as well as being recorded that way. Writing is best effort: the commit
    stands if a chapter fails, and that chapter is written the next time it is read."""
    sl = _storyline(book_id, session_id)
    out = draft.commit(sl)
    try:
        written = prose.write_changed_chapters(draft.fresh(sl.id), get_chat_model())
    except Exception as exc:  # no model, a database error: the commit still stands
        log.exception("writing chapters after commit failed storyline=%s", sl.id)
        written = {"written": [], "failed": {"all": str(exc) or type(exc).__name__}, "unresolved": {}}
    return {**out, "chapters_written": written["written"], "chapters_failed": written["failed"],
            "chapters_with_problems": written["unresolved"]}  # fmt: skip


def discard_draft(book_id: str, session_id: str) -> dict[str, Any]:
    return draft.discard(_storyline(book_id, session_id))


# --- prose ---------------------------------------------------------------


def _chapter_base(book_id: str, chapter: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    info = text_tools.resolve_chapter(book_id, chapter)
    ids = [c.id for c in chunks.list_chunks(book_id, info["seq_start"], info["seq_end"])]
    shown = {"chapter": info["title"], "page_start": info["page_start"], "page_end": info["page_end"]}
    return info, shown, ids


def read_chapter(book_id: str, chapter: str, session_id: str | None = None) -> dict[str, Any]:
    """The chapter as this session's storyline tells it. A rewritten chapter comes with its text;
    an unchanged one (or any chapter in a canon chat) is the original, given as chunk ids."""
    sl = session_storyline(book_id, session_id)
    info, shown, ids = _chapter_base(book_id, chapter)
    original = {**shown, "source": "original", "chunk_ids": ids}
    if sl is None:
        return original
    view = prose.ChapterView(sl, info["title"])
    if not view.changed:
        return original
    passage = prose.current_passage(sl, view)
    if passage is None:
        if sl.draft is not None:  # the draft is still moving; do not write prose for it yet
            return {**original, "stale": True,
                    "note": "this chapter changed in the open draft and has no rewritten text yet; "
                            "call generate_passage to preview it"}  # fmt: skip
        passage = prose.generate_chapter(sl, info, get_chat_model())
    return {**shown, "source": "storyline", "chunk_id": passage.id, "text": passage.text,
            "issues": passage.issues}  # fmt: skip


def generate_passage(book_id: str, session_id: str, chapter: str) -> dict[str, Any]:
    sl = _storyline(book_id, session_id)
    info, shown, _ = _chapter_base(book_id, chapter)
    if not prose.ChapterView(sl, info["title"]).changed:
        raise ValueError(f"'{info['title']}' is unchanged in this storyline; there is nothing to rewrite")
    passage = prose.generate_chapter(sl, info, get_chat_model())
    return {**shown, "chunk_id": passage.id, "characters": len(passage.text),
            "preview": passage.text[:600], "issues": passage.issues}  # fmt: skip
