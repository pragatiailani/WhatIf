"""The MCP server. Tool descriptions are what the LLM reads, so they say when to use each tool.

Every tool takes `book_id` (graph and text tools) or `session_id` (session tools). The inference
graph supplies these from the user's session; the LLM must never choose them, so the client
hides them from the model and fills them in on each call.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.logging_config import fields
from app.mcp_server import graph_tools, rag_tools, session_tools, storyline_tools, text_tools
from app.storyline.schema import Fact

mcp = FastMCP("whatif")
log = logging.getLogger("app.mcp")


def _logged(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Log what the agent-side logger cannot see: the traceback of an unexpected failure.

    A ValueError is an expected refusal (unknown name, a rule such as "verify before commit"):
    a warning. Successful calls are logged by the agent, so here they are only debug lines.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except ValueError as exc:
            log.warning("tool=%s refused: %s | %s", fn.__name__, exc, fields(kwargs))
            raise
        except Exception:
            log.exception("tool=%s crashed | %s", fn.__name__, fields(kwargs))
            raise
        log.debug("tool=%s ok in %dms", fn.__name__, round((time.perf_counter() - started) * 1000))
        return result

    return wrapper


def tool() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """`@tool()` registers an MCP tool with error logging."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        return mcp.tool()(_logged(fn))

    return register


# --- retrieval -----------------------------------------------------------


@tool()
def retrieve(
    book_id: str, query: str, limit: int = 5, session_id: str | None = None
) -> dict[str, Any]:
    """Start here for most questions. Finds what the book says about `query` (usually the user's
    question) in one call: the entities it is about, matched by name and by meaning; how they are
    related in the graph and the communities they belong to; the closest events; and the best
    passages (with chunk ids, chapter and page) by keyword, meaning and graph mentions. Use the
    other tools to dig further."""
    return rag_tools.retrieve(book_id, query, limit, session_id)


# --- graph tools ---------------------------------------------------------


@tool()
def find_entities(
    book_id: str, text: str, limit: int = 10, session_id: str | None = None
) -> dict[str, Any]:
    """Find characters, places, objects etc. mentioned in `text`, by name and alias; when no
    name matches, the entities closest in meaning (e.g. "the old sailor"). Gives entity keys;
    other tools accept a key or a name."""
    return graph_tools.find_entities(book_id, text, limit, session_id)


@tool()
def get_neighbors(
    book_id: str, entity: str, hops: int = 1, limit: int = 25, session_id: str | None = None
) -> dict[str, Any]:
    """Entities connected to `entity` (key or name) within 1 or 2 hops, with how each is
    connected. Use for "who is X connected to" and for gathering context around an entity."""
    return graph_tools.get_neighbors(book_id, entity, hops, limit, session_id)


@tool()
def find_path(book_id: str, a: str, b: str, session_id: str | None = None) -> dict[str, Any]:
    """Shortest chain of relations linking entity `a` to entity `b` (keys or names). Use for
    "how is X related to Y"."""
    return graph_tools.find_path(book_id, a, b, session_id)


@tool()
def get_relations(
    book_id: str,
    entity: str,
    rel_type: str | None = None,
    limit: int = 25,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Relations of `entity`, optionally only one type such as BETRAYS, KILLS, LOCATED_IN,
    MARRIED_TO, MENTOR_OF. An unknown type returns the list of valid ones."""
    return graph_tools.get_relations(book_id, entity, rel_type, limit, session_id)


@tool()
def search_communities(book_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    """Summaries of the book's groups of connected characters, places and things most relevant
    to `query`, by keyword and meaning; level 1 is an overview of the whole book. Use for
    big-picture questions (themes, factions, overall plot) rather than specific facts."""
    return graph_tools.search_communities(book_id, query, limit)


# --- text tools ----------------------------------------------------------


@tool()
def get_chunks(
    book_id: str, chunk_ids: list[str], session_id: str | None = None
) -> dict[str, Any]:
    """Passages by chunk id (max 10), with chapter and page; in a what-if chat also its rewritten chapters. Use to quote the book
    and to cite; every answer should be backed by passages fetched here or by search_chunks."""
    return text_tools.get_chunks(book_id, chunk_ids, session_id)


@tool()
def search_chunks(
    book_id: str,
    query: str,
    chapter: str | None = None,
    limit: int = 5,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Search the book text by keyword and by meaning, optionally within one chapter. Returns
    excerpts with chunk ids; `match` says how each was found. Use when the graph has no answer;
    call get_chunks for the full passage."""
    return text_tools.search_chunks(book_id, query, chapter, limit, session_id)


@tool()
def get_chapter_summary(
    book_id: str, chapter: str, session_id: str | None = None
) -> dict[str, Any]:
    """What happens in a chapter (title, or its number): its events in order, main entities
    and chunk ids. Summarise from this; fetch chunks for exact quotes."""
    return text_tools.get_chapter_summary(book_id, chapter, session_id)


@tool()
def get_timeline(book_id: str, entity: str, session_id: str | None = None) -> dict[str, Any]:
    """Events involving `entity` in the order the story tells them, with page and chunk id.
    Use for "what happens to X" and "when did X ...". """
    return text_tools.get_timeline(book_id, entity, session_id)


# --- session tools -------------------------------------------------------


@tool()
def get_history(session_id: str, n: int = 10) -> dict[str, Any]:
    """The last `n` messages of this chat, oldest first."""
    return session_tools.get_history(session_id, n)


@tool()
def get_session_summary(session_id: str) -> dict[str, Any]:
    """Rolling summary of the older part of this chat."""
    return session_tools.get_session_summary(session_id)


@tool()
def save_note(session_id: str, text: str, message_id: str | None = None) -> dict[str, Any]:
    """Bookmark `text` (an answer or passage) for the user, optionally linked to a message."""
    return session_tools.save_note(session_id, text, message_id)


# --- storyline tools -----------------------------------------------------


@tool()
def get_dependents(
    book_id: str,
    entity: str,
    from_event: str | None = None,
    from_pos: float | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Everything after a point in the story that involves `entity`: later events (with their
    chapters) and relations. Use it to see what a change to this character would ripple into.
    The point is `from_event` (an event key) or `from_pos` (a story_pos)."""
    return storyline_tools.get_dependents(book_id, entity, from_event, from_pos, session_id)


@tool()
def get_state_at(
    book_id: str,
    pos: float,
    entities: list[str] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Where each entity stands at story position `pos`: facts set in this storyline, recent
    events, relations, and hints such as a KILLS relation. Defaults to the entities the open
    draft affects."""
    return storyline_tools.get_state_at(book_id, pos, entities, session_id)


@tool()
def start_draft(
    book_id: str,
    session_id: str,
    anchor_event: str,
    premise: str,
    facts: list[Fact] | None = None,
) -> dict[str, Any]:
    """Begin an alternate version of the story at `anchor_event` (an event key). `premise` says
    what changes; `facts` state what is now true of entities from that point (e.g. entity "A",
    attribute "status", value "alive"). Queues every later event and relation involving the
    affected entities for you to decide on."""
    return storyline_tools.start_draft(book_id, session_id, anchor_event, premise, facts)


@tool()
def get_open_items(book_id: str, session_id: str, limit: int = 5) -> dict[str, Any]:
    """The next unresolved events/relations of the draft, in story order, with why each was
    queued. Read the passage with get_chunks (chunk_id) before deciding."""
    return storyline_tools.get_open_items(book_id, session_id, limit)


@tool()
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
    """Decide one item: action "keep" (still true in this version), "hide" (no longer happens),
    or "rewrite" (replace the event with `new_summary`, one sentence; `participants` replaces its
    cast). `affects` lists entities whose situation this change alters, which queues their later
    events too. `facts` record what is now true from this point. `ref` may also be any event key
    that verify_draft flagged."""
    return storyline_tools.resolve_item(
        book_id, session_id, ref, action, new_summary, participants, affects, facts
    )


@tool()
def verify_draft(book_id: str, session_id: str) -> dict[str, Any]:
    """Check the draft for continuity: nothing left open, nobody acts after dying, and an
    LLM check of every chapter the draft touches. Returns the contradictions to fix with
    resolve_item. Call it after every round of changes; commit_draft needs a pass."""
    return storyline_tools.verify_draft(book_id, session_id)


@tool()
def commit_draft(book_id: str, session_id: str) -> dict[str, Any]:
    """Make the draft part of the storyline. Refused unless the latest verify_draft passed and
    nothing changed since. Only call this after the user has agreed to the changes."""
    return storyline_tools.commit_draft(book_id, session_id)


@tool()
def discard_draft(book_id: str, session_id: str) -> dict[str, Any]:
    """Throw away the open draft and every change in it."""
    return storyline_tools.discard_draft(book_id, session_id)


@tool()
def read_chapter(book_id: str, chapter: str, session_id: str | None = None) -> dict[str, Any]:
    """A chapter as this chat's story tells it. In a what-if storyline a changed chapter comes back
    as rewritten text (use it instead of the original, and cite its chunk_id); an unchanged chapter
    comes back as the original's chunk ids, to fetch with get_chunks."""
    return storyline_tools.read_chapter(book_id, chapter, session_id)


@tool()
def generate_passage(book_id: str, session_id: str, chapter: str) -> dict[str, Any]:
    """Write (or rewrite) the text of a chapter that the storyline changed, from its current events
    and facts, and check it for contradictions. Returns a preview and any problems it still has.
    Use it to show the user what a changed chapter now reads like."""
    return storyline_tools.generate_passage(book_id, session_id, chapter)
