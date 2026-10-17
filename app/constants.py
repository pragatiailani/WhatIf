"""Fixed names and defaults. Environment-specific settings live in `app.config`."""

# --- Mongo collections ---------------------------------------------------

BOOKS_COLLECTION = "books"
CHUNKS_COLLECTION = "chunks"
MENTIONS_COLLECTION = "mentions"
GRAPH_LEVELS_COLLECTION = "graph_levels"

# --- Extraction ----------------------------------------------------------

# Entity types; each becomes the second Falkor label (`:Entity:Character`).
ENTITY_TYPES = ("Character", "Place", "Organization", "Object", "Concept", "Group")

# Controlled relation vocabulary. Anything the LLM invents maps to FALLBACK_REL.
REL_VOCABULARY = frozenset(
    {
        "FRIEND_OF", "ENEMY_OF", "FAMILY_OF", "PARENT_OF", "CHILD_OF", "SIBLING_OF", "SPOUSE_OF",
        "LOVES", "HATES", "KILLS", "SAVES", "BETRAYS", "WORKS_FOR", "LEADS", "MEMBER_OF", "OWNS",
        "LOCATED_IN", "LIVES_IN", "TRAVELS_TO", "KNOWS", "MENTORS",
    }
)  # fmt: skip
FALLBACK_REL = "RELATED_TO"

# Pages sent to the LLM at the same time during per-page graph generation.
DEFAULT_EXTRACTION_BATCH_SIZE = 10

# Minimum fraction of an evidence quote that must be found in the chunk to accept the match.
MIN_QUOTE_MATCH = 0.8

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
