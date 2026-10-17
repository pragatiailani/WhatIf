"""Inkitt through its MCP server: connect with a user's access token and send a story there.

Each user has their own Inkitt token (stored encrypted, see `repo.users.set_inkitt_pat`). The
chat agent gets a whitelist of Inkitt's tools plus `publish_to_inkitt`, which sends a book the user
uploaded or a storyline they made. Chapters are copied straight from MongoDB, so the text never
passes through the model.
"""

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.constants import INKITT_CHAPTER_NAME_CHARS, INKITT_MCP_URL, INKITT_SUMMARY_CHARS
from app.repo import books, chunks, inkitt_stories, storylines
from app.repo.inkitt_stories import SourceKind


class InkittError(Exception):
    """Inkitt refused a call; the message is Inkitt's own, for the model or the user."""


@asynccontextmanager
async def open_session(pat: str) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {pat}"}
    async with streamablehttp_client(INKITT_MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def verify(pat: str) -> bool:
    """Whether Inkitt accepts `pat`. Any failure to connect or list stories counts as no."""
    try:
        async with open_session(pat) as session:
            await call(session, "list_stories", {})
        return True
    except Exception:  # a rejected token surfaces as an HTTP error inside the transport
        return False


async def call(session: ClientSession, tool: str, args: dict[str, Any]) -> Any:
    """Call an Inkitt tool and return its result, parsed from JSON when it is JSON."""
    result = await session.call_tool(tool, args)
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    if result.isError:
        raise InkittError(f"{tool}: {text or 'failed'}")
    if result.structuredContent:
        return result.structuredContent
    try:
        return json.loads(text)
    except ValueError:
        return text


def _id_of(data: Any, kind: str) -> int | None:
    """The id in a create/list result: `{kind: {id}}`, `{kind}_id` or `id`."""
    if not isinstance(data, dict):
        return None
    nested = data.get(kind)
    for value in (
        nested.get("id") if isinstance(nested, dict) else None,
        data.get(f"{kind}_id"),
        data.get("id"),
    ):
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            return int(value)
    return None


def _chapter_list(data: Any) -> list[dict]:
    items = data if isinstance(data, list) else (data or {}).get("chapters", [])
    return [c for c in items if isinstance(c, dict)]


def book_chapters(book_id: str) -> list[tuple[str, str]]:
    """(title, markdown text) per chapter, in book order.

    Chunks are whole pages labelled with the chapter their page starts in, so the text is split at
    the chapter heading lines instead. Text before the first heading (title page, contents) is
    left out; a book without detected chapters becomes one chapter named after the book.
    """
    pages = chunks.list_chunks(book_id)
    text = "\n".join(c.text for c in pages)
    titles = [c["title"] for c in chunks.list_chapters(book_id)]
    if not titles:
        book = books.get_book(book_id)
        return [(book.title if book else "Chapter 1", text.strip())] if text.strip() else []

    starts: list[tuple[str, int, int]] = []  # (title, heading start, body start)
    pos = 0
    for title in titles:
        heading = re.compile(rf"^[ \t]*{re.escape(title.strip())}[ \t]*$", re.MULTILINE)
        match = heading.search(text, pos)
        if match is None:  # the heading was cleaned away; fall back to its first page
            seq = next(c.seq for c in pages if c.chapter == title)
            start = sum(len(c.text) + 1 for c in pages if c.seq < seq)
            starts.append((title, start, start))
            pos = start
            continue
        starts.append((title, match.start(), match.end()))
        pos = match.end()

    out = []
    for i, (title, _, body_start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        body = text[body_start:end].strip()
        if body:
            out.append((title, body))
    return out


@dataclass(frozen=True)
class Story:
    """What gets sent to Inkitt: a book or a storyline, with its chapters in reading order."""

    kind: SourceKind
    source_id: str
    owner_id: str | None
    title: str
    chapters: list[tuple[str, str]]  # (title, markdown text)


def book_story(book_id: str) -> Story:
    book = books.get_book(book_id)
    if book is None:
        raise ValueError(f"unknown book '{book_id}'")
    return Story("book", book.id, book.owner_id, book.title, book_chapters(book_id))


def storyline_story(storyline_id: str) -> Story:
    """The storyline as a full book: the base book's chapters before the storyline diverges, then
    the storyline's own chapters. Raises `StorylinesUnavailable` until storylines are stored."""
    line = storylines.get_storyline_text(storyline_id)
    if line is None:
        raise ValueError(f"unknown storyline '{storyline_id}'")
    base = book_chapters(line.book_id)
    titles = [t for t, _ in base]
    cut = titles.index(line.diverges_at) if line.diverges_at in titles else len(base)
    return Story("storyline", line.id, line.owner_id, line.title, [*base[:cut], *line.chapters])


async def publish_story(
    session: ClientSession,
    story: Story,
    *,
    summary: str = "",
    story_type: str = "original",
    age_rating: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Create an Inkitt story with one chapter per chapter of `story`, as drafts unless `publish`.
    Each book or storyline is sent once: later calls report the existing Inkitt story instead."""
    existing_id = inkitt_stories.get_story_id(story.kind, story.source_id)
    if existing_id is not None:
        return {
            "status": "already_on_inkitt",
            "story_id": existing_id,
            "note": f"This {story.kind} was already sent to Inkitt. Use list_chapters / "
            "publish_all_chapters on this story_id instead of sending it again.",
        }
    if not story.chapters:
        raise ValueError(f"this {story.kind} has no text to publish yet")

    args: dict[str, Any] = {"title": story.title, "story_type": story_type, "story_status": "complete"}
    if summary.strip():
        args["summary"] = summary.strip()[:INKITT_SUMMARY_CHARS]
    if age_rating:
        args["age_rating"] = age_rating
    created = await call(session, "create_story", args)
    story_id = _id_of(created, "story")
    if story_id is None:
        raise InkittError(f"create_story returned no story id: {str(created)[:300]}")
    inkitt_stories.save(story.kind, story.source_id, story.owner_id, story_id)

    # Inkitt creates an empty first chapter with the story; fill it rather than leave it blank.
    existing = _chapter_list(await call(session, "list_chapters", {"story_id": story_id}))
    first_id = _id_of(existing[0], "chapter") if existing else None
    chapter_ids: list[int] = []
    for i, (title, body) in enumerate(story.chapters):
        fields = {"story_id": story_id, "name": title[:INKITT_CHAPTER_NAME_CHARS], "text": body}
        if i == 0 and first_id is not None:
            await call(session, "update_chapter", {**fields, "chapter_id": first_id})
            chapter_ids.append(first_id)
            continue
        new_id = _id_of(await call(session, "create_chapter", fields), "chapter")
        if new_id is not None:
            chapter_ids.append(new_id)

    out: dict[str, Any] = {
        "status": "drafts_created",
        "story_id": story_id,
        "chapters": len(story.chapters),
        "chapter_titles": [t for t, _ in story.chapters],
        "chapter_ids": chapter_ids,
    }
    if publish:
        try:
            await call(session, "publish_all_chapters", {"story_id": story_id})
            out["status"] = "published"
        except InkittError as exc:  # usually missing cover, genre or summary
            out["publish_error"] = str(exc)
    return out
