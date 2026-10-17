"""Text tools: passages, keyword search, chapter summaries and entity timelines."""

import re
from typing import Any

from app.constants import (
    TOOL_MAX_CHUNKS_PER_CALL,
    TOOL_MAX_RESULTS,
    TOOL_MAX_TIMELINE_EVENTS,
    TOOL_SNIPPET_CHARS,
)
from app.mcp_server.common import canon_scope, entity_out, resolve
from app.mcp_server.matching import content_words, score_text, words
from app.repo import chunks, graph
from app.repo.models import Chunk, GraphEvent

MAX_CHAPTER_LISTING = 60


def _pages(chunk: Chunk) -> list[int]:
    return sorted({s.page for s in chunk.page_spans})


def _chunk_ref(chunk: Chunk) -> dict[str, Any]:
    pages = _pages(chunk)
    return {
        "chunk_id": chunk.id,
        "seq": chunk.seq,
        "chapter": chunk.chapter,
        "page_start": pages[0] if pages else None,
        "page_end": pages[-1] if pages else None,
    }


def get_chunks(book_id: str, chunk_ids: list[str]) -> dict[str, Any]:
    canon_scope(book_id)
    if len(chunk_ids) > TOOL_MAX_CHUNKS_PER_CALL:
        raise ValueError(f"ask for at most {TOOL_MAX_CHUNKS_PER_CALL} chunks per call")
    found = [c for c in chunks.get_chunks(chunk_ids) if c.book_id == book_id]
    got = {c.id for c in found}
    return {
        "chunks": [{**_chunk_ref(c), "text": c.text} for c in found],
        "missing": [i for i in chunk_ids if i not in got],
    }


def _snippet(text: str, terms: list[str]) -> str:
    """About TOOL_SNIPPET_CHARS of text around the earliest query word."""
    hits = [m.start() for t in terms if (m := re.search(re.escape(t), text, re.IGNORECASE))]
    centre = min(hits) if hits else 0
    start = max(0, centre - TOOL_SNIPPET_CHARS // 4)
    end = min(len(text), start + TOOL_SNIPPET_CHARS)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _chapters(book_id: str) -> list[dict[str, Any]]:
    return chunks.list_chapters(book_id)


def _resolve_chapter(book_id: str, name: str) -> dict[str, Any]:
    """Match a chapter by exact title, by number ('3' -> 'Chapter 3'), then by substring."""
    chapters = _chapters(book_id)
    wanted = name.strip().lower()
    number = re.fullmatch(r"(?:chapter\s+)?(\d+)", wanted)
    for matches in (
        lambda c: c["title"].lower() == wanted,
        lambda c: bool(number) and number.group(1) in re.findall(r"\d+", c["title"]),
        lambda c: wanted in c["title"].lower(),
    ):
        hit = [c for c in chapters if matches(c)]
        if len(hit) == 1:
            return hit[0]
        if len(hit) > 1:
            options = ", ".join(c["title"] for c in hit[:8])
            raise ValueError(f"'{name}' matches several chapters: {options}")
    titles = ", ".join(c["title"] for c in chapters[:MAX_CHAPTER_LISTING]) or "(none)"
    raise ValueError(f"no chapter matches '{name}'. Chapters: {titles}")


def search_chunks(
    book_id: str, query: str, chapter: str | None = None, limit: int = 5
) -> dict[str, Any]:
    canon_scope(book_id)
    terms = list(dict.fromkeys(content_words(query)))
    if not terms:
        raise ValueError("query has no searchable words")
    title = _resolve_chapter(book_id, chapter)["title"] if chapter else None
    phrase = " ".join(words(query))
    scored = []
    for c in chunks.search(book_id, terms, chapter=title):
        score = score_text(c.text, terms, phrase)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].seq))
    top = scored[: min(max(limit, 1), TOOL_MAX_RESULTS)]
    return {
        "results": [
            {**_chunk_ref(c), "score": round(score, 3), "snippet": _snippet(c.text, terms)}
            for score, c in top
        ],
        "total_matches": len(scored),
    }


def _event_out(event: GraphEvent, chunk_ids: dict[int, str]) -> dict[str, Any]:
    seq = int(event.story_pos // 1000)
    return {
        "key": event.key,
        "summary": event.summary,
        "chapter": event.chapter,
        "page": event.first_page,
        "story_pos": event.story_pos,
        "story_time": event.story_time,
        "chunk_seq": seq,
        "chunk_id": chunk_ids.get(seq),
    }


def get_timeline(book_id: str, entity: str) -> dict[str, Any]:
    scope = canon_scope(book_id)
    found = resolve(scope, entity)
    rows = graph.entity_events(scope, found["key"])
    shown = rows[:TOOL_MAX_TIMELINE_EVENTS]
    ids = chunks.ids_by_seq(book_id, {int(e.story_pos // 1000) for e, _ in shown})
    return {
        "entity": {"key": found["key"], "name": found["name"]},
        "events": [{**_event_out(e, ids), "role": role} for e, role in shown],
        "total": len(rows),
        "truncated": len(rows) > len(shown),
    }


def get_chapter_summary(book_id: str, chapter: str) -> dict[str, Any]:
    """The chapter's span plus its events and main entities, for the agent to summarise."""
    scope = canon_scope(book_id)
    info = _resolve_chapter(book_id, chapter)
    rows = graph.chapter_events(scope, info["title"])
    shown = rows[:TOOL_MAX_TIMELINE_EVENTS]

    counts: dict[str, int] = {}
    for _, keys in rows:
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    top_keys = sorted(counts, key=lambda k: (-counts[k], k))[:10]
    top = {e.key: e for e in graph.get_entities(scope, top_keys)}
    in_chapter = chunks.list_chunks(book_id, info["seq_start"], info["seq_end"])
    ids = {c.seq: c.id for c in in_chapter}
    names = {e.key: e.name for e in top.values()}
    return {
        "chapter": info["title"],
        "page_start": info["page_start"],
        "page_end": info["page_end"],
        "chunk_ids": [c.id for c in in_chapter],
        "main_entities": [
            {**entity_out(top[k]), "events_in_chapter": counts[k]} for k in top_keys if k in top
        ],
        "events": [
            {**_event_out(e, ids), "main_participants": [names[k] for k in keys if k in names]}
            for e, keys in shown
        ],
        "total_events": len(rows),
        "truncated": len(rows) > len(shown),
    }
