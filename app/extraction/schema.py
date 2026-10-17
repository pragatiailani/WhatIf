"""What the LLM returns for one chunk.

Every item carries an `evidence` quote copied from the chunk text. The quote is matched back to
the text in `app.extraction.evidence`; page numbers are never part of the LLM output.
"""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.constants import FALLBACK_REL, REL_VOCABULARY

EntityType = Literal["Character", "Place", "Organization", "Group", "Object", "Concept"]


class ExtractedEntity(BaseModel):
    name: str = Field(description="Most complete name used in the text")
    type: EntityType
    aliases: list[str] = Field(default_factory=list, description="Other names, nicknames, titles")
    description: str = Field(description="One or two sentences, using only this chunk")
    evidence: str = Field(description="Short verbatim quote from the chunk mentioning the entity")


class EventParticipant(BaseModel):
    name: str = Field(description="Name of an extracted entity")
    role: Literal["actor", "target", "witness", "other"] = "other"


class ExtractedEvent(BaseModel):
    summary: str = Field(description="One sentence, e.g. 'Ravi leaves Delhi for Mumbai'")
    participants: list[EventParticipant] = Field(default_factory=list)
    place: str | None = Field(default=None, description="Name of an extracted Place entity")
    story_time: str | None = Field(default=None, description="In-world time, only if stated")
    evidence: str = Field(description="Short verbatim quote from the chunk describing the event")


class ExtractedRelation(BaseModel):
    source: str = Field(description="Name of an extracted entity")
    target: str = Field(description="Name of an extracted entity")
    rel: str = Field(description=f"One of: {', '.join(sorted(REL_VOCABULARY))}")
    description: str = ""
    evidence: str = Field(description="Short verbatim quote from the chunk stating the relation")

    @field_validator("rel")
    @classmethod
    def _normalize_rel(cls, value: str) -> str:
        rel = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
        return rel if rel in REL_VOCABULARY else FALLBACK_REL


class ChunkExtraction(BaseModel):
    """Entities, events (in narrative order) and relations found in a single chunk."""

    entities: list[ExtractedEntity] = Field(default_factory=list)
    events: list[ExtractedEvent] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


# --- keys ----------------------------------------------------------------


def slug(text: str) -> str:
    """Lowercase ASCII with underscores: 'Zoë  Park' -> 'zoe_park'. Empty if nothing usable."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def entity_key(name: str, type_: str) -> str:
    """Normalized name + type, e.g. 'ravi_sharma|character'."""
    return f"{slug(name)}|{type_.lower()}"


def event_key(chunk_seq: int, order: int) -> str:
    """Unique per book: events are numbered by chunk and by order within the chunk."""
    return f"event|{chunk_seq}|{order}"
