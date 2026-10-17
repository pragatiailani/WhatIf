"""Why an answer holds: the chain of graph facts behind it.

Once the agent has answered, one more model call picks, from the relations and events its tools
returned this turn, the ones the answer rests on, ordered so each leads to the next (A -REL-> B,
B -REL-> C, an event involving C, ...), with a sentence on why each matters. Links are checked
against those facts, so a chain only ever shows what is in the graph. No facts, or a failed call,
means no chain; the answer stands either way.

A link is a relation {kind: "relation", id, src_key, dst_key, src, dst, rel, description, why} or
an event {kind: "event", key, summary, chapter, page, involves (keys), names, why}.
"""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.book_qa.prompt import EXPLAIN_PROMPT
from app.constants import REASONING_MAX_FACTS
from app.extraction.runner import is_blocked
from app.repo import GraphScope, graph

log = logging.getLogger(__name__)
# Tool result fields that hold relations (retrieve, get_relations; get_neighbors; find_path).
RELATION_LISTS = ("relations", "neighbors", "path")
RELATION_FIELDS = ("id", "src_key", "dst_key", "rel", "description")
EVENT_FIELDS = ("key", "summary", "chapter", "page")


class Link(BaseModel):
    fact: int = Field(description="The fact's number in the list")
    why: str = Field(description="One sentence: what this fact shows and how it leads on")


class Reasoning(BaseModel):
    chain: list[Link] = Field(description="The supporting facts, ordered so each leads to the next")
    conclusion: str = Field(description="One or two sentences on how the chain gives the answer")


def facts_from(results: list[dict]) -> list[dict[str, Any]]:
    """The relations and events in this turn's tool results, once each, relations first."""
    relations: dict[str, dict[str, Any]] = {}
    events: dict[str, dict[str, Any]] = {}
    for data in results:
        for r in (r for field in RELATION_LISTS for r in data.get(field) or []):
            if isinstance(r, dict) and all(r.get(k) for k in RELATION_FIELDS[:4]):
                relations.setdefault(
                    r["id"], {"kind": "relation", **{k: r.get(k) or "" for k in RELATION_FIELDS}}
                )
        for e in data.get("events") or []:
            if isinstance(e, dict) and e.get("key") and e.get("summary"):
                events.setdefault(
                    e["key"], {"kind": "event", **{k: e.get(k) for k in EVENT_FIELDS}}
                )
    return [*relations.values(), *events.values()][:REASONING_MAX_FACTS]


def _named(book_id: str, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Facts with entity names: src/dst for relations, involves/names for events."""
    scope = GraphScope.canon(book_id)
    involves = graph.event_participants(scope, [f["key"] for f in facts if f["kind"] == "event"])
    keys = {k for f in facts for k in (f.get("src_key"), f.get("dst_key")) if k}
    keys |= {k for found in involves.values() for k in found}
    names = {e.key: e.name for e in graph.get_entities(scope, sorted(keys))}
    out = []
    for f in facts:
        if f["kind"] == "relation":
            f = {
                **f,
                "src": names.get(f["src_key"], f["src_key"]),
                "dst": names.get(f["dst_key"], f["dst_key"]),
            }
        else:
            keys_in = involves.get(f["key"], [])
            f = {**f, "involves": keys_in, "names": [names.get(k, k) for k in keys_in]}
        out.append(f)
    return out


def _line(i: int, f: dict[str, Any]) -> str:
    if f["kind"] == "relation":
        note = f": {f['description']}" if f["description"] else ""
        return f"[{i}] {f['src']} -{f['rel']}-> {f['dst']}{note}"
    where = ", ".join(str(p) for p in (f.get("chapter"), f.get("page") and f"p. {f['page']}") if p)
    who = f" involving {', '.join(f['names'])}" if f["names"] else ""
    return f"[{i}] Event{f' ({where})' if where else ''}{who}: {f['summary']}"


async def explain(
    llm: BaseChatModel, book_id: str, question: str, answer: str, results: list[dict]
) -> dict[str, Any] | None:
    """{chain: [link], conclusion} for the answer, or None without one (see module doc)."""
    facts = facts_from(results)
    if not facts or not answer:
        return None
    facts = _named(book_id, facts)
    listing = "\n".join(_line(i, f) for i, f in enumerate(facts, 1))
    messages = [
        SystemMessage(content=EXPLAIN_PROMPT),
        HumanMessage(content=f"Question: {question}\n\nAnswer: {answer}\n\nFacts:\n{listing}"),
    ]
    try:
        out = await llm.with_structured_output(Reasoning, method="function_calling").ainvoke(
            messages
        )
    except Exception as exc:
        if not is_blocked(exc):
            log.warning("explaining an answer on book %s failed", book_id, exc_info=True)
        return None

    chain, used = [], set()
    for link in out.chain:
        if 1 <= link.fact <= len(facts) and link.fact not in used:
            used.add(link.fact)
            chain.append({**facts[link.fact - 1], "why": link.why.strip()})
    return {"chain": chain, "conclusion": out.conclusion.strip()} if chain else None
