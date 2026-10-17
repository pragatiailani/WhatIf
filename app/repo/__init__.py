"""Repository layer: the only way LangGraph nodes (and the API) reach MongoDB and FalkorDB.

Import from `app.repo`, never from `app.db`.
"""

from app.repo import (
    books,
    checkpoints,
    chunks,
    communities,
    graph,
    graph_levels,
    mentions,
    sessions,
    storylines,
    users
)
from app.repo.graph import GraphScope


def ensure_indexes() -> None:
    """Create Mongo indexes. Falkor indexes are per book: `graph.ensure_indexes(book_id)`."""
    for module in (books, chunks, communities, users, graph_levels, mentions, sessions, storylines):
        module.ensure_indexes()


__all__ = [
    "GraphScope",
    "books",
    "checkpoints",
    "chunks",
    "communities",
    "ensure_indexes",
    "graph",
    "graph_levels",
    "mentions",
    "sessions",
    "storylines",
    "users"
]
