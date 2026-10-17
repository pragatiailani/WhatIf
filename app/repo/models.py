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
    inkitt_pat: str | None = None  # Inkitt access token, encrypted with `app.crypto`
    created_at: datetime = Field(default_factory=utcnow)


class UserMemory(_Doc):
    """A persistent preference or useful user context shared across chat sessions."""

    user_id: str
    type: Literal["preference", "fact"]
    key: str
    value: str
    scope_type: Literal["global", "book", "storyline"] = "global"
    book_id: str | None = None
    storyline_id: str | None = None
    source: Literal["explicit", "inferred"] = "inferred"
    confidence: float = 0.7
    evidence_count: int = 1
    status: Literal["pending", "active", "superseded", "deleted"] = "active"
    last_session_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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
    # Graph nodes the answer's tools returned: {seeds, expanded, edges, communities}, for the UI.
    graph_refs: dict[str, list] = Field(default_factory=dict)
    # Why the answer holds: {chain: [relation + {src, dst, why}], conclusion}; None without one.
    reasoning: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Note(_Doc):
    """A bookmarked answer or passage the user saved from a chat."""

    session_id: str
    book_id: str
    message_id: str | None = None
    text: str
    created_at: datetime = Field(default_factory=utcnow)


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
