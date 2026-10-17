"""Prompts for community summaries: one per group, then one overview of the whole book."""

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.constants import (
    COMMUNITY_PROMPT_ENTITIES,
    COMMUNITY_PROMPT_EVENTS,
    COMMUNITY_PROMPT_RELATIONS,
)
from app.merge.publish import entity_description
from app.repo.models import Community, GraphPayload


class CommunityReport(BaseModel):
    title: str = Field(description="3-8 words naming the group by its part in the story")
    summary: str = Field(description="The summary, using only the information given")


class BookOverview(BaseModel):
    """Its own schema: the field descriptions steer the model as much as the prompt does."""

    title: str = Field(description="The whole book's central story in 3-8 words, not a group's")
    summary: str = Field(description="6-10 sentences covering the whole book and every group")


COMMUNITY_SYSTEM_PROMPT = """You summarise one group of closely connected characters, places and things,
taken from a knowledge graph of a book. Readers use the summary to answer big-picture
questions about the book, so say what the group means for the story.

Write:
- title: 3-8 words naming the group by its part in the story (e.g. "The rebellion on Manor
  Farm"), not just a list of names.
- summary: 4-6 sentences. Who is in the group and how they are related; what happens to them
  over the story, in order; the conflicts and themes they carry.

Use only the information given; do not add outside knowledge of the book. Return JSON:
{"title": ..., "summary": ...}"""

OVERVIEW_SYSTEM_PROMPT = """You write an overview of a whole book from summaries of its groups of characters.
Write:
- title: the whole book's central story in 3-8 words. It covers every group, so it must not
  repeat any one group's title.
- summary: 6-10 sentences. The main characters, the arc of the plot from beginning to end, and
  the main themes. Mention how the groups connect; give each group its due, not only the first.

Use only the summaries given. Return JSON: {"title": ..., "summary": ...}"""

_DESCRIPTION_CHARS = 300


def _clip(text: str, limit: int = _DESCRIPTION_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_community_messages(
    book_title: str, members: list[str], payload: GraphPayload
) -> list[BaseMessage]:
    """`members` are entity keys, most connected first."""
    inside = set(members)
    by_key = {e["key"]: e for e in payload.entities}
    lines = [f"Book: {book_title}", "", "Members:"]
    for key in members[:COMMUNITY_PROMPT_ENTITIES]:
        e = by_key[key]
        lines.append(f"- {e['name']} ({e['type']}): {_clip(entity_description(e))}")
    if len(members) > COMMUNITY_PROMPT_ENTITIES:
        lines.append(f"- … and {len(members) - COMMUNITY_PROMPT_ENTITIES} more")

    relations = sorted(
        (r for r in payload.relations if r["src"] in inside and r["dst"] in inside),
        key=lambda r: -(r.get("weight") or 1),
    )[:COMMUNITY_PROMPT_RELATIONS]
    if relations:
        lines += ["", "Relations:"]
        for r in relations:
            note = f": {_clip(r['description'], 150)}" if r.get("description") else ""
            lines.append(f"- {by_key[r['src']]['name']} {r['rel']} {by_key[r['dst']]['name']}{note}")

    # Events with at least two members, in story order: what happens to the group.
    events = [
        e for e in sorted(payload.events, key=lambda e: e["story_pos"])
        if len({p["key"] for p in e.get("participants", [])} & inside) >= 2
    ]  # fmt: skip
    if events:
        step = max(1, len(events) // COMMUNITY_PROMPT_EVENTS)
        lines += ["", "Events, in story order:"]
        for e in events[::step][:COMMUNITY_PROMPT_EVENTS]:
            where = f"[{e['chapter']}] " if e.get("chapter") else ""
            lines.append(f"- {where}{_clip(e['summary'], 200)}")
    return [SystemMessage(COMMUNITY_SYSTEM_PROMPT), HumanMessage("\n".join(lines))]


def build_overview_messages(book_title: str, groups: list[Community]) -> list[BaseMessage]:
    blocks = [f"## {c.title}\n{c.summary}" for c in groups]
    text = f"Book: {book_title}\n\n" + "\n\n".join(blocks)
    return [SystemMessage(OVERVIEW_SYSTEM_PROMPT), HumanMessage(text)]
