"""Prompt for turning one page into a graph."""

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.constants import ENTITY_TYPES, FALLBACK_REL, REL_VOCABULARY
from app.repo.models import Chunk

SYSTEM_PROMPT = f"""You build a knowledge graph from ONE page of a book.

Use only what this page says. Do not use outside knowledge of the book, and do not guess.

ENTITIES: people, places, organizations, objects, concepts and groups that matter to the story.
- Only extract an entity that takes part in at least one event or relation you extract. Skip passing front matter (author, translator, publisher, websites).
- type must be one of: {", ".join(ENTITY_TYPES)}. Use the same type for the same thing on every page:
  a song, law or idea is a Concept; a physical thing is an Object.
- name is the most complete name used on the page. Put other names, nicknames and titles in aliases.
- Resolve pronouns ("he", "she") to the right entity when it is clear from this page.
- description is one or two sentences based only on this page.

EVENTS: concrete things that happen (actions, decisions, arrivals, deaths, meetings). Not descriptions or thoughts.
- List events in the order they happen on the page.
- summary is one plain sentence naming who did what.
- participants and place must use names of entities you extracted, with a role: actor, target, witness or other.
- participants include EVERY extracted entity the event involves, not only characters: objects, groups and
  concepts too (for "Clover sings Beasts of England", both Clover and Beasts of England).
- story_time only if the page states when it happens in the story's world (for example "three years earlier").

RELATIONS: lasting facts linking two extracted entities.
- rel must be one of: {", ".join(sorted(REL_VOCABULARY))}. If none fits, use {FALLBACK_REL}.
- source and target must be names of entities you extracted.

EVIDENCE: every entity, event and relation needs an evidence quote.
- Copy a short span (under 25 words) EXACTLY from the page, character for character. Never paraphrase.
- The quote must show the item, so someone can verify it by reading that span.

If the page has nothing to extract (for example a title page), return empty lists."""


def build_messages(chunk: Chunk) -> list[BaseMessage]:
    context = f"Chapter: {chunk.chapter}\n" if chunk.chapter else ""
    page = chunk.page_spans[0].page if chunk.page_spans else "?"
    return [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"{context}Page {page}\n\n--- PAGE TEXT ---\n{chunk.text}\n--- END ---"),
    ]
