"""Graph tools: entity lookup, neighbours, paths, typed relations, communities."""

from typing import Any

from app import retrieval
from app.constants import (
    FALLBACK_REL,
    REL_VOCABULARY,
    SEMANTIC_ENTITY_MIN_SCORE,
    TOOL_BFS_BATCH,
    TOOL_MAX_FRONTIER,
    TOOL_MAX_HOPS,
    TOOL_MAX_NEIGHBORS,
    TOOL_MAX_PATH_HOPS,
    TOOL_MAX_RESULTS,
)
from app.mcp_server.common import canon_scope, entity_out, relation_out, resolve, short
from app.mcp_server.matching import content_words, match_entities
from app.repo import communities, graph
from app.repo.models import Community, GraphEntity, GraphRelation


def find_entities(book_id: str, text: str, limit: int = 10) -> dict[str, Any]:
    """Entities named in `text`; when no name matches, the entities closest in meaning."""
    scope = canon_scope(book_id)
    limit = min(max(limit, 1), TOOL_MAX_RESULTS)
    found = [
        {**f, "match": "name"} for f in match_entities(text, graph.entity_index(scope), limit)
    ]
    if not found:
        hits = retrieval.semantic(book_id, "entity", retrieval.embed_query(text), limit)
        found = [
            {"key": key, "score": round(sim, 3), "matched": None, "match": "meaning"}
            for key, sim in hits
            if sim >= SEMANTIC_ENTITY_MIN_SCORE
        ]
    by_key = {e.key: e for e in graph.get_entities(scope, [f["key"] for f in found])}
    return {
        "entities": [
            {**entity_out(by_key[f["key"]]), "score": f["score"], "matched": f["matched"],
             "match": f["match"]}
            for f in found
            if f["key"] in by_key
        ]
    }  # fmt: skip


def get_neighbors(book_id: str, entity: str, hops: int = 1, limit: int = 25) -> dict[str, Any]:
    if not 1 <= hops <= TOOL_MAX_HOPS:
        raise ValueError(f"hops must be between 1 and {TOOL_MAX_HOPS}")
    scope = canon_scope(book_id)
    start = resolve(scope, entity)
    limit = min(max(limit, 1), TOOL_MAX_NEIGHBORS)

    seen = {start["key"]}
    frontier = [start["key"]]
    neighbors: list[dict[str, Any]] = []
    for hop in range(1, hops + 1):
        next_frontier: list[str] = []
        for origin, rel, other in graph.expand(scope, frontier):
            if other.key in seen:
                continue
            seen.add(other.key)
            next_frontier.append(other.key)
            neighbors.append(
                {"hop": hop, "via": origin, "entity": entity_out(other), **relation_out(rel, origin)}
            )
        frontier = next_frontier
    center = graph.get_entity(scope, start["key"])
    return {
        "entity": entity_out(center) if center else start,
        "neighbors": neighbors[:limit],
        "total": len(neighbors),
    }


def find_path(book_id: str, a: str, b: str) -> dict[str, Any]:
    """Breadth-first search over REL edges, ignoring direction, up to TOOL_MAX_PATH_HOPS."""
    scope = canon_scope(book_id)
    src, dst = resolve(scope, a)["key"], resolve(scope, b)["key"]
    if src == dst:
        raise ValueError("both names resolve to the same entity")

    came_from: dict[str, tuple[str, GraphRelation]] = {}
    entities: dict[str, GraphEntity] = {}
    frontier = [src]
    visited = {src}
    for _ in range(TOOL_MAX_PATH_HOPS):
        frontier = frontier[:TOOL_MAX_FRONTIER]
        next_frontier: list[str] = []
        for i in range(0, len(frontier), TOOL_BFS_BATCH):
            for origin, rel, other in graph.expand(scope, frontier[i : i + TOOL_BFS_BATCH]):
                if other.key in visited:
                    continue
                visited.add(other.key)
                came_from[other.key] = (origin, rel)
                entities[other.key] = other
                next_frontier.append(other.key)
        if dst in visited or not next_frontier:
            break
        frontier = next_frontier
    if dst not in came_from:
        return {"found": False, "max_hops": TOOL_MAX_PATH_HOPS, "path": []}

    steps, node = [], dst
    while node != src:
        origin, rel = came_from[node]
        steps.append((origin, rel, node))
        node = origin
    steps.reverse()
    start = graph.get_entity(scope, src)
    names = {src: start.name if start else src, **{k: e.name for k, e in entities.items()}}
    return {
        "found": True,
        "hops": len(steps),
        "path": [
            {"from": names[o], "to": names[t], **relation_out(rel, o)} for o, rel, t in steps
        ],
    }


def _normalize_rel(rel_type: str) -> str:
    rel = "_".join(rel_type.strip().upper().replace("-", " ").split())
    if rel not in REL_VOCABULARY and rel != FALLBACK_REL:
        valid = ", ".join(sorted(REL_VOCABULARY | {FALLBACK_REL}))
        raise ValueError(f"unknown relation type '{rel_type}'. Valid types: {valid}")
    return rel


def get_relations(
    book_id: str, entity: str, rel_type: str | None = None, limit: int = 25
) -> dict[str, Any]:
    scope = canon_scope(book_id)
    start = resolve(scope, entity)
    rel = _normalize_rel(rel_type) if rel_type else None
    rows = graph.entity_relations(
        scope,
        start["key"],
        limit=min(max(limit, 1), TOOL_MAX_NEIGHBORS),
        rel_types=[rel] if rel else None,
    )
    return {
        "entity": {"key": start["key"], "name": start["name"]},
        "rel_type": rel,
        "relations": [
            {**relation_out(r, start["key"]), "other": entity_out(other)} for r, other in rows
        ],
    }


def community_out(c: Community, score: float | None = None, chars: int = 1500) -> dict[str, Any]:
    out = {"cid": c.cid, "level": c.level, "title": c.title, "summary": short(c.summary, chars),
           "entity_count": len(c.entity_keys)}  # fmt: skip
    return out if score is None else {**out, "score": round(score, 4)}


def search_communities(book_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    """Communities by keyword and meaning. When neither finds anything, the book overview and the
    largest groups, so a big-picture question always gets the big picture."""
    canon_scope(book_id)
    everything = communities.list_communities(book_id)
    if not everything:
        return {"communities": [], "note": "no communities have been built for this book yet"}
    limit = min(max(limit, 1), TOOL_MAX_RESULTS)
    by_cid = {c.cid: c for c in everything}
    terms = content_words(query)
    scored = []
    for c in everything:
        title, summary = set(content_words(c.title)), set(content_words(c.summary))
        score = sum(3 * (t in title) + (t in summary) for t in terms)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].cid))
    hits = retrieval.semantic(book_id, "community", retrieval.embed_query(query), limit)
    meaning = [int(ref) for ref, _ in hits if int(ref) in by_cid]
    if not meaning:
        if scored:
            return {"communities": [community_out(c, s) for s, c in scored[:limit]]}
        overview = [c for c in everything if c.level > 0]
        groups = sorted((c for c in everything if c.level == 0), key=lambda c: c.cid)
        return {
            "communities": [community_out(c) for c in [*overview, *groups][:limit]],
            "note": "nothing matched the query; showing the book overview and its largest groups",
        }
    fused = retrieval.fuse([str(c.cid) for _, c in scored], [str(cid) for cid in meaning])
    return {"communities": [community_out(by_cid[int(ref)], s) for ref, s in fused[:limit]]}
