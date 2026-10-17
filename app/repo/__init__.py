"""Repository layer: the only way LangGraph nodes (and the API) reach MongoDB and FalkorDB.

Import from `app.repo`, never from `app.db`.
"""

from app.repo import books, chunks, graph, graph_levels, mentions, users
from app.repo.graph import GraphScope


def ensure_indexes() -> None:
    """Create Mongo indexes. Falkor indexes are per book: `graph.ensure_indexes(book_id)`."""
    for module in (users, books, chunks, graph_levels, mentions):
        module.ensure_indexes()


__all__ = ["GraphScope", "books", "chunks", "ensure_indexes", "graph", "graph_levels", "mentions", "users"]
