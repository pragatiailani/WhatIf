"""Text tools: passages, hybrid (keyword + meaning) search, chapter summaries and timelines."""

import re
from collections.abc import Sequence
from typing import Any

from app import retrieval
from app.constants import (
    TOOL_MAX_CHUNKS_PER_CALL,
    TOOL_MAX_RESULTS,
    TOOL_MAX_TIMELINE_EVENTS,
    TOOL_SNIPPET_CHARS,
)
from app.mcp_server.common import branch_scope, entity_out, resolve, session_storyline
from app.mcp_server.matching import content_words, score_text, words
from app.repo import chunks, graph, storylines
from app.repo.models import Chunk, GraphEvent, Storyline
from app.storyline.prose import ChapterView

MAX_CHAPTER_LISTING = 60


def _pages(chunk: Chunk) -> list[int]:
    return sorted({s.page for s in chunk.page_spans})


def chunk_ref(chunk: Chunk) -> dict[str, Any]:
    pages = _pages(chunk)
    return {
        "chunk_id": chunk.id,
        "seq": chunk.seq,
        "chapter": chunk.chapter,
        "page_start": pages[0] if pages else None,
        "page_end": pages[-1] if pages else None,
    }


class _ChapterViews:
    """A storyline's chapters compared with canon, each built at most once per call."""

    def __init__(self, sl: Storyline | None):
        self.sl = sl
        self._views: dict[str, ChapterView] = {}

    def __getitem__(self, chapter: str) -> ChapterView:
        if chapter not in self._views:
            self._views[chapter] = ChapterView(self.sl, chapter)
        return self._views[chapter]


def superseded_note(sl: Storyline | None, views: _ChapterViews, chunk: Chunk, rewritten: set[str]) -> dict[str, str]:
    """`{"superseded": ...}` for an original passage of a chapter the storyline changed: its text
    no longer reads that way. Nothing for canon, for unchanged chapters and for rewritten ones."""
    if sl and chunk.id not in rewritten and chunk.chapter and views[chunk.chapter].changed:
        return {"superseded": "this chapter was changed in the storyline; use read_chapter"}
    return {}


def get_chunks(
    book_id: str, chunk_ids: list[str], session_id: str | None = None
) -> dict[str, Any]:
    sl = session_storyline(book_id, session_id)
    if len(chunk_ids) > TOOL_MAX_CHUNKS_PER_CALL:
        raise ValueError(f"ask for at most {TOOL_MAX_CHUNKS_PER_CALL} chunks per call")
    by_id = {c.id: c for c in chunks.get_chunks(chunk_ids) if c.book_id == book_id}
    rewritten: set[str] = set()
    if sl:  # ids the original text does not have may be this storyline's rewritten chapters
        passages = storylines.get_passages(sl.id, [i for i in chunk_ids if i not in by_id])
        rewritten = {p.id for p in passages}
        by_id |= {p.id: p.as_chunk() for p in passages}
    views = _ChapterViews(sl)
    return {
        "chunks": [
            {**chunk_ref(by_id[i]), "text": by_id[i].text, **superseded_note(sl, views, by_id[i], rewritten)}
            for i in chunk_ids
            if i in by_id
        ],
        "missing": [i for i in chunk_ids if i not in by_id],
    }


def snippet(text: str, terms: list[str], size: int = TOOL_SNIPPET_CHARS) -> str:
    """About `size` characters of text around the earliest query word (the start without one)."""
    hits = [m.start() for t in terms if (m := re.search(re.escape(t), text, re.IGNORECASE))]
    centre = min(hits) if hits else 0
    start = max(0, centre - size // 4)
    end = min(len(text), start + size)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _chapters(book_id: str) -> list[dict[str, Any]]:
    return chunks.list_chapters(book_id)


def resolve_chapter(book_id: str, name: str) -> dict[str, Any]:
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


def rank_chunks(
    book_id: str,
    query: str,
    chapter: str | None = None,
    query_vec: Any = None,
    extra: Sequence[Sequence[str]] = (),
) -> tuple[list[tuple[Chunk, float, str]], int]:
    """Chunks for `query`, best first, as (chunk, score, match), plus the keyword match count.

    Keyword matches and the chunks closest in meaning (`query_vec`) are combined by reciprocal
    rank fusion, together with any `extra` rankings of chunk ids (e.g. from the graph). `match`
    says where a chunk came from: keyword, meaning, both, or graph. Without semantic or extra
    rankings this is plain keyword search, scored as before.
    """
    terms = list(dict.fromkeys(content_words(query)))
    phrase = " ".join(words(query))
    scored = []
    for c in chunks.search(book_id, terms, chapter=chapter) if terms else []:
        score = score_text(c.text, terms, phrase)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].seq))
    meaning = [ref for ref, _ in retrieval.semantic(book_id, "chunk", query_vec)]
    if not meaning and not any(extra):
        return [(c, score, "keyword") for score, c in scored], len(scored)

    by_id = {c.id: c for _, c in scored}
    wanted = [i for ranking in (meaning, *extra) for i in ranking if i not in by_id]
    by_id.update({c.id: c for c in chunks.get_chunks(list(dict.fromkeys(wanted))) if c.book_id == book_id})
    keep = {i for i, c in by_id.items() if chapter is None or c.chapter == chapter}
    keyword = [c.id for _, c in scored]
    rankings = [[i for i in r if i in keep] for r in (keyword, meaning, *extra)]
    by_keyword, by_meaning = set(keyword), set(meaning)

    def match(i: str) -> str:
        if i in by_keyword:
            return "both" if i in by_meaning else "keyword"
        return "meaning" if i in by_meaning else "graph"

    return [(by_id[i], score, match(i)) for i, score in retrieval.fuse(*rankings)], len(scored)


def with_rewrites(
    sl: Storyline | None,
    views: _ChapterViews,
    ranked: list[tuple[Chunk, float, str]],
    query: str,
    terms: list[str],
    chapter: str | None = None,
) -> tuple[list[tuple[Chunk, float, str]], set[str]]:
    """`ranked` as the storyline reads: a current rewritten chapter replaces the original text of
    that chapter, its matches ranked first. Also returns the rewritten passages' ids."""
    if not sl:
        return ranked, set()
    rewritten = {
        p.chapter: p
        for p in storylines.list_passages(sl.id)
        if p.fingerprint == views[p.chapter].fingerprint
    }
    phrase = " ".join(words(query))
    mine = [
        (p.as_chunk(), score_text(p.text, terms, phrase), "keyword")
        for t, p in rewritten.items()
        if chapter in (None, t) and terms
    ]
    ranked = [m for m in sorted(mine, key=lambda r: -r[1]) if m[1]] + [
        r for r in ranked if r[0].chapter not in rewritten
    ]
    return ranked, {p.id for p in rewritten.values()}


def search_chunks(
    book_id: str,
    query: str,
    chapter: str | None = None,
    limit: int = 5,
    session_id: str | None = None,
) -> dict[str, Any]:
    sl = session_storyline(book_id, session_id)
    query_vec = retrieval.embed_query(query)
    terms = list(dict.fromkeys(content_words(query)))
    if not terms and query_vec is None:
        raise ValueError("query has no searchable words")
    title = resolve_chapter(book_id, chapter)["title"] if chapter else None
    ranked, keyword_matches = rank_chunks(book_id, query, title, query_vec)
    views = _ChapterViews(sl)
    ranked, rewritten_ids = with_rewrites(sl, views, ranked, query, terms, title)
    top = ranked[: min(max(limit, 1), TOOL_MAX_RESULTS)]
    return {
        "results": [
            {**chunk_ref(c), "score": round(score, 4), "match": how, "snippet": snippet(c.text, terms),
             **superseded_note(sl, views, c, rewritten_ids)}
            for c, score, how in top
        ],
        "total_matches": keyword_matches,
    }


def event_out(event: GraphEvent, chunk_ids: dict[int, str]) -> dict[str, Any]:
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


def get_timeline(book_id: str, entity: str, session_id: str | None = None) -> dict[str, Any]:
    scope = branch_scope(book_id, session_id)
    found = resolve(scope, entity)
    rows = graph.entity_events(scope, found["key"])
    shown = rows[:TOOL_MAX_TIMELINE_EVENTS]
    ids = chunks.ids_by_seq(book_id, {int(e.story_pos // 1000) for e, _ in shown})
    return {
        "entity": {"key": found["key"], "name": found["name"]},
        "events": [{**event_out(e, ids), "role": role} for e, role in shown],
        "total": len(rows),
        "truncated": len(rows) > len(shown),
    }


def get_chapter_summary(
    book_id: str, chapter: str, session_id: str | None = None
) -> dict[str, Any]:
    """The chapter's span plus its events and main entities, for the agent to summarise."""
    scope = branch_scope(book_id, session_id)
    info = resolve_chapter(book_id, chapter)
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
            {**event_out(e, ids), "main_participants": [names[k] for k in keys if k in names]}
            for e, keys in shown
        ],
        "total_events": len(rows),
        "truncated": len(rows) > len(shown),
    }
