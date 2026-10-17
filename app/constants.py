"""Fixed names and defaults. Environment-specific settings live in `app.config`."""

# --- Mongo collections ---------------------------------------------------

BOOKS_COLLECTION = "books"
CHUNKS_COLLECTION = "chunks"
MENTIONS_COLLECTION = "mentions"
GRAPH_LEVELS_COLLECTION = "graph_levels"

# --- FalkorDB ------------------------------------------------------------

# One graph per book: f"{GRAPH_NAME_PREFIX}{book_id}".
GRAPH_NAME_PREFIX = "book_"

# Node labels that get a `key` index when a book graph is created.
KEY_INDEXED_LABELS = ("Entity", "Event")

# Properties stripped from read results (large, only used by vector search).
OMITTED_GRAPH_PROPS = frozenset({"embedding"})

# Default cap on edges returned by `graph.entity_relations`.
DEFAULT_RELATION_LIMIT = 50

# Substring of the FalkorDB error raised when an index already exists.
FALKOR_INDEX_EXISTS_ERROR = "already indexed"
