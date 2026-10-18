"""Inkitt through its MCP server: connect with a user's access token and send a story there.

Each user has their own Inkitt token (stored encrypted, see `repo.users.set_inkitt_pat`). The
chat agent gets a whitelist of Inkitt's tools plus `publish_to_inkitt`, which sends a book the user
uploaded or a storyline they made. Chapters are copied straight from MongoDB, so the text never
passes through the model.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.constants import (
    INKITT_CHAPTER_CHARS,
    INKITT_CHAPTER_NAME_CHARS,
    INKITT_COVER_URL,
    INKITT_MCP_URL,
    INKITT_SUMMARY_CHARS,
)
from app.repo import books, chunks, inkitt_stories, storylines
from app.repo.inkitt_stories import SourceKind

log = logging.getLogger(__name__)


class InkittError(Exception):
    """Inkitt refused a call; the message is Inkitt's own, for the model or the user."""


def root_cause(exc: BaseException) -> BaseException:
    """The first real error inside the exception groups an MCP session wraps failures in."""
    while inner := getattr(exc, "exceptions", None):  # an exception group (also on 3.10's backport)
        exc = inner[0]
    return exc


def failure_text(exc: BaseException) -> str:
    """What to tell the model when sending to Inkitt failed."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 413:
            return "Inkitt refused the upload as too large (HTTP 413)."
        return f"Inkitt refused the request (HTTP {status} {exc.response.reason_phrase})."
    if isinstance(exc, httpx.HTTPError):
        return "Inkitt could not be reached. Try again in a moment."
    return str(exc) or type(exc).__name__


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


def genre_pair(settings: Any, genre: str, sub_genre: str = "") -> dict[str, Any] | None:
    """The `story_genres` entry for `genre`/`sub_genre` (shortnames) from a `get_settings` result.
    An unknown genre becomes "other"; an unknown sub-genre becomes the genre's "general" one, or
    its first. None when the settings list no genres."""
    data = settings.get("settings", settings) if isinstance(settings, dict) else {}
    genres = data.get("genres") or {}
    mains = {m["shortname"]: m for m in genres.get("main_genres", [])}
    main = mains.get(genre) or mains.get("other")
    subs = (genres.get("sub_genres") or {}).get(str(main["id"]), []) if main else []
    if not subs:
        return None
    sub = next((s for s in subs if s["shortname"] == sub_genre), None) or next(
        (s for s in subs if s["shortname"] == "general"), subs[0]
    )
    return {
        "main_genre_id": main["id"],
        "sub_genre_id": sub["id"],
        "main_genre_shortname": main["shortname"],
        "sub_genre_shortname": sub["shortname"],
    }


async def set_metadata(
    session: ClientSession,
    story: Story,
    story_id: int,
    *,
    summary: str,
    genre: str,
    sub_genre: str,
    cover_url: str = INKITT_COVER_URL,
) -> list[str]:
    """Give the Inkitt story what publishing needs: summary, genre and the What If cover (none
    when `cover_url` is empty). Returns Inkitt's refusals, if any; the drafts stay either way."""
    errors: list[str] = []
    fields: dict[str, Any] = {"story_id": story_id, "ai_assisted": story.kind == "storyline"}
    if summary.strip():
        fields["summary"] = summary.strip()[:INKITT_SUMMARY_CHARS]
    try:
        if pair := genre_pair(await call(session, "get_settings", {}), genre, sub_genre):
            fields["story_genres"] = [pair]
        await call(session, "update_story", fields)
    except InkittError as exc:
        errors.append(str(exc))
    if not cover_url:
        return errors
    try:
        await call(session, "update_cover", {"story_id": story_id, "cover_url": cover_url})
    except InkittError as exc:
        errors.append(f"{exc} (cover image URL: {cover_url})")
    return errors


def _pieces(text: str, limit: int, seps: tuple[str, ...] = ("\n\n", "\n", " ")) -> list[str]:
    """`text` cut into pieces of at most `limit` characters, at paragraph breaks if it can, else
    at line breaks, then spaces, then anywhere."""
    if len(text) <= limit:
        return [text]
    if not seps:
        return [text[i : i + limit] for i in range(0, len(text), limit)]
    sep, finer = seps[0], seps[1:]
    out: list[str] = []
    current = ""
    for block in text.split(sep):
        for piece in _pieces(block, limit, finer):  # a block too long on its own is cut finer
            if current and len(current) + len(sep) + len(piece) > limit:
                out.append(current)
                current = piece
            else:
                current = f"{current}{sep}{piece}" if current else piece
    if current:
        out.append(current)
    return out


def split_long(chapters: list[tuple[str, str]], limit: int = INKITT_CHAPTER_CHARS) -> list[tuple[str, str]]:
    """Chapters longer than Inkitt accepts in one request become "Title (part n)" chapters. A book
    whose chapters were not detected is one long chapter, so this is what splits it up."""
    out: list[tuple[str, str]] = []
    for title, body in chapters:
        parts = [p.strip() for p in _pieces(body, limit) if p.strip()]
        if len(parts) <= 1:
            out.append((title, body))
            continue
        base = title[: INKITT_CHAPTER_NAME_CHARS - 12]
        out.extend((f"{base} (part {i})", part) for i, part in enumerate(parts, 1))
    return out


async def _upload_chapters(
    session: ClientSession, story: Story, story_id: int, chapters: list[tuple[str, str]], start: int
) -> list[int]:
    """Send `chapters[start:]`, recording progress after each so a broken upload can carry on."""
    first_id = None
    if start == 0:  # Inkitt creates an empty first chapter with the story; fill it, not leave it blank
        existing = _chapter_list(await call(session, "list_chapters", {"story_id": story_id}))
        first_id = _id_of(existing[0], "chapter") if existing else None
    chapter_ids: list[int] = []
    for i in range(start, len(chapters)):
        title, body = chapters[i]
        fields = {"story_id": story_id, "name": title[:INKITT_CHAPTER_NAME_CHARS], "text": body}
        if i == 0 and first_id is not None:
            await call(session, "update_chapter", {**fields, "chapter_id": first_id})
            chapter_ids.append(first_id)
        elif (new_id := _id_of(await call(session, "create_chapter", fields), "chapter")) is not None:
            chapter_ids.append(new_id)
        inkitt_stories.set_progress(story.kind, story.source_id, i + 1)
    inkitt_stories.set_progress(story.kind, story.source_id, len(chapters), uploaded=True)
    return chapter_ids


async def _publish_all(session: ClientSession, story_id: int, out: dict[str, Any]) -> None:
    try:
        await call(session, "publish_all_chapters", {"story_id": story_id})
        out["status"] = "published"
    except InkittError as exc:
        out["publish_error"] = str(exc)


async def publish_story(
    session: ClientSession,
    story: Story,
    *,
    summary: str = "",
    genre: str = "other",
    sub_genre: str = "",
    story_type: str = "original",
    age_rating: str | None = None,
    publish: bool = False,
    cover_url: str = INKITT_COVER_URL,
) -> dict[str, Any]:
    """Create an Inkitt story with one chapter per chapter of `story` (long ones in parts), as
    drafts unless `publish`, with the summary, genre and What If cover publishing needs. Each book
    or storyline is sent once: if an earlier upload broke off, this carries it on from the first
    chapter that did not arrive; if it finished, this refreshes the story's metadata (and
    publishes it if asked) instead."""
    meta = {"summary": summary, "genre": genre, "sub_genre": sub_genre, "cover_url": cover_url}
    chapters = split_long(story.chapters)
    record = inkitt_stories.get(story.kind, story.source_id)
    if record is not None and record["uploaded"]:
        out: dict[str, Any] = {
            "status": "already_on_inkitt",
            "story_id": record["story_id"],
            "note": f"This {story.kind} was already sent to Inkitt; its summary, genre and cover "
            "were updated. Use list_chapters / publish_all_chapters on this story_id for more.",
        }
        if errors := await set_metadata(session, story, record["story_id"], **meta):
            out["setup_errors"] = errors
        if publish:
            await _publish_all(session, record["story_id"], out)
        return out
    if not chapters:
        raise ValueError(f"this {story.kind} has no text to publish yet")

    if record is None:
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
        start = 0
    else:
        story_id, start = record["story_id"], min(record["chapters_sent"], len(chapters))
        log.info("resuming Inkitt upload story=%s %s=%s from chapter %d/%d",
                 story_id, story.kind, story.source_id, start, len(chapters))

    chapter_ids = await _upload_chapters(session, story, story_id, chapters, start)
    out = {
        "status": "drafts_created",
        "story_id": story_id,
        "chapters": len(chapters),
        "chapter_titles": [t for t, _ in chapters],
        "chapter_ids": chapter_ids,
    }
    if start:
        out["resumed_from_chapter"] = start + 1
    if errors := await set_metadata(session, story, story_id, **meta):
        out["setup_errors"] = errors
    if publish:
        await _publish_all(session, story_id, out)
    return out
