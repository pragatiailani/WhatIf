"""Branch prose: a rewritten passage per changed chapter, written from the verified events.

The graph stays the source of truth; prose is rendered from it. A chapter is *changed* when its
visible events differ from canon. Its passage is *current* while the chapter's visible events
still hash to the passage's fingerprint, and is regenerated after further edits. Each passage is
checked against the facts and events before it is stored; contradictions found are fed back for
one retry, and any that remain are stored with the passage instead of being hidden.
"""

import hashlib
import logging
import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.constants import PROSE_CONTEXT_CHARS, PROSE_MAX_RETRIES, PROSE_SEGMENT_CHARS
from app.repo import chunks, graph, storylines
from app.repo.graph import GraphScope
from app.repo.models import Chunk, GraphEvent, PageSpan, Storyline, StorylineChunk
from app.storyline import ledger
from app.storyline.prose_prompt import (
    PROSE_CHECK_SYSTEM,
    PROSE_SYSTEM,
    prose_check_user,
    prose_user,
)
from app.storyline.schema import Continuity

log = logging.getLogger(__name__)


class ChapterView:
    """What a storyline does to one chapter: its events now, the ones removed, and a fingerprint."""

    def __init__(self, sl: Storyline, chapter: str):
        self.scope = storylines.scope(sl)
        self.chapter = chapter
        self.now: list[tuple[GraphEvent, list[str]]] = graph.chapter_events(self.scope, chapter)
        canon = graph.chapter_events(GraphScope.canon(sl.book_id), chapter)
        keys = {e.key for e, _ in self.now}
        self.removed = [e for e, _ in canon if e.key not in keys]
        self.changed = keys != {e.key for e, _ in canon}
        self.fingerprint = hashlib.sha1("|".join(sorted(keys)).encode()).hexdigest()


def current_passage(sl: Storyline, view: ChapterView) -> StorylineChunk | None:
    passage = storylines.get_passage(sl.id, view.chapter)
    return passage if passage and passage.fingerprint == view.fingerprint else None


def _event_lines(view: ChapterView) -> tuple[list[str], list[str]]:
    keys = sorted({k for _, ks in view.now for k in ks})
    names = {e.key: e.name for e in graph.get_entities(view.scope, keys)}
    now = [
        f"- {'[NEW] ' if e.sid else ''}{e.summary}"
        + (f" ({', '.join(names.get(k, k) for k in ks)})" if ks else "")
        for e, ks in view.now
    ]
    return now, [f"- {e.summary}" for e in view.removed]


def _facts_text(sl: Storyline, view: ChapterView) -> str:
    facts = ledger.facts_at(sl)
    names = {e.key: e.name for e in graph.get_entities(view.scope, list(facts))}
    return "\n".join(
        f"- {names.get(k, k)}: " + ", ".join(f"{a} = {v}" for a, v in attrs.items())
        for k, attrs in facts.items()
    )


def _segments(originals: list[Chunk]) -> list[str]:
    """The chapter's original text in pieces of about PROSE_SEGMENT_CHARS, split between chunks."""
    pieces: list[str] = []
    current = ""
    for chunk in originals:
        if current and len(current) + len(chunk.text) > PROSE_SEGMENT_CHARS:
            pieces.append(current)
            current = ""
        current += ("\n\n" if current else "") + chunk.text
    if current:
        pieces.append(current)
    return pieces


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in content)


def _rewrite(
    llm: BaseChatModel, sl: Storyline, view: ChapterView, originals: list[Chunk], feedback: list[str]
) -> str:
    now, removed = _event_lines(view)
    facts = _facts_text(sl, view)
    premise = sl.draft.premise if sl.draft else ""
    parts: list[str] = []
    for piece in _segments(originals):
        tail = parts[-1][-PROSE_CONTEXT_CHARS:] if parts else ""
        user = prose_user(premise, facts, view.chapter, now, removed, piece, tail, feedback)
        reply = llm.invoke([SystemMessage(PROSE_SYSTEM), HumanMessage(user)])
        parts.append(_text(reply.content).strip())
    return "\n\n".join(p for p in parts if p)


def _check(llm: BaseChatModel, sl: Storyline, view: ChapterView, text: str) -> list[str]:
    now, removed = _event_lines(view)
    judge = llm.with_structured_output(Continuity, method="function_calling")
    user = prose_check_user(_facts_text(sl, view), now, removed, text)
    result: Continuity = judge.invoke([SystemMessage(PROSE_CHECK_SYSTEM), HumanMessage(user)])
    return [i.problem for i in result.issues]


def generate_chapter(
    sl: Storyline, chapter: dict[str, Any], llm: BaseChatModel
) -> StorylineChunk:
    """Write, check and store the passage for `chapter` (a `chunks.list_chapters` entry)."""
    view = ChapterView(sl, chapter["title"])
    originals = chunks.list_chunks(sl.book_id, chapter["seq_start"], chapter["seq_end"])
    if not originals:
        raise ValueError(f"chapter '{chapter['title']}' has no text")
    started = time.perf_counter()
    log.info("writing chapter storyline=%s chapter=%s chunks=%d removed_events=%d",
             sl.id, view.chapter, len(originals), len(view.removed))
    feedback: list[str] = []
    for attempt in range(1, PROSE_MAX_RETRIES + 2):
        text = _rewrite(llm, sl, view, originals, feedback)
        if not text:
            raise ValueError("the model returned no text")
        issues = _check(llm, sl, view, text)
        if not issues:
            break
        log.warning("chapter check found %d problem(s) storyline=%s chapter=%s attempt=%d",
                    len(issues), sl.id, view.chapter, attempt)
        feedback = issues
    spans = [s for c in originals for s in c.page_spans]
    log.info("chapter written storyline=%s chapter=%s chars=%d unresolved_problems=%d in %.1fs",
             sl.id, view.chapter, len(text), len(issues), time.perf_counter() - started)
    return storylines.save_passage(
        StorylineChunk(
            storyline_id=sl.id,
            book_id=sl.book_id,
            chapter=view.chapter,
            text=text,
            fingerprint=view.fingerprint,
            op_seq=sl.head_seq,
            seq_start=chapter["seq_start"],
            seq_end=chapter["seq_end"],
            page_spans=[PageSpan.model_validate(s) for s in spans],
            issues=issues,
        )
    )
