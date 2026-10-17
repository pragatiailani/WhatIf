"""Prompts for the merge judge (one call per merge with ambiguous pairs) and final descriptions."""

from collections import defaultdict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.merge.candidates import EntityCandidate, EventCandidate
from app.repo.models import Book, GraphLevel, GraphPayload

MERGE_SYSTEM_PROMPT = """You resolve duplicates while merging two knowledge graphs extracted from consecutive
sections of the same book. For each candidate pair, decide whether LEFT and RIGHT refer
to the same thing in the story.

Rules:
- "same" means the same story referent: one person, place, object, group or event.
- Different characters can share a first name or surname. Never decide on name alone;
  use type, description, relations and pages.
- Nicknames, titles and epithets ("the Inspector", "Ravi bhai", "the old man") can be the
  same referent if the descriptions and relations agree.
- Different types are never the same.
- Events are "same" only if they describe the same moment, with the same participants,
  not two similar happenings.
- If unsure, answer "different". A missed duplicate is compared again later; a wrong
  merge cannot be undone.
- Use only the information given. Return JSON only."""

_RETURN_FORMAT = """Return:
{
  "entities": [
    {"id": "E1", "decision": "same|different", "canonical_name": "<best full name>",
     "confidence": 0.0-1.0, "reason": "<max 15 words>"}
  ],
  "events": [
    {"id": "V1", "decision": "same|different", "confidence": 0.0-1.0}
  ]
}"""

DESCRIPTION_SYSTEM_PROMPT = """For each entity below, write a 2-3 sentence description using only the notes given.
Keep what changes over the story in order (e.g. "a clerk in Delhi who later starts a
business in Mumbai"). Do not invent facts. Return JSON: {"descriptions": [{"key": ..., "description": ...}]}"""

_TOP_RELATIONS = 3


def _pages(pages: list[int]) -> str:
    """[3, 4, 5, 9] -> '3-5, 9'."""
    runs: list[list[int]] = []
    for p in sorted(set(pages)):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs) or "?"


def _book_line(book: Book | None) -> str:
    if book is None:
        return "Book: unknown"
    return f"Book: {book.title} by {book.author}" if book.author else f"Book: {book.title}"


class _Side:
    """Lookups over one side's graph for rendering candidates."""

    def __init__(self, graph: GraphPayload):
        self.names = {e["key"]: e["name"] for e in graph.entities}
        rels: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for r in graph.relations:
            src, dst = self.names.get(r["src"], r["src"]), self.names.get(r["dst"], r["dst"])
            rels[r["src"]].append((r["weight"], f"{r['rel']} → {dst}"))
            rels[r["dst"]].append((r["weight"], f"{r['rel']} ← {src}"))
        self.relations = {
            k: [text for _, text in sorted(v, key=lambda x: -x[0])[:_TOP_RELATIONS]]
            for k, v in rels.items()
        }

    def entity(self, label: str, e: dict) -> str:
        notes = "; ".join(f"p.{n['page']}: {n['text']}" for n in e["descriptions"]) or "-"
        return (
            f"{label} name: {e['name']} | type: {e['type']} | aliases: "
            f"{', '.join(e['aliases']) or '-'}\n"
            f"      description: {notes}\n"
            f"      relations: {', '.join(self.relations.get(e['key'], [])) or '-'} | "
            f"pages: {_pages(e['pages'])}"
        )

    def event(self, label: str, e: dict) -> str:
        people = ", ".join(self.names.get(p["key"], p["key"]) for p in e["participants"]) or "-"
        return f"{label} summary: {e['summary']} | participants: {people} | page: {e['first_page']}"


def build_merge_messages(
    book: Book | None,
    left: GraphLevel,
    right: GraphLevel,
    left_graph: GraphPayload,
    right_graph: GraphPayload,
    entities: list[tuple[str, EntityCandidate]],
    events: list[tuple[str, EventCandidate]],
) -> list[BaseMessage]:
    """`entities` / `events` are (id, candidate) pairs, ids like E1 / V1."""
    ls, rs = _Side(left_graph), _Side(right_graph)
    lines = [
        _book_line(book),
        f"LEFT section: pages {left.page_start}-{left.page_end}    "
        f"RIGHT section: pages {right.page_start}-{right.page_end}",
    ]
    if entities:
        lines += ["", "ENTITY CANDIDATES"]
        for id_, c in entities:
            embed = f"{c.embed_sim:.2f}" if c.embed_sim is not None else "n/a"
            lines += [
                f"[{id_}]",
                ls.entity("LEFT ", c.left),
                rs.entity("RIGHT", c.right),
                f"signals: name_sim={c.name_sim:.2f}, embed_sim={embed}",
            ]
    if events:
        lines += ["", "EVENT CANDIDATES"]
        for id_, c in events:
            lines += [f"[{id_}]", ls.event("LEFT ", c.left), rs.event("RIGHT", c.right)]
    lines += ["", _RETURN_FORMAT]
    return [SystemMessage(MERGE_SYSTEM_PROMPT), HumanMessage("\n".join(lines))]


def build_description_messages(entities: list[dict]) -> list[BaseMessage]:
    blocks = []
    for e in entities:
        notes = "\n".join(f"- p.{n['page']}: {n['text']}" for n in e["descriptions"])
        blocks.append(f"{e['key']} | {e['name']} | {e['type']}\n{notes}")
    return [SystemMessage(DESCRIPTION_SYSTEM_PROMPT), HumanMessage("\n\n".join(blocks))]
