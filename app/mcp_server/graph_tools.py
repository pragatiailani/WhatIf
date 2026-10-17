"""Graph tools: entity lookup, neighbours, paths, typed relations, communities."""

from typing import Any

from app.constants import (
    FALLBACK_REL,
    REL_VOCABULARY,
    TOOL_BFS_BATCH,
    TOOL_MAX_FRONTIER,
    TOOL_MAX_HOPS,
    TOOL_MAX_NEIGHBORS,
    TOOL_MAX_PATH_HOPS,
    TOOL_MAX_RESULTS,
)
from app.mcp_server.common import branch_scope, entity_out, relation_out, resolve, short
from app.mcp_server.matching import content_words, match_entities
from app.repo import communities, graph
from app.repo.models import GraphEntity, GraphRelation


def find_entities(
    book_id: str, text: str, limit: int = 10, session_id: str | None = None
) -> dict[str, Any]:
    scope = branch_scope(book_id, session_id)
    found = match_entities(text, graph.entity_index(scope), min(max(limit, 1), TOOL_MAX_RESULTS))
    by_key = {e.key: e for e in graph.get_entities(scope, [f["key"] for f in found])}
    return {
        "entities": [
            {**entity_out(by_key[f["key"]]), "score": f["score"], "matched": f["matched"]}
            for f in found
            if f["key"] in by_key
        ]
    }


def get_neighbors(
    book_id: str, entity: str, hops: int = 1, limit: int = 25, session_id: str | None = None
) -> dict[str, Any]:
    if not 1 <= hops <= TOOL_MAX_HOPS:
        raise ValueError(f"hops must be between 1 and {TOOL_MAX_HOPS}")
    scope = branch_scope(book_id, session_id)
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


def find_path(book_id: str, a: str, b: str, session_id: str | None = None) -> dict[str, Any]:
    """Breadth-first search over REL edges, ignoring direction, up to TOOL_MAX_PATH_HOPS."""
    scope = branch_scope(book_id, session_id)
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
    book_id: str,
    entity: str,
    rel_type: str | None = None,
    limit: int = 25,
    session_id: str | None = None,
) -> dict[str, Any]:
    scope = branch_scope(book_id, session_id)
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


def search_communities(book_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    branch_scope(book_id)
    everything = communities.list_communities(book_id)
    if not everything:
        return {"communities": [], "note": "no communities have been built for this book yet"}
    terms = content_words(query)
    scored = []
    for c in everything:
        title, summary = set(content_words(c.title)), set(content_words(c.summary))
        score = sum(3 * (t in title) + (t in summary) for t in terms)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].cid))
    return {
        "communities": [
            {
                "cid": c.cid,
                "level": c.level,
                "title": c.title,
                "summary": short(c.summary, 1500),
                "entity_count": len(c.entity_keys),
                "score": score,
            }
            for score, c in scored[: min(max(limit, 1), TOOL_MAX_RESULTS)]
        ]
    }
