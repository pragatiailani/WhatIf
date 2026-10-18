"""Fixed names and defaults. Environment-specific settings live in `app.config`."""

# --- Mongo collections ---------------------------------------------------

USERS_COLLECTION = "users"
BOOKS_COLLECTION = "books"
CHUNKS_COLLECTION = "chunks"
MENTIONS_COLLECTION = "mentions"
GRAPH_LEVELS_COLLECTION = "graph_levels"
INKITT_STORIES_COLLECTION = "inkitt_stories"
VECTORS_COLLECTION = "vectors"

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
# Extraction passes per ingestion; each pass retries only the pages that failed (rate limits etc.).
EXTRACTION_ATTEMPTS = 3

# Minimum fraction of an evidence quote that must be found in the chunk to accept the match.
MIN_QUOTE_MATCH = 0.8

# --- Merge ---------------------------------------------------------------

# LLM and embedding calls in flight at once while merging page graphs.
DEFAULT_MERGE_CONCURRENCY = 8
# Merge passes per ingestion; saved nodes are skipped, so each pass retries only the failed ones.
MERGE_ATTEMPTS = 3

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

# --- Communities -----------------------------------------------------------

# Leiden (modularity) over the entity graph: story relations plus co-appearance in events.
COMMUNITY_SEED = 42  # fixed, so a rebuild of the same graph finds the same groups
COMMUNITY_EVENT_WEIGHT = 0.5  # edge weight added per event two entities both take part in
COMMUNITY_MIN_SIZE = 3  # smaller groups get no summary
COMMUNITY_MAX = 40  # largest groups kept per book (one LLM summary each)
# What a community summary prompt shows, most important first.
COMMUNITY_PROMPT_ENTITIES = 25
COMMUNITY_PROMPT_RELATIONS = 40
COMMUNITY_PROMPT_EVENTS = 20
OVERVIEW_CID = 0  # the level-1 community: the whole book, summarised from the groups

# --- Retrieval (embeddings) ------------------------------------------------

EMBED_BATCH = 64  # texts per embedding request
EMBED_MAX_CHARS = 8000  # longer texts are cut before embedding (model limit is ~8k tokens)
RRF_K = 60  # reciprocal rank fusion: score = sum of 1 / (RRF_K + rank) over the rankings
SEMANTIC_CANDIDATES = 30  # vector hits per kind that go into fusion
# Lowest cosine similarity for an entity found only by meaning (no name match) in find_entities.
SEMANTIC_ENTITY_MIN_SCORE = 0.3
RETRIEVE_SEEDS = 5  # retrieve: entities the query is about
RETRIEVE_RELATIONS = 15  # retrieve: graph edges around the seeds
RETRIEVE_EVENTS = 5  # retrieve: events closest to the query
RETRIEVE_COMMUNITIES = 2  # retrieve: communities the seeds belong to
RETRIEVE_COMMUNITY_CHARS = 600  # retrieve: summary length per community
RETRIEVE_PASSAGE_CHARS = 1500  # retrieve: text per passage; get_chunks has the full page

# --- Book Q&A agent ----------------------------------------------------------

DEFAULT_CHAT_HISTORY_BUDGET = 6000  # tokens of conversation before older turns are summarised
HISTORY_KEEP_MESSAGES = 6  # most recent messages kept verbatim when summarising
MAX_MODEL_CALLS = 80  # model calls per message (tool loop bound); a draft queue needs many
MAX_CITATIONS = 8
CITATION_QUOTE_CHARS = 240  # excerpt shown with each citation
TRACE_FOUND_NAMES = 4  # names listed per kind in a trace step's "found" line
TRACE_TEXT_CHARS = 160  # longest plain-text or error result shown in a trace step
REASONING_MAX_FACTS = 40  # relations offered to the model when explaining an answer
# Tools the model may call; the conversation is already in its context.
AGENT_EXCLUDED_TOOLS = frozenset({"get_history", "get_session_summary"})

# --- Inkitt --------------------------------------------------------------

INKITT_MCP_URL = "https://mcp-server.inkitt.com/mcp"
# Inkitt tools the chat agent may call. The server also has delete and customer-support (`cs_*`)
# tools; those are never loaded.
INKITT_AGENT_TOOLS = frozenset(
    {
        "get_settings", "list_stories", "get_story", "update_story", "list_chapters",
        "get_chapter", "publish_chapter", "unpublish_chapter", "publish_all_chapters",
        "unpublish_all_chapters", "get_cover", "update_cover",
    }
)  # fmt: skip
INKITT_SUMMARY_CHARS = 2205
INKITT_CHAPTER_NAME_CHARS = 200
TITLE_CHARS = 60

# --- Storylines ------------------------------------------------------------------

STORYLINES_COLLECTION = "storylines"
STORYLINE_OPS_COLLECTION = "storyline_ops"
STORYLINE_ITEMS_COLLECTION = "storyline_items"

DEAD_STATUSES = frozenset({"dead", "killed", "deceased"})
MAX_QUEUE_ITEMS = 400  # items a draft queues at once (events and relations after the divergence)
OPEN_ITEMS_PER_CALL = 5  # open items returned per get_open_items call, to keep the context small
MAX_VERIFY_ISSUES = 20  # issues reported by one verification

# --- Storyline prose ---------------------------------------------------------------

STORYLINE_CHUNKS_COLLECTION = "storyline_chunks"
PROSE_SEGMENT_CHARS = 12000  # original text rewritten per LLM call; a long chapter is done in pieces
PROSE_CONTEXT_CHARS = 800  # tail of the previous rewritten piece shown for continuity
PROSE_MAX_RETRIES = 1  # regenerations when the passage check finds contradictions

# --- Logging ---------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 3
SLOW_QUERY_SECONDS = 1.0  # a graph query slower than this is logged as a warning
LOG_VALUE_CHARS = 200  # longest argument value written to a log line
# Libraries that log a line per request or connection; kept at WARNING unless LOG_LEVEL=DEBUG.
NOISY_LOGGERS = ("httpx", "httpcore", "openai", "urllib3", "pymongo", "mcp", "asyncio", "watchfiles")
PROSE_WORKERS = 4  # chapters written at the same time when a storyline is committed
PASSAGE_PREVIEW_CHARS = 1500  # how much of the first rewritten chapter the reading page shows
