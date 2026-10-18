"""The branch ledger: what is true of each entity in a storyline, from facts set by its ops."""

from typing import Any

from app.repo import graph, storylines
from app.repo.graph import GraphScope
from app.repo.models import Storyline, StorylineOp

RECENT_EVENTS = 3
MAX_STATE_RELATIONS = 15


def fact_ops(sl: Storyline) -> list[StorylineOp]:
    """The storyline's applied set_fact ops, in the order they take effect."""
    ops = [o for o in storylines.list_ops(sl.id, upto_seq=sl.head_seq) if o.kind == "set_fact"]
    return sorted(ops, key=lambda o: (o.payload["from_pos"], o.seq))


def facts_at(sl: Storyline, pos: float = float("inf")) -> dict[str, dict[str, str]]:
    """{entity key: {attribute: value}} as of story position `pos`; later facts win."""
    facts: dict[str, dict[str, str]] = {}
    for op in fact_ops(sl):
        if op.payload["from_pos"] <= pos:
            facts.setdefault(op.payload["entity"], {})[op.payload["attribute"]] = op.payload["value"]
    return facts


def state_at(
    scope: GraphScope, sl: Storyline | None, keys: list[str], pos: float
) -> list[dict[str, Any]]:
    """What the graph and the branch facts say about each entity at story position `pos`."""
    facts = facts_at(sl, pos) if sl else {}
    out = []
    for entity in graph.get_entities(scope, keys):
        events = [(e, role) for e, role in graph.entity_events(scope, entity.key) if e.story_pos <= pos]
        relations = [
            (r, other)
            for r, other in graph.entity_relations(scope, entity.key, limit=200)
            if r.story_pos is None or r.story_pos <= pos
        ]
        hints = [
            f"{other.name} {r.rel} this entity (pos {r.story_pos})"
            for r, other in relations
            if r.rel == "KILLS" and r.dst == entity.key
        ]
        out.append(
            {
                "entity": {"key": entity.key, "name": entity.name, "type": entity.type},
                "branch_facts": facts.get(entity.key, {}),
                "canon_hints": hints,
                "recent_events": [
                    {"key": e.key, "summary": e.summary, "story_pos": e.story_pos, "role": role}
                    for e, role in events[-RECENT_EVENTS:]
                ],
                "relations": [
                    {"rel": r.rel, "other": other.name, "direction": "out" if r.src == entity.key else "in",
                     "story_pos": r.story_pos}
                    for r, other in relations[:MAX_STATE_RELATIONS]
                ],
            }
        )
    return out
