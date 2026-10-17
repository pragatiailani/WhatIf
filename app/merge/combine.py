"""Merge two adjacent section graphs into one. Pure, no LLM or database.

`from_leaf` turns a level-0 page graph into the merged shape. `combine` takes a `Resolution`
(which right-side nodes are the same as which left-side nodes) and merges nodes and edges:

- entities: union aliases, pages, chunk_ids and sources; sum mention_count; min first_page,
  max last_page; keep up to `MERGE_MAX_DESCRIPTIONS` distinct per-page descriptions.
- events: union participants, pages, chunk_ids and sources; keep the earliest story_pos.
- relations: remap both endpoints through `find()`, then group by (src, rel, dst): sum weights,
  min pos_start, union pages and chunk_ids. Different rels between the same pair stay separate.

Left-side keys never change. A right-side entity that is not merged but whose key is already
taken on the left (two different "Ravi"s) gets a new key, `<key>@<first chunk seq>`.
"""

from copy import deepcopy
from dataclasses import dataclass, field

from app.constants import MERGE_MAX_DESCRIPTIONS
from app.extraction.schema import slug
from app.repo.models import GraphLevel, GraphPayload


@dataclass
class Resolution:
    entities: dict[str, str] = field(default_factory=dict)  # right key -> left key (same referent)
    events: dict[str, str] = field(default_factory=dict)  # right key -> left key
    names: dict[str, str] = field(default_factory=dict)  # left key -> canonical name


def from_leaf(level: GraphLevel) -> GraphPayload:
    """The merged shape of a level-0 page graph."""
    seq, chunk_ids = level.seq_start, level.chunk_ids
    entities = [
        {
            "key": e["key"],
            "name": e["name"],
            "type": e["type"],
            "aliases": list(e.get("aliases", [])),
            "descriptions": (
                [{"page": e["first_page"], "text": e["description"]}]
                if e.get("description")
                else []
            ),
            "pages": sorted({e["first_page"], e["last_page"]} - {None}),
            "first_page": e["first_page"],
            "last_page": e["last_page"],
            "mention_count": e.get("mention_count", 1),
            "chunk_ids": list(chunk_ids),
            "sources": [[seq, e["key"]]],  # (chunk seq, page-level key): which mentions are ours
        }
        for e in level.graph.entities
    ]
    events = [
        {
            **{k: e.get(k) for k in ("key", "summary", "story_pos", "story_time", "chapter")},
            "first_page": e.get("first_page"),
            "participants": list(e.get("participants", [])),
            "place": e.get("place"),
            "pages": [e["first_page"]] if e.get("first_page") is not None else [],
            "chunk_ids": list(chunk_ids),
            "sources": [[seq, e["key"]]],
        }
        for e in level.graph.events
    ]
    relations = [
        {
            "src": r["src"],
            "dst": r["dst"],
            "rel": r["rel"],
            "description": r.get("description", ""),
            "weight": r.get("weight", 1.0),
            "pos_start": r.get("story_pos"),
            "pages": [r["page"]] if r.get("page") is not None else [],
            "chunk_ids": list(chunk_ids),
        }
        for r in level.graph.relations
    ]
    return GraphPayload(entities=entities, events=events, relations=relations)


def combine(left: GraphPayload, right: GraphPayload, resolution: Resolution) -> GraphPayload:
    left, right = deepcopy(left), deepcopy(right)

    # --- entities ------------------------------------------------------------------------
    entities = {e["key"]: e for e in left.entities}
    right_keys: dict[str, str] = {}  # right key -> key in the merged graph
    for e in right.entities:
        target = resolution.entities.get(e["key"])
        if target in entities:
            _merge_entity(entities[target], e)
            right_keys[e["key"]] = target
        else:
            key = e["key"] if e["key"] not in entities else _free_key(e, entities)
            entities[key] = {**e, "key": key}
            right_keys[e["key"]] = key
    for key, name in resolution.names.items():
        if key in entities:
            _rename(entities[key], name)

    def find(key: str | None, from_right: bool) -> str | None:
        return right_keys.get(key, key) if from_right and key else key

    # --- events --------------------------------------------------------------------------
    events = {e["key"]: e for e in left.events}
    for e in right.events:
        e["participants"] = _dedupe_participants(
            {**p, "key": find(p["key"], True)} for p in e["participants"]
        )
        e["place"] = find(e["place"], True)
        target = resolution.events.get(e["key"])
        if target in events:
            _merge_event(events[target], e)
        else:
            events[e["key"]] = e

    # --- relations -----------------------------------------------------------------------
    edges: dict[tuple[str, str, str], dict] = {}
    for from_right, rels in ((False, left.relations), (True, right.relations)):
        for r in rels:
            src, dst = find(r["src"], from_right), find(r["dst"], from_right)
            if src == dst:
                continue  # both ends turned out to be the same entity
            edge = edges.get((src, r["rel"], dst))
            if edge is None:
                edges[(src, r["rel"], dst)] = {**r, "src": src, "dst": dst}
                continue
            edge["weight"] += r["weight"]
            edge["pos_start"] = _min(edge["pos_start"], r["pos_start"])
            edge["pages"] = sorted(set(edge["pages"]) | set(r["pages"]))
            edge["chunk_ids"] = _union(edge["chunk_ids"], r["chunk_ids"])
            edge["description"] = edge["description"] or r["description"]

    relations = sorted(edges.values(), key=lambda r: (r["pos_start"] is None, r["pos_start"] or 0))
    return GraphPayload(
        entities=list(entities.values()),
        events=sorted(events.values(), key=lambda e: e["story_pos"]),
        relations=relations,
    )


# --- helpers ---------------------------------------------------------------------------------


def _min(a, b):
    return b if a is None else a if b is None else min(a, b)


def _max(a, b):
    return b if a is None else a if b is None else max(a, b)


def _union(a: list, b: list) -> list:
    return list(dict.fromkeys([*a, *b]))


def _merge_entity(into: dict, other: dict) -> None:
    known = {slug(n) for n in (into["name"], *into["aliases"])}
    for name in (other["name"], *other["aliases"]):
        if slug(name) and slug(name) not in known:
            into["aliases"].append(name)
            known.add(slug(name))
    into["pages"] = sorted(set(into["pages"]) | set(other["pages"]))
    into["chunk_ids"] = _union(into["chunk_ids"], other["chunk_ids"])
    into["sources"] = into["sources"] + other["sources"]
    into["mention_count"] += other["mention_count"]
    into["first_page"] = _min(into["first_page"], other["first_page"])
    into["last_page"] = _max(into["last_page"], other["last_page"])
    into["descriptions"] = cap_descriptions(into["descriptions"] + other["descriptions"])


def cap_descriptions(notes: list[dict], limit: int = MERGE_MAX_DESCRIPTIONS) -> list[dict]:
    """Distinct descriptions in page order; past `limit`, keep first, last and evenly spaced ones
    so later parts of the story stay represented."""
    seen: set[str] = set()
    distinct = []
    for note in sorted(notes, key=lambda n: (n["page"] is None, n["page"] or 0)):
        text = " ".join(note["text"].split()).lower()
        if text and text not in seen:
            seen.add(text)
            distinct.append(note)
    if len(distinct) <= limit:
        return distinct
    step = (len(distinct) - 1) / (limit - 1)
    return [distinct[round(i * step)] for i in range(limit)]


def _rename(entity: dict, name: str) -> None:
    if slug(name) == slug(entity["name"]):
        return
    old = entity["name"]
    entity["name"] = name
    entity["aliases"] = [a for a in [old, *entity["aliases"]] if slug(a) != slug(name)]


def _free_key(entity: dict, taken: dict) -> str:
    base = entity["key"].split("@")[0]
    first_seq = min(seq for seq, _ in entity["sources"])
    key, n = f"{base}@{first_seq}", 2
    while key in taken:
        key, n = f"{base}@{first_seq}.{n}", n + 1
    return key


def _dedupe_participants(participants) -> list[dict]:
    by_key: dict[str, dict] = {}
    for p in participants:
        by_key.setdefault(p["key"], p)
    return list(by_key.values())


def _merge_event(into: dict, other: dict) -> None:
    into["participants"] = _dedupe_participants([*into["participants"], *other["participants"]])
    into["place"] = into["place"] or other["place"]
    into["story_time"] = into["story_time"] or other["story_time"]
    into["story_pos"] = _min(into["story_pos"], other["story_pos"])
    into["first_page"] = _min(into["first_page"], other["first_page"])
    into["pages"] = sorted(set(into["pages"]) | set(other["pages"]))
    into["chunk_ids"] = _union(into["chunk_ids"], other["chunk_ids"])
    into["sources"] = into["sources"] + other["sources"]
