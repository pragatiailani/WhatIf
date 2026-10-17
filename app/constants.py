"""Fixed names and defaults. Environment-specific settings live in `app.config`."""

# --- Mongo collections ---------------------------------------------------

BOOKS_COLLECTION = "books"
CHUNKS_COLLECTION = "chunks"
MENTIONS_COLLECTION = "mentions"
GRAPH_LEVELS_COLLECTION = "graph_levels"

# --- LLM -----------------------------------------------------------------

DEFAULT_AZURE_LLM_API_VERSION = "2025-01-01-preview"
DEFAULT_AZURE_LLM_MODEL = "gpt-4.1-mini"

# --- Extraction ----------------------------------------------------------

# Entity types the LLM extracts; each becomes the second Falkor label (`:Entity:Character`).
ENTITY_TYPES = ("Character", "Place", "Organization", "Group", "Object", "Concept")
# Every node type in the book graph. Events come from the event list, chapters from parsing,
# communities from community detection; none of them is extracted as an entity.
NODE_TYPES = (*ENTITY_TYPES, "Event", "Chapter", "Community")

# Story relations between two entities: the vocabulary the LLM extracts from. Anything else the
# LLM invents maps to FALLBACK_REL.
REL_VOCABULARY = frozenset(
    {
        "FRIEND_OF", "ENEMY_OF", "RIVAL_OF", "LOVES", "MARRIED_TO", "PARENT_OF", "CHILD_OF",
        "SIBLING_OF", "RELATIVE_OF", "MENTOR_OF", "WORKS_FOR", "LEADS", "MEMBER_OF", "ALLY_OF",
        "BETRAYS", "HELPS", "HARMS", "KILLS", "SAVES", "MEETS", "TALKS_TO", "TRUSTS", "DISTRUSTS",
        "FEARS", "OWNS", "USES", "CREATES", "SEEKS", "LIVES_IN", "TRAVELS_TO", "LOCATED_IN",
        "PART_OF", "BELIEVES_IN", "SYMBOLIZES",
    }
)  # fmt: skip
FALLBACK_REL = "OTHER"
# Edges built in code, never by the LLM: event -> participant / place / later or caused event,
# node -> chapter / community.
STRUCTURAL_RELS = frozenset({"INVOLVES", "AT", "BEFORE", "CAUSES", "IN_CHAPTER", "IN_COMMUNITY"})

# Pages sent to the LLM at the same time during per-page graph generation.
DEFAULT_EXTRACTION_BATCH_SIZE = 10

# Minimum fraction of an evidence quote that must be found in the chunk to accept the match.
MIN_QUOTE_MATCH = 0.8

# --- Merge ---------------------------------------------------------------

# LLM and embedding calls in flight at once while merging page graphs.
DEFAULT_MERGE_CONCURRENCY = 8

# Candidate pairs (entities + events) per judge call; more are split over several calls.
MERGE_MAX_CANDIDATES_PER_CALL = 40
# Most left-side candidates kept for one right-side entity.
MERGE_MAX_CANDIDATES_PER_ENTITY = 3
# A "same" verdict from the judge is applied only at or above this confidence.
MERGE_MIN_SAME_CONFIDENCE = 0.7

# Name similarity (0-1) at or above which two same-type entities go to the judge.
MERGE_NAME_SIM_CANDIDATE = 0.75
# Description embedding similarity at or above which two same-type entities go to the judge.
MERGE_EMBED_SIM_CANDIDATE = 0.85
# Identical names are merged in code unless their embedding similarity is below this.
MERGE_MIN_EMBED_FOR_AUTO = 0.5

# Events are compared only if at most this many chunks apart (same moment across a page break).
MERGE_EVENT_MAX_SEQ_GAP = 2
# Summary similarity at or above which two events sharing a participant go to the judge.
MERGE_EVENT_SUMMARY_SIM = 0.6

# Distinct per-page descriptions kept on a merged entity.
MERGE_MAX_DESCRIPTIONS = 5
# Entities per final-description LLM call.
FINAL_DESCRIPTION_BATCH = 40

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
# Substring of the FalkorDB error raised when deleting a graph that does not exist.
FALKOR_MISSING_GRAPH_ERROR = "empty key"
