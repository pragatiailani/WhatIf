"""Fixed names and defaults. Environment-specific settings live in `app.config`."""

# --- Mongo collections ---------------------------------------------------

USERS_COLLECTION = "users"
BOOKS_COLLECTION = "books"
CHUNKS_COLLECTION = "chunks"
MENTIONS_COLLECTION = "mentions"
GRAPH_LEVELS_COLLECTION = "graph_levels"
CHUNK_VECTORS_COLLECTION = "chunk_vectors"

# --- Auth ----------------------------------------------------------------

# How long a sign-in token stays valid.
AUTH_TOKEN_TTL_SECONDS = 7 * 24 * 3600
MIN_PASSWORD_LENGTH = 8

# --- LLM -----------------------------------------------------------------

DEFAULT_AZURE_LLM_API_VERSION = "2025-01-01-preview"
DEFAULT_AZURE_LLM_MODEL = "gpt-4.1-mini"

# --- Extraction ----------------------------------------------------------

# Entity types the LLM extracts; each becomes the second Falkor label (`:Entity:Character`).
ENTITY_TYPES = ("Character", "Place", "Organization", "Group", "Object", "Concept")
# Every node type in the book graph. Events come from the event list, chapters from parsing,
# communities from community detection; none of them is extracted as an entity.
NODE_TYPES = (*ENTITY_TYPES, "Event", "Chapter", "Community")

# Story relations: the vocabulary the LLM extracts from. Anything else the LLM invents maps to
# FALLBACK_REL.
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
# Edges built in code, never by the LLM.
STRUCTURAL_RELS = frozenset(
    {"INVOLVES", "AT", "BEFORE", "CAUSES", "IN_CHAPTER", "IN_COMMUNITY"}
)

# Pages sent to the LLM at the same time during per-page graph generation.
DEFAULT_EXTRACTION_BATCH_SIZE = 10
# Extraction passes per ingestion; each pass retries only the pages that failed.
EXTRACTION_ATTEMPTS = 3

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
# Description embedding similarity (0-1) at or above which two same-type entities go to the judge.
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

# Other properties the read queries filter or sort on: (label, property).
PROPERTY_INDEXES = (
    ("Event", "story_pos"),
    ("Event", "chapter"),
    ("Chapter", "title"),
)

# Node labels that get a vector index on `embedding` once embeddings exist.
# `Chunk` nodes are optional: only use this label if chunk nodes are actually written to FalkorDB.
VECTOR_INDEXED_LABELS = ("Entity", "Event", "Chunk")
VECTOR_SIMILARITY = "cosine"

# Properties stripped from read results (large, only used by vector search).
OMITTED_GRAPH_PROPS = frozenset({"embedding"})

# Default cap on edges returned by `graph.entity_relations`.
DEFAULT_RELATION_LIMIT = 50
# Chunk ids kept on a relation / event node, so a tool can point at its evidence directly.
MAX_EDGE_CHUNK_IDS = 5

# Rows per write query when loading vectors (each row carries a full embedding).
VECTOR_WRITE_BATCH = 100
# Seconds the name index of a book (every entity's name and aliases) is kept in memory.
ENTITY_INDEX_TTL_SECONDS = 300

# Substring of the FalkorDB error raised when an index already exists.
FALKOR_INDEX_EXISTS_ERROR = "already indexed"
# Substring of the FalkorDB error raised when deleting a graph that does not exist.
FALKOR_MISSING_GRAPH_ERROR = "empty key"

# --- Agent tools (MCP server) ---------------------------------------------

SESSIONS_COLLECTION = "chat_sessions"
MESSAGES_COLLECTION = "messages"
NOTES_COLLECTION = "notes"
COMMUNITIES_COLLECTION = "communities"

# Hard caps so a tool result always fits in the LLM context.
TOOL_MAX_RESULTS = 20  # entities / chunks / communities per call
TOOL_MAX_HOPS = 2  # get_neighbors
TOOL_MAX_PATH_HOPS = 6  # find_path
TOOL_MAX_FRONTIER = 200  # nodes expanded per BFS level in find_path
TOOL_MAX_CHUNKS_PER_CALL = 10  # get_chunks
TOOL_MAX_TIMELINE_EVENTS = 100  # get_timeline / get_chapter_summary
TOOL_SNIPPET_CHARS = 400  # search_chunks excerpt length
TOOL_MAX_HISTORY = 50  # get_history
TOOL_DESCRIPTION_CHARS = 400  # entity description length in tool results
TOOL_MAX_NEIGHBORS = 50  # get_neighbors / get_relations
TOOL_BFS_BATCH = 100  # nodes per expand() query in find_path
TOOL_EXPAND_PER_NODE = 30  # strongest edges taken from each node when expanding
TOOL_MAX_EVIDENCE = 20  # get_evidence
TOOL_PASSAGE_CHARS = 700  # retrieve: length of each returned passage excerpt

# --- Retrieval ------------------------------------------------------------

# Name matching: a question word this similar (0-100) to a name word counts as that word.
FUZZY_NAME_CUTOFF = 85
FUZZY_MIN_WORD_LEN = 4
FUZZY_PENALTY = 0.9  # a fuzzy name match scores at most this fraction of an exact one
# Lexical score at or above which a name hit is "strong": no embedding fallback needed.
STRONG_NAME_SCORE = 0.8
# Semantic entity hits need at least this cosine similarity and score at most SEMANTIC_WEIGHT.
SEMANTIC_MIN_SIM = 0.3
SEMANTIC_WEIGHT = 0.7
SEMANTIC_ENTITY_K = 5
# Reciprocal-rank-fusion constant used to merge keyword and vector rankings.
RRF_K = 60
# Chunk candidates asked from the vector index per search (before chapter / page filtering).
VECTOR_CHUNK_K = 40
# Keyword search looks only at chunks containing one of the rarest query words.
KEYWORD_RAREST_TERMS = 3
KEYWORD_CANDIDATE_CAP = 600
# Term-frequency saturation (BM25 k1) for keyword scoring.
KEYWORD_TF_K1 = 1.2
# retrieve(): linked entities / events / passages returned.
RETRIEVE_ENTITIES = 4
RETRIEVE_RELATIONS_PER_ENTITY = 6
RETRIEVE_EVENTS = 6
RETRIEVE_PASSAGES = 6

# --- Communities ----------------------------------------------------------

COMMUNITY_MIN_SIZE = 3  # entities
COMMUNITY_MAX_LEVELS = 3  # level 0 = most detailed
COMMUNITY_PROMPT_ENTITIES = 25  # members described to the LLM per community
COMMUNITY_PROMPT_RELATIONS = 20
COMMUNITY_DESCRIPTION_CHARS = 240
COMMUNITY_SEED = 42
DEFAULT_EMBEDDING_BATCH_SIZE = 64
EMBED_TEXT_CHARS = 6000  # longest text sent to the embedding model

# --- Book Q&A agent -------------------------------------------------------

DEFAULT_CHAT_HISTORY_BUDGET = 6000  # tokens of conversation before older turns are summarised
HISTORY_KEEP_MESSAGES = 6  # most recent turns kept verbatim when summarising
MAX_MODEL_CALLS = 15  # model calls per question (tool loop bound)
MAX_CITATIONS = 8
# Tools the model may call; the conversation is already in its context.
AGENT_EXCLUDED_TOOLS = frozenset({"get_history", "get_session_summary"})
TITLE_CHARS = 60

