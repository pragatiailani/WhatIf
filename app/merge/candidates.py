"""Find right-side nodes that may be the same as left-side nodes. Pure, no LLM or database.

Clear cases are decided here:
- different types are never the same;
- an identical name, or a full name that is one side's name and the other's alias, on exactly
  one node per side is the same, unless the description embeddings clearly disagree;
- nodes with nothing in common are different.
Everything in between becomes a candidate pair for the LLM judge.

Events are compared only near the section boundary (`MERGE_EVENT_MAX_SEQ_GAP` chunks), since a
duplicate event is one moment split across a page break, and only if they share a participant.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

from app.constants import (
    MERGE_EMBED_SIM_CANDIDATE,
    MERGE_EVENT_MAX_SEQ_GAP,
    MERGE_EVENT_SUMMARY_SIM,
    MERGE_MAX_CANDIDATES_PER_ENTITY,
    MERGE_MIN_EMBED_FOR_AUTO,
    MERGE_NAME_SIM_CANDIDATE,
)
from app.extraction.schema import slug
from app.repo.models import GraphPayload

_SUBSET_SIM = 0.85  # "ravi" vs "ravi_sharma": likely, but the judge must check
_STOPWORDS = frozenset({"the", "a", "an", "of"})

Vectors = dict[str, np.ndarray]  # entity key -> unit-length embedding


@dataclass
class EntityCandidate:
    left: dict
    right: dict
    name_sim: float
    embed_sim: float | None


@dataclass
class EventCandidate:
    left: dict
    right: dict


@dataclass
class Candidates:
    entities: dict[str, str] = field(default_factory=dict)  # decided same in code: right -> left
    events: dict[str, str] = field(default_factory=dict)
    ambiguous_entities: list[EntityCandidate] = field(default_factory=list)
    ambiguous_events: list[EventCandidate] = field(default_factory=list)


def names(entity: dict) -> list[str]:
    """Slugs of the name and aliases, name first."""
    return list(dict.fromkeys(s for s in map(slug, (entity["name"], *entity["aliases"])) if s))


def _tokens(name_slug: str) -> set[str]:
    return set(name_slug.split("_")) - _STOPWORDS


def _pair_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and (ta <= tb or tb <= ta):
        return _SUBSET_SIM
    return SequenceMatcher(None, a, b).ratio()


def name_sim(left: dict, right: dict) -> float:
    return max((_pair_sim(a, b) for a in names(left) for b in names(right)), default=0.0)


def _blocks(entity: dict) -> set[tuple[str, str]]:
    """Blocking keys: entities that share none are never compared by name."""
    return {(entity["type"], t[:3]) for n in names(entity) for t in _tokens(n)}


def _embed_matrix(left: list[dict], right: list[dict], lv: Vectors, rv: Vectors) -> np.ndarray:
    lm = np.stack([lv[e["key"]] for e in left])
    rm = np.stack([rv[e["key"]] for e in right])
    return rm @ lm.T  # [right, left] cosine similarity


def find_candidates(
    left: GraphPayload,
    right: GraphPayload,
    left_vectors: Vectors | None = None,
    right_vectors: Vectors | None = None,
) -> Candidates:
    out = Candidates()
    _entity_candidates(left.entities, right.entities, left_vectors, right_vectors, out)
    _event_candidates(left.events, right.events, out)
    return out


def _entity_candidates(
    left: list[dict], right: list[dict], lv: Vectors | None, rv: Vectors | None, out: Candidates
) -> None:
    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, e in enumerate(left):
        for block in _blocks(e):
            index[block].append(i)

    # Embedding similarity per type: catches "the Inspector" vs "Ravi Sharma".
    row: dict[int, int] = {}  # right index -> row in its type's matrix
    col: dict[int, int] = {}  # left index -> column in its type's matrix
    matrices: dict[str, np.ndarray] = {}
    embed_hits: dict[int, list[int]] = defaultdict(list)
    if lv is not None and rv is not None:
        for type_ in {e["type"] for e in right}:
            li = [i for i, e in enumerate(left) if e["type"] == type_ and e["key"] in lv]
            ri = [j for j, e in enumerate(right) if e["type"] == type_ and e["key"] in rv]
            if not (li and ri):
                continue
            matrices[type_] = _embed_matrix([left[i] for i in li], [right[j] for j in ri], lv, rv)
            row.update({j: a for a, j in enumerate(ri)})
            col.update({i: b for b, i in enumerate(li)})
            for a, b in zip(*np.nonzero(matrices[type_] >= MERGE_EMBED_SIM_CANDIDATE), strict=True):
                embed_hits[ri[a]].append(li[b])

    def esim(j: int, i: int) -> float | None:
        if j not in row or i not in col:
            return None
        return float(matrices[right[j]["type"]][row[j], col[i]])

    auto: dict[int, list[int]] = defaultdict(list)  # right index -> left indexes
    auto_left: dict[int, list[int]] = defaultdict(list)
    ambiguous: dict[int, list[EntityCandidate]] = defaultdict(list)
    for j, r in enumerate(right):
        pool = {i for block in _blocks(r) for i in index.get(block, ())} | set(embed_hits[j])
        for i in sorted(pool):
            entry = left[i]
            if entry["type"] != r["type"]:
                continue
            ns, es = name_sim(entry, r), esim(j, i)
            shared = set(names(entry)) & set(names(r))
            disagree = es is not None and es < MERGE_MIN_EMBED_FOR_AUTO
            same_name = slug(entry["name"]) == slug(r["name"])
            full_name = any(  # "Ravi Sharma" as one side's name and the other's alias
                len(_tokens(n)) >= 2 and n in (slug(entry["name"]), slug(r["name"])) for n in shared
            )
            if not disagree and (same_name or full_name):
                auto[j].append(i)
                auto_left[i].append(j)
            elif shared or ns >= MERGE_NAME_SIM_CANDIDATE or (
                es is not None and es >= MERGE_EMBED_SIM_CANDIDATE
            ):
                ambiguous[j].append(EntityCandidate(entry, r, ns, es))

    # Code decides only one-to-one matches; two possible partners go to the judge instead.
    matched_left: set[int] = set()
    for j, lefts in auto.items():
        if len(lefts) == 1 and len(auto_left[lefts[0]]) == 1:
            out.entities[right[j]["key"]] = left[lefts[0]]["key"]
            matched_left.add(lefts[0])
        else:
            ambiguous[j] += [
                EntityCandidate(left[i], right[j], name_sim(left[i], right[j]), esim(j, i))
                for i in lefts
            ]
    matched_keys = {left[i]["key"] for i in matched_left}
    for j in sorted(ambiguous):
        if right[j]["key"] in out.entities:
            continue
        pairs = [c for c in ambiguous[j] if c.left["key"] not in matched_keys]
        pairs.sort(key=lambda c: c.name_sim + (c.embed_sim or 0.0), reverse=True)
        out.ambiguous_entities += pairs[:MERGE_MAX_CANDIDATES_PER_ENTITY]


def chunk_seqs(node: dict) -> list[int]:
    return [seq for seq, _ in node["sources"]]


def _event_candidates(left: list[dict], right: list[dict], out: Candidates) -> None:
    if not left or not right:
        return
    # Right participant key -> left keys it may be (decided or still ambiguous).
    maybe: dict[str, set[str]] = defaultdict(set)
    for r, entry in out.entities.items():
        maybe[r].add(entry)
    for c in out.ambiguous_entities:
        maybe[c.right["key"]].add(c.left["key"])

    boundary = min(min(chunk_seqs(e)) for e in right)
    recent = [e for e in left if boundary - max(chunk_seqs(e)) <= MERGE_EVENT_MAX_SEQ_GAP]
    auto: dict[str, list[str]] = defaultdict(list)
    auto_left: dict[str, list[str]] = defaultdict(list)
    for r in right:
        if min(chunk_seqs(r)) - boundary > MERGE_EVENT_MAX_SEQ_GAP:
            continue
        r_people = {k for p in r["participants"] for k in maybe.get(p["key"], {p["key"]})}
        for entry in recent:
            if not r_people & {p["key"] for p in entry["participants"]}:
                continue
            if slug(entry["summary"]) == slug(r["summary"]):
                auto[r["key"]].append(entry["key"])
                auto_left[entry["key"]].append(r["key"])
            elif _summary_sim(entry, r) >= MERGE_EVENT_SUMMARY_SIM:
                out.ambiguous_events.append(EventCandidate(entry, r))
    by_key = {e["key"]: e for e in left}
    right_by_key = {e["key"]: e for e in right}
    for r, lefts in auto.items():
        if len(lefts) == 1 and len(auto_left[lefts[0]]) == 1:
            out.events[r] = lefts[0]
        else:
            out.ambiguous_events += [EventCandidate(by_key[k], right_by_key[r]) for k in lefts]
    out.ambiguous_events = [
        c
        for c in out.ambiguous_events
        if c.right["key"] not in out.events and c.left["key"] not in out.events.values()
    ]


def _summary_sim(a: dict, b: dict) -> float:
    return SequenceMatcher(None, a["summary"].lower(), b["summary"].lower()).ratio()
