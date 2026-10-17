"""The MCP server. Tool descriptions are what the LLM reads, so they say when to use each tool.

Every tool takes `book_id` (graph and text tools) or `session_id` (session tools). The inference
graph supplies these from the user's session; the LLM must never choose them, so the client
hides them from the model and fills them in on each call.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.mcp_server import graph_tools, session_tools, text_tools

mcp = FastMCP("whatif")


# --- graph tools ---------------------------------------------------------


@mcp.tool()
def find_entities(book_id: str, text: str, limit: int = 10) -> dict[str, Any]:
    """Find characters, places, objects etc. mentioned in `text` (usually the user's question),
    by name and alias. Start here to get entity keys; other tools accept a key or a name."""
    return graph_tools.find_entities(book_id, text, limit)


@mcp.tool()
def get_neighbors(book_id: str, entity: str, hops: int = 1, limit: int = 25) -> dict[str, Any]:
    """Entities connected to `entity` (key or name) within 1 or 2 hops, with how each is
    connected. Use for "who is X connected to" and for gathering context around an entity."""
    return graph_tools.get_neighbors(book_id, entity, hops, limit)


@mcp.tool()
def find_path(book_id: str, a: str, b: str) -> dict[str, Any]:
    """Shortest chain of relations linking entity `a` to entity `b` (keys or names). Use for
    "how is X related to Y"."""
    return graph_tools.find_path(book_id, a, b)


@mcp.tool()
def get_relations(
    book_id: str, entity: str, rel_type: str | None = None, limit: int = 25
) -> dict[str, Any]:
    """Relations of `entity`, optionally only one type such as BETRAYS, KILLS, LOCATED_IN,
    MARRIED_TO, MENTOR_OF. An unknown type returns the list of valid ones."""
    return graph_tools.get_relations(book_id, entity, rel_type, limit)


@mcp.tool()
def search_communities(book_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    """Topic clusters of the book most relevant to `query`, with their summaries. Use for
    big-picture questions (themes, factions, overall plot) rather than specific facts."""
    return graph_tools.search_communities(book_id, query, limit)


# --- text tools ----------------------------------------------------------


@mcp.tool()
def get_chunks(book_id: str, chunk_ids: list[str]) -> dict[str, Any]:
    """Original passages by chunk id (max 10), with chapter and page. Use to quote the book
    and to cite; every answer should be backed by passages fetched here or by search_chunks."""
    return text_tools.get_chunks(book_id, chunk_ids)


@mcp.tool()
def get_evidence(book_id: str, entity: str, limit: int = 10) -> dict[str, Any]:
    """Source quotes for an entity name/key or an event key, with chunk and page citations."""
    return text_tools.get_evidence(book_id, entity, limit)


@mcp.tool()
def search_chunks(
    book_id: str, query: str, chapter: str | None = None, limit: int = 5
) -> dict[str, Any]:
    """Keyword search over the book text, optionally within one chapter. Returns excerpts with
    chunk ids. Use when the graph has no answer; call get_chunks for the full passage."""
    return text_tools.search_chunks(book_id, query, chapter, limit)


@mcp.tool()
def get_chapter_summary(book_id: str, chapter: str) -> dict[str, Any]:
    """What happens in a chapter (title, or its number): its events in order, main entities
    and chunk ids. Summarise from this; fetch chunks for exact quotes."""
    return text_tools.get_chapter_summary(book_id, chapter)


@mcp.tool()
def get_timeline(book_id: str, entity: str) -> dict[str, Any]:
    """Events involving `entity` in the order the story tells them, with page and chunk id.
    Use for "what happens to X" and "when did X ...". """
    return text_tools.get_timeline(book_id, entity)


# --- session tools -------------------------------------------------------


@mcp.tool()
def get_history(session_id: str, n: int = 10) -> dict[str, Any]:
    """The last `n` messages of this chat, oldest first."""
    return session_tools.get_history(session_id, n)


@mcp.tool()
def get_session_summary(session_id: str) -> dict[str, Any]:
    """Rolling summary of the older part of this chat."""
    return session_tools.get_session_summary(session_id)


@mcp.tool()
def save_note(session_id: str, text: str, message_id: str | None = None) -> dict[str, Any]:
    """Bookmark `text` (an answer or passage) for the user, optionally linked to a message."""
    return session_tools.save_note(session_id, text, message_id)
