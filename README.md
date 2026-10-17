# WhatIf?

A chatbot that turns a book into a knowledge graph. You can ask it questions about the story, or rewrite the story into your own storyline. Each storyline is saved and persists across the session.

Built with **LangGraph**, **FalkorDB** and **MongoDB**.

---

## Features

- **Book ingestion.** The book is split into chunks, and one graph is built per chunk. Those graphs are then merged recursively, level by level, until one graph represents the whole book.
- **Grounded Q&A.** Answers come from the graph and the original text, with chapter and page citations.
  - *Local search* answers specific questions (characters, events).
  - *Global search* answers big-picture questions (themes, summaries).
- **Storylines.** The user changes the plot ("what if Ravi never left Delhi?"). The bot rewrites the affected events and answers later questions from the new storyline.
- **Checkpoints and undo.** Save points inside a storyline, with restore that can itself be undone.
- **Forks.** Branch any storyline you can access into an independent copy.
- **Sharing.** Books from the catalog are shared by everyone. User uploads and storylines are private unless the owner shares them.

---

## Architecture

```
            ┌──────────────┐     ┌──────────────────────────┐
  Upload ──▶│  Ingestion   │────▶│ FalkorDB  book_<id>      │
            │  (LangGraph) │     │  canon + storyline delta │
            └──────┬───────┘     └────────────▲─────────────┘
                   │                          │
                   ▼                          │ repository layer
            ┌──────────────┐     ┌────────────┴─────────────┐
            │   MongoDB    │◀───▶│  Chat / Inference /      │◀── User
            │ text, ops,   │     │  Storyline graphs        │
            │ sessions     │     │  (LangGraph)             │
            └──────────────┘     └──────────────────────────┘
```

| Store | Holds |
|---|---|
| FalkorDB | The final book graph (entities, events, communities, relations) plus storyline deltas tagged with `sid` |
| MongoDB | Raw text, chunks, mentions/pages, intermediate merge levels, storyline op log, sessions, messages, LangGraph checkpoints |

---

## Pipelines

### 1. Ingestion
1. Upload the book. Create a `books` document and queue a job.
2. Parse and clean: extract text, detect chapters, build `page_spans`.
3. Chunk: one chunk per page.
4. Per-chunk extraction (LangGraph `Send` fan-out): the LLM returns entities, events and relations, each with an **evidence quote**.
5. Save the level-0 chunk graphs to `graph_levels` and the mentions to `mentions`.
6. Tree merge (`app/merge`): a binary tree of adjacent merges, each node starting as soon as its two children exist. Clear matches (same name and type) are decided in code; only ambiguous entity and event pairs go to an LLM judge, in one batched call per merge. Nodes and edges are merged with provenance kept (pages, chunk ids, page-level keys), and each node is saved to `graph_levels` so a resumed run skips finished subtrees. The root's entities then get final descriptions.
7. Write the final graph to FalkorDB and create the vector indexes.
8. Run community detection (Leiden) and write summaries. Set the book's status to `ready`.

Ingestion is resumable. LangGraph's MongoDB checkpointer saves progress, so a crash at level N resumes from level N.

### 2. Inference (questions)
1. Load chat context: the rolling summary plus the last N turns.
2. Rewrite the question so it stands alone, then route it: `local`, `global`, `canon` or `edit`.
3. **Local:** match the seed entities by vector search, expand k hops in Cypher, and fetch the source chunks.
4. **Global:** pick the top-k community summaries, then map-reduce partial answers.
5. Assemble the context: rerank and fit it to the token budget.
6. Answer with citations, run a grounding check, stream the response, and save the retrieval trace.
7. If the grounding check fails, widen the search once. If it still fails, answer "not in the book".

### 3. Chat management
Open or resume a session (`thread_id`), then save the user message. If the history exceeds the token budget, fold the old turns into a rolling summary. Run the graph with the checkpointer, then save the assistant message (citations, tokens, trace). After the turn, set the title, update `last_active` and record feedback.

### 4. Storyline update
1. The router classifies the message as an `edit`.
2. The LLM parses the edit into **ops**: `add_*`, `hide`, `modify`.
3. Find the **divergence point**, the earliest `story_pos` the edit touches.
4. Impact analysis: linked events after the divergence point are marked **stale** (hidden, not deleted).
5. Generate a continuation passage and store it in `storyline_chunks`.
6. Extract and resolve the passage with the ingestion extractor. Tag the results with `sid`.
7. Consistency check against active canon. If it fails, ask the user to adjust the edit.
8. Apply the ops: write the Mongo op log first, then the Falkor delta, and advance `head_op_seq`.
9. Post-apply jobs: rewrite the dirty community summaries, and switch the storyline from delta to copy mode if it has grown large.
10. Reply with a diff of what changed and the new passage.

### 5. Checkpoints and forks
- **Save checkpoint:** stores a name and `op_seq`. Nothing is copied.
- **Restore to N:** auto-save the current head, revert ops after N in reverse order, and set the head to N. Reverted ops are marked, not deleted.
- **Fork:** check access, create a storyline with `parent_storyline_id` and `fork_op_seq`, copy the ops up to `fork_op_seq` under the new `sid`, then materialize them.

---

## Agent tools (MCP server)

`app/mcp_server` exposes the tools the inference graph calls. Run it with `python -m app.mcp_server`
(stdio) or `python -m app.mcp_server streamable-http`. Search uses names, aliases and keywords, not embeddings.

| Group | Tools |
|---|---|
| Graph | `find_entities`, `get_neighbors` (1-2 hops), `find_path`, `get_relations` (by type), `search_communities` |
| Text | `get_chunks`, `search_chunks`, `get_chapter_summary`, `get_timeline` |
| Session | `get_history`, `get_session_summary`, `save_note` |
| Storyline (read) | `get_dependents`, `get_state_at`, `read_chapter` |
| Storyline (draft) | `start_draft`, `get_open_items`, `resolve_item`, `verify_draft`, `commit_draft`, `discard_draft`, `generate_passage` |

Agents live in `app/agents` and are looked up by name: `await get_agent("whatif").run(book_id, session_id, question)`.
`whatif` is the one agent behind every chat: a single ReAct agent (`langchain.agents.create_agent`) bound to all of these tools plus its planning tools (`write_todos`, `read_todos`, `think_tool`). The conversation is
checkpointed to MongoDB (`checkpoints`, `checkpoint_writes`), one thread per chat session, and older turns are
summarised past `CHAT_HISTORY_BUDGET`. Each question and answer is also saved to `messages` with its citations.
**What-if editing.** The same agent handles it, in the same chat, when the reader asks for a change: no mode to pick and no hand-off. `start_draft` queues every later event and relation involving the changed characters (the ripple queue); `resolve_item` keeps, hides or rewrites each one and can pull more characters in; `verify_draft` runs code checks plus an LLM continuity check per chapter; `commit_draft` is refused unless the latest verification passed on the current draft. Ops are logged in `storyline_ops`; storyline graph items carry `sid` and `created_by_op`, and hidden items come from the op log.

Graph and text tools take `book_id`, session tools take `session_id`. The inference graph fills these
in from the user's session; the LLM never picks them.

---

## Storyline storage model

- The canon graph is **never modified**. Storyline additions go into the same graph, tagged `sid`.
- Removals use a per-storyline **hidden set** (`hidden_node_keys`, `hidden_edge_ids`).
- Every query filters on `(sid IS NULL OR sid = $sid) AND NOT hidden`.
- A heavy storyline (more than about 500 ops, or more than about 2k hidden items) switches to **copy mode**: `GRAPH.COPY` the base graph and replay the ops.
- `storyline_ops` is the **source of truth**. Any storyline graph can be rebuilt from canon plus its ops.

---

## Page and event tracking

- Chunks store `page_spans: [{page, char_start, char_end}]`.
- Extraction returns an exact evidence quote. Fuzzy-match it to an offset in the chunk, then map the offset to a page. **Page numbers produced by the LLM are never used.**
- Events are their own nodes, with `story_pos = chunk.seq * 1000 + order` (narrative order) and an optional `story_time` (in-world time, for flashbacks).
- Falkor stores only `first_page`, `last_page` and `mention_count`. Full mentions live in the Mongo `mentions` collection.
- Generated storyline events get a fractional `story_pos` (e.g. `5023.5`) and cite the storyline chunk, not a page.

---

## Data model

### FalkorDB
```
(:Entity {key, name, type, aliases[], description, embedding,
          first_page, last_page, mention_count, community_id, sid, created_by_op})
(:Event  {key, summary, story_pos, story_time?, chapter, first_page,
          embedding, sid, created_by_op})
(:Chapter {title, first_page})
(:Community {cid, level, title, summary, embedding})
-[:REL {rel, description, weight, story_pos, sid, created_by_op}]->
-[:INVOLVES {role}]->  -[:AT]->  -[:BEFORE]->  -[:CAUSES]->  -[:IN_CHAPTER]->  -[:IN_COMMUNITY]->
```
Entity types (the second label, `:Entity:Character`): Character, Place, Organization, Group, Object, Concept.
`REL.rel` is one of: FRIEND_OF, ENEMY_OF, RIVAL_OF, LOVES, MARRIED_TO, PARENT_OF, CHILD_OF, SIBLING_OF,
RELATIVE_OF, MENTOR_OF, WORKS_FOR, LEADS, MEMBER_OF, ALLY_OF, BETRAYS, HELPS, HARMS, KILLS, SAVES, MEETS,
TALKS_TO, TRUSTS, DISTRUSTS, FEARS, OWNS, USES, CREATES, SEEKS, LIVES_IN, TRAVELS_TO, LOCATED_IN, PART_OF,
BELIEVES_IN, SYMBOLIZES, or OTHER when nothing fits.

### MongoDB collections
| Collection | Purpose |
|---|---|
| `users` | Accounts |
| `books` | Metadata, `content_hash`, visibility, access, status, stats |
| `chunks` | Text, `seq`, chapter, `page_spans` |
| `mentions` | Evidence quotes mapped to chunk, page and offsets |
| `graph_levels` | Intermediate merge graphs per level and index |
| `ingestion_jobs` | Progress and errors for each ingestion run |
| `communities` | Canon community summaries and embeddings |
| `storylines` | Mode, head/applied seq, hidden sets, parent/fork info, visibility |
| `storyline_ops` | Append-only op log (`applied`/`reverted`) |
| `storyline_chunks` | Generated narrative passages |
| `storyline_checkpoints` | Named save points (`op_seq`) |
| `community_overrides` | Per-storyline community summaries |
| `chat_sessions` | Session with `book_id`, `storyline_id`, rolling summary |
| `messages` | Content, `seq`, intent, route, citations, op_seqs, trace, feedback |
| `notes` | Answers or passages a user bookmarked from a chat |
| `checkpoints`, `checkpoint_writes` | Managed by LangGraph `MongoDBSaver` |

See `docs/schema.md` for the full field-level schema.

---

## Engineering rules

1. **All graph access goes through the repository layer.** It injects the `sid` and hidden-set filter and checks access. Tools never run raw Cypher.
2. **The op log comes first.** Write ops to Mongo before Falkor. On failure, replay from `falkor_applied_seq`.
3. **Pages come from evidence.** Never take a page number from LLM output.
4. **Canon is immutable.** Storylines only add tagged items or hide existing ones.
5. **Isolation is tested.** A test asserts that one storyline's content never appears in another storyline's answers.

---

## Proposed project structure

```
whatif/
├── app/
│   ├── api/              # REST/streaming endpoints
│   ├── agents/           # agent registry + LangGraph agents (whatif), MCP tool binding
│   ├── graphs/           # LangGraph: ingestion, storyline
│   ├── extraction/       # prompts, parsers, evidence matching
│   ├── merge/            # pairing, entity resolution, merge
│   ├── retrieval/        # local, global, rerank, context
│   ├── storyline/        # ops, impact, checkpoints, forks
│   ├── repo/             # repository layer (Falkor + Mongo)
│   └── config.py
├── evals/                # question sets + runner
├── tests/
├── docs/schema.md
├── docker-compose.yml
└── README.md
```

---

## Getting started

### Prerequisites
- Python 3.10+
- Docker & Docker Compose
- [UV](https://docs.astral.sh/uv/) (fast Python package manager)

### Setup

1. **Install UV** (if not already installed):
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (via PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or use Homebrew/Chocolatey
brew install uv  # macOS
choco install uv  # Windows
```

2. **Clone and setup**:
```bash
git clone <repo>
cd storyline
cp .env.example .env          # Copy template and update with your secrets
# Edit .env and set secure passwords for MongoDB, LLM keys, etc.
```

⚠️ **Security:** `.env` is in `.gitignore` — never commit passwords or API keys. Use a secure password manager to generate strong credentials.

3. **Install dependencies** (using UV):
```bash
uv sync                       # Install from pyproject.toml
```

4. **Start databases**:
```bash
docker compose up -d          # FalkorDB + MongoDB
docker compose ps             # Verify services are running
```

5. **Run the FastAPI server**:
```bash
uv run fastapi dev app/main.py
# Server runs at http://localhost:8000
# Docs at http://localhost:8000/docs
```

6. **Try the agent** (needs both databases running, a book ingested and the LLM keys in `.env`):
```bash
uv run python -m scripts.chat                 # chat in the terminal (/history, /new, /quit)
uv run python -m scripts.chat --book <id>     # skip the book picker
```
Or call the API (`/docs` has a form for each): `GET /books`, `POST /sessions {"book_id": ...}`,
`POST /sessions/{id}/chat {"question": ...}`, `GET /sessions/{id}/messages`. No auth yet.

7. **CLI commands** (optional):
```bash
uv run python -m app.cli ingest path/to/book.pdf
uv run python -m app.cli chat --book <book_id>
```

### Docker management

```bash
docker compose up -d          # Start services in background
docker compose down           # Stop services
docker compose logs -f        # View logs
docker compose ps             # Check status
```

FalkorDB Browser runs at http://localhost:3000. Connect with host `localhost`, port `6379` and
`FALKOR_PASSWORD`; each book is a graph named `book_<book_id>`.

### Environment variables
| Variable | Description | Default |
|---|---|---|
| `AZURE_OPENAI_LLM_KEY`, `AZURE_LLM_ENDPOINT` | Azure OpenAI connection used for extraction and answers | - |
| `AZURE_LLM_API_VERSION` | Azure OpenAI API version | `2025-01-01-preview` |
| `AZURE_LLM_MODEL` | Azure deployment name of the chat model | `gpt-4.1-mini` |
| `AZURE_EMBEDDING_MODEL` | Azure embedding deployment used to compare entities while merging (empty: names only) | - |
| `MONGO_USER`, `MONGO_PASSWORD` | MongoDB root credentials (used by Docker Compose) | **required** |
| `MONGO_URI`, `MONGO_DB` | MongoDB connection (`MONGO_URI` includes the credentials) | **required**, `whatif` |
| `FALKOR_HOST`, `FALKOR_PORT` | FalkorDB connection | `localhost`, `6379` |
| `FALKOR_PASSWORD` | FalkorDB password (Compose sets it via `--requirepass`) | **required** |
| `SERVER_HOST`, `SERVER_PORT` | API server bind address | `127.0.0.1`, `8000` |
| `DEBUG` | Enables auto-reload when running `python -m app.main` | `false` |
| `EXTRACTION_BATCH_SIZE` | Pages sent to the LLM in parallel during extraction | `10` |
| `MERGE_CONCURRENCY` | LLM and embedding calls in flight while merging page graphs | `8` |
| `CHAT_HISTORY_BUDGET` | Token limit before rolling summary | `6000` |
| `MCP_SERVER_URL` | Streamable-HTTP URL of the tool server; empty runs it over stdio for each question | empty |
| `STORYLINE_COPY_THRESHOLD` | Op count before copy mode | `500` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR` | `INFO` |
| `LOG_FILE` | Also write logs to this rotating file (empty: console only) | empty |

---

## Logging

All backend logs go to stderr (and to `LOG_FILE` if set) in one format:
`time LEVEL logger [request-id] message`. Set `LOG_LEVEL=DEBUG` to also see graph queries and per-node merge steps.

- **Requests:** one line each with status and duration (`app.request`). Every response carries an `X-Request-ID`; quote it when reporting a problem. Background work started by a request (ingestion) keeps its id.
- **Ingestion, merge, storylines, agent:** stage changes, counts and timings (`app.ingestion`, `app.merge.*`, `app.storyline.*`, `app.agents.whatif`), plus a line per agent tool call (`app.agent.tools`) and per model call with tokens (`app.llm`).
- **Tool server:** runs as its own process and logs to stderr, which shows up in the API's console; it records tracebacks for crashes (`app.mcp`).
- **Never logged:** passwords, tokens, API keys, emails, prompts or book text. Long argument values are clipped.
