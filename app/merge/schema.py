"""What the LLM returns while merging: duplicate verdicts per merge, final descriptions per book."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class _Verdict(BaseModel):
    id: str = Field(description="Candidate id, e.g. E1 or V1")
    decision: Literal["same", "different"]
    confidence: float = Field(default=0.0, description="0.0-1.0")

    @field_validator("decision", mode="before")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


class EntityVerdict(_Verdict):
    canonical_name: str = Field(default="", description="Best full name, if same")
    reason: str = Field(default="", description="At most 15 words")


class EventVerdict(_Verdict):
    pass


class MergeVerdicts(BaseModel):
    """One verdict per candidate pair."""

    entities: list[EntityVerdict] = Field(default_factory=list)
    events: list[EventVerdict] = Field(default_factory=list)


class EntityDescription(BaseModel):
    key: str
    description: str = Field(description="2-3 sentences, using only the notes given")


class FinalDescriptions(BaseModel):
    descriptions: list[EntityDescription] = Field(default_factory=list)
