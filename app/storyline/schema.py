from typing import Literal

from pydantic import BaseModel, Field

Attribute = Literal["status", "location", "note"]


class Fact(BaseModel):
    """Something that is now true of an entity in the storyline, from a story position on."""

    entity: str = Field(description="Entity key or name")
    attribute: Attribute = Field(description="status (alive, dead, missing...), location or note")
    value: str = Field(description="e.g. 'alive', 'dead', 'in hospital', 'Delhi'")


class ContinuityIssue(BaseModel):
    event_key: str | None = Field(default=None, description="Key of the event at fault, if one")
    chapter: str | None = None
    problem: str = Field(description="The concrete contradiction, citing the facts involved")
    suggestion: str = Field(default="", description="How the event could be changed to fit")


class Continuity(BaseModel):
    """The judge's findings for one chapter. Empty when the chapter is consistent."""

    issues: list[ContinuityIssue] = Field(default_factory=list)
