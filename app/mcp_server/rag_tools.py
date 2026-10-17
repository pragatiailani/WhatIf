"""`retrieve`: one-call local search over the graph and the text (README, Inference: Local).

Seed entities come from name matching fused with semantic similarity; the graph around them gives
relations and the communities they belong to; the events closest to the query and the chunks that
mention the seeds join keyword and semantic chunk search to pick the passages. Without embeddings
it still works on names, keywords and the graph.
"""

from typing import Any

from app import retrieval
from app.constants import (
    RETRIEVE_COMMUNITIES,
    RETRIEVE_COMMUNITY_CHARS,
    RETRIEVE_EVENTS,
    RETRIEVE_PASSAGE_CHARS,
    RETRIEVE_RELATIONS,
    RETRIEVE_SEEDS,
    TOOL_MAX_CHUNKS_PER_CALL,
)
from app.mcp_server.common import canon_scope, entity_out, short
from app.mcp_server.graph_tools import community_out
from app.mcp_server.matching import content_words, match_entities
from app.mcp_server.text_tools import chunk_ref, event_out, rank_chunks, snippet
from app.repo import chunks, communities, graph, mentions

MENTIONS_PER_SEED = 20


def _seeds(book_id: str, query: str, query_vec: Any) -> list[tuple[str, str]]:
    """(entity key, how it matched) for the entities the query is about, best first."""
    scope = canon_scope(book_id)
    by_name = [f["key"] for f in match_entities(query, graph.entity_index(scope), RETRIEVE_SEEDS)]
    by_meaning = [k for k, _ in retrieval.semantic(book_id, "entity", query_vec, RETRIEVE_SEEDS * 2)]
    named, meant = set(by_name), set(by_meaning)
    fused = [k for k, _ in retrieval.fuse(by_name, by_meaning)][:RETRIEVE_SEEDS]
    return [(k, "both" if k in named and k in meant else "name" if k in named else "meaning")
            for k in fused]  # fmt: skip


def _mention_ranking(book_id: str, seed_keys: list[str]) -> list[str]:
    """Chunk ids mentioning the seeds: chunks with more seeds first, then in reading order."""
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for key in seed_keys:
        for m in mentions.list_for_node(book_id, key, limit=MENTIONS_PER_SEED):
            counts[m.chunk_id] = counts.get(m.chunk_id, 0) + 1
            order.setdefault(m.chunk_id, m.chunk_seq)
    return sorted(counts, key=lambda c: (-counts[c], order[c]))


def retrieve(book_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    scope = canon_scope(book_id)
    limit = min(max(limit, 1), TOOL_MAX_CHUNKS_PER_CALL)
    query_vec = retrieval.embed_query(query)

    seeds = _seeds(book_id, query, query_vec)
    seed_keys = [k for k, _ in seeds]
    found = {e.key: e for e in graph.get_entities(scope, seed_keys)}
    names = {k: e.name for k, e in found.items()}

    relations, seen = [], set()
    for _, rel, other in graph.expand(scope, seed_keys) if seed_keys else []:
        names.setdefault(other.key, other.name)
        edge = (rel.src, rel.rel, rel.dst)
        if edge in seen:
            continue
        seen.add(edge)
        relations.append({
            "id": rel.id, "src": names.get(rel.src, rel.src), "rel": rel.rel,
            "dst": names.get(rel.dst, rel.dst), "src_key": rel.src, "dst_key": rel.dst,
            "description": short(rel.description),
        })  # fmt: skip
        if len(relations) >= RETRIEVE_RELATIONS:
            break

    hits = retrieval.semantic(book_id, "event", query_vec, RETRIEVE_EVENTS)
    events = [e for key, _ in hits if (e := graph.get_event(scope, key)) is not None]
    seq_ids = chunks.ids_by_seq(book_id, {int(e.story_pos // 1000) for e in events})
    event_chunks = [seq_ids[s] for e in events if (s := int(e.story_pos // 1000)) in seq_ids]

    ranked, _ = rank_chunks(
        book_id, query, query_vec=query_vec,
        extra=[_mention_ranking(book_id, seed_keys), event_chunks],
    )  # fmt: skip
    terms = list(dict.fromkeys(content_words(query)))
    return {
        "entities": [{**entity_out(found[k]), "match": how} for k, how in seeds if k in found],
        "relations": relations,
        "communities": [
            community_out(c, chars=RETRIEVE_COMMUNITY_CHARS)
            for c in communities.for_entities(book_id, seed_keys)[:RETRIEVE_COMMUNITIES]
        ] if seed_keys else [],
        "events": [event_out(e, seq_ids) for e in events],
        "passages": [
            {**chunk_ref(c), "match": how, "text": snippet(c.text, terms, RETRIEVE_PASSAGE_CHARS)}
            for c, _, how in ranked[:limit]
        ],
        "semantic": query_vec is not None,
    }
