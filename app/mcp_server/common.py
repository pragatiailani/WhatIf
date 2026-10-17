from typing import Any

from app.constants import TOOL_DESCRIPTION_CHARS
from app.mcp_server.matching import resolve_entity
from app.repo import GraphScope, books, graph
from app.repo.models import GraphEntity, GraphRelation


def canon_scope(book_id: str) -> GraphScope:
    """Scope for a tool call. Raises if the book does not exist."""
    if books.get_book(book_id) is None:
        raise ValueError(f"unknown book '{book_id}'")
    return GraphScope.canon(book_id)


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
        "first_page": rel.first_page,
        "pages": rel.pages,
        "evidence_chunk_ids": rel.chunk_ids,
    }


def resolve(scope: GraphScope, ref: str) -> dict[str, Any]:
    """The entity (index row) a key, name or alias refers to."""
    return resolve_entity(ref, graph.entity_index(scope))
