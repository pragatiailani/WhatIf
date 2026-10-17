from typing import Any

from app.constants import TOOL_DESCRIPTION_CHARS
from app.mcp_server.matching import resolve_entity
from app.repo import GraphScope, books, graph, sessions, storylines
from app.repo.models import GraphEntity, GraphRelation, Storyline


def session_storyline(book_id: str, session_id: str | None) -> Storyline | None:
    """The storyline the session is working in, or None for canon. Raises on an unknown book or
    a session of another book."""
    if books.get_book(book_id) is None:
        raise ValueError(f"unknown book '{book_id}'")
    if session_id is None:
        return None
    session = sessions.get_session(session_id)
    if session is None or session.book_id != book_id:
        raise ValueError(f"unknown session '{session_id}' for this book")
    if session.storyline_id is None:
        return None
    sl = storylines.get_storyline(session.storyline_id)
    if sl is None or sl.book_id != book_id:
        raise ValueError(f"unknown storyline '{session.storyline_id}'")
    return sl


def branch_scope(book_id: str, session_id: str | None = None) -> GraphScope:
    """Scope for a tool call: the session's storyline (its draft included) or canon."""
    sl = session_storyline(book_id, session_id)
    return storylines.scope(sl) if sl else GraphScope.canon(book_id)


def short(text: str, limit: int = TOOL_DESCRIPTION_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def entity_out(e: GraphEntity) -> dict[str, Any]:
    return {
        "key": e.key,
        "name": e.name,
        "type": e.type,
        "aliases": e.aliases,
        "description": short(e.description),
        "first_page": e.first_page,
        "last_page": e.last_page,
        "mention_count": e.mention_count,
    }


def relation_out(rel: GraphRelation, origin: str) -> dict[str, Any]:
    """An edge seen from `origin`: 'out' if origin is the source, else 'in'."""
    return {
        "rel": rel.rel,
        "direction": "out" if rel.src == origin else "in",
        "description": short(rel.description),
        "weight": rel.weight,
        "first_pos": rel.story_pos,
    }


def resolve(scope: GraphScope, ref: str) -> dict[str, Any]:
    """The entity (index row) a key, name or alias refers to."""
    return resolve_entity(ref, graph.entity_index(scope))
