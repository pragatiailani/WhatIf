"""Typed records returned and accepted by the repository layer."""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    """Current UTC time truncated to milliseconds, the precision Mongo stores."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class _Doc(BaseModel):
    """Mongo document: `id` maps to `_id`."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=new_id, alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


# --- Mongo ---------------------------------------------------------------

class User(_Doc):
    name: str
    email: str  # lower-cased
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


BookStatus = Literal["queued", "parsing", "extracting", "merging", "indexing", "ready", "failed"]
Visibility = Literal["public", "private"]


class Book(_Doc):
    title: str
    author: str | None = None
    content_hash: str
    owner_id: str | None = None  # None for catalog books
    visibility: Visibility = "private"
    shared_with: list[str] = Field(default_factory=list)
    status: BookStatus = "queued"
    error: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PageSpan(BaseModel):
    page: int
    char_start: int
    char_end: int


class Chunk(_Doc):
    book_id: str
    seq: int
    chapter: str | None = None
    text: str
    page_spans: list[PageSpan] = Field(default_factory=list)
    token_count: int | None = None


class Mention(_Doc):
    book_id: str
    node_key: str  # Entity/Event key in the graph
    chunk_id: str
    chunk_seq: int
    quote: str
    char_start: int  # offsets within the chunk text
    char_end: int
    page: int


class ChatSession(_Doc):
    book_id: str
    user_id: str | None = None
    storyline_id: str | None = None  # None = canon
    title: str | None = None
    summary: str = ""  # rolling summary of turns folded out of the history window
    created_at: datetime = Field(default_factory=utcnow)
    last_active: datetime = Field(default_factory=utcnow)


class Message(_Doc):
    session_id: str
    seq: int = 0  # position in the session, set by `sessions.add_message`
    role: Literal["user", "assistant"]
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)  # {chunk_id, page, chapter}
    trace: list[dict[str, Any]] = Field(default_factory=list)  # tool calls behind an answer
    route: str | None = None  # local, global or edit: how the answer was produced
    retrieval: dict[str, Any] = Field(default_factory=dict)  # entity keys the answer touched
    diff: dict[str, Any] | None = None  # what an editing turn changed in the storyline
    created_at: datetime = Field(default_factory=utcnow)


class Note(_Doc):
    """A bookmarked answer or passage the user saved from a chat."""

    session_id: str
    book_id: str
    message_id: str | None = None
    text: str
    created_at: datetime = Field(default_factory=utcnow)


class DraftState(BaseModel):
    """An uncommitted edit session on a storyline: where it diverges and who it touches."""

    premise: str
    anchor_event: str
    divergence_pos: float
    affected: list[str] = Field(default_factory=list)  # entity keys whose situation changed


class Storyline(_Doc):
    book_id: str
    owner_id: str | None = None
    name: str
    premise: str = ""  # the first "what if"
    visibility: Literal["public", "private"] = "private"
    head_seq: int = 0  # seq of the last op written
    committed_seq: int = 0  # ops up to here passed verification
    version: int = 0  # bumped by every draft change; a verification is valid for one version
    verified_version: int | None = None  # version that last passed verification
    draft: DraftState | None = None
    last_verification: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


OpKind = Literal["set_fact", "hide_node", "hide_edge", "add_event"]


class StorylineOp(_Doc):
    """Append-only edit log entry; the source of truth for a storyline."""

    storyline_id: str
    seq: int
    kind: OpKind
    payload: dict[str, Any]
    status: Literal["applied", "reverted"] = "applied"
    created_at: datetime = Field(default_factory=utcnow)


class StorylineItem(_Doc):
    """One thing in the ripple queue: a canon event or relation the draft must decide on."""

    storyline_id: str
    kind: Literal["event", "relation"]
    ref: str  # event key or relation id
    pos: float
    summary: str
    chapter: str | None = None
    reason: str
    state: Literal["open", "resolved"] = "open"
    action: str | None = None  # keep / hide / rewrite, once resolved


class StorylineChunk(_Doc):
    """A rewritten chapter of a storyline. Current only while the chapter's visible events still
    match `fingerprint`; after further edits to that chapter it is stale and is regenerated."""

    storyline_id: str
    book_id: str
    chapter: str
    text: str
    fingerprint: str  # hash of the chapter's visible event keys when it was written
    op_seq: int  # the storyline's head_seq then
    seq_start: int  # original chunks the chapter covers
    seq_end: int
    page_spans: list[PageSpan] = Field(default_factory=list)  # the original chapter's pages
    issues: list[str] = Field(default_factory=list)  # contradictions the check still found
    created_at: datetime = Field(default_factory=utcnow)

    def as_chunk(self) -> Chunk:
        """The passage as a chunk, so text tools and citations treat it like the original text."""
        return Chunk(
            id=self.id, book_id=self.book_id, seq=self.seq_start, chapter=self.chapter,
            text=self.text, page_spans=self.page_spans,
        )  # fmt: skip


class Community(_Doc):
    book_id: str
    cid: int
    level: int = 0
    title: str
    summary: str
    entity_keys: list[str] = Field(default_factory=list)


class GraphPayload(BaseModel):
    """An intermediate (pre-Falkor) graph. Item shapes are owned by extraction/merge."""

    entities: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)


class GraphLevel(_Doc):
    book_id: str
    level: int  # 0 = per-chunk graphs
    index: int  # position within the level
    seq_start: int  # first chunk seq covered
    seq_end: int  # last chunk seq covered (inclusive)
    page_start: int | None = None  # first page covered
    page_end: int | None = None  # last page covered (inclusive)
    chunk_ids: list[str] = Field(default_factory=list)  # chunks covered, in seq order
    graph: GraphPayload
    created_at: datetime = Field(default_factory=utcnow)


# --- Falkor --------------------------------------------------------------


class GraphEntity(BaseModel):
    key: str
    name: str
    type: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    embedding: list[float] | None = None
    first_page: int | None = None
    last_page: int | None = None
    mention_count: int = 0
    community_id: int | None = None
    sid: str | None = None
    created_by_op: int | None = None


class GraphEvent(BaseModel):
    key: str
    summary: str
    story_pos: float
    story_time: str | None = None
    chapter: str | None = None
    first_page: int | None = None
    embedding: list[float] | None = None
    sid: str | None = None
    created_by_op: int | None = None


class GraphRelation(BaseModel):
    id: str = Field(default_factory=new_id)
    src: str  # Entity key
    dst: str  # Entity key
    rel: str
    description: str = ""
    weight: float = 1.0
    story_pos: float | None = None
    sid: str | None = None
    created_by_op: int | None = None
