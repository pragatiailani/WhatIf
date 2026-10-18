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
    inkitt_stories,
    mentions,
    refresh_tokens,
    sessions,
    storylines,
    users,
    vectors,
)
from app.repo.graph import GraphScope


def ensure_indexes() -> None:
    """Create Mongo indexes. Falkor indexes are per book: `graph.ensure_indexes(book_id)`."""
    modules = (books, chunks, communities, users, graph_levels, inkitt_stories, mentions, refresh_tokens,
               sessions, storylines, vectors)  # fmt: skip
    for module in modules:
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
    "inkitt_stories",
    "mentions",
    "refresh_tokens",
    "sessions",
    "storylines",
    "users",
    "vectors",
]
