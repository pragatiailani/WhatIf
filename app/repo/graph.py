"""FalkorDB access. Every read goes through `visible()`, the sid/hidden-set/page filter hook.

Only canon scopes are supported for now; storyline scopes (sid + hidden sets) plug into
`visible()` once the storyline repo exists.

A scope can also carry `max_page`, the reader's position: everything first seen after that page
(nodes, edges, events, passages) is hidden, and entity descriptions are rebuilt from the notes
written up to that page. This is the spoiler guard.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from falkordb import Graph
from redis.exceptions import ResponseError

from app.constants import (
    DEFAULT_RELATION_LIMIT,
    ENTITY_INDEX_TTL_SECONDS,
    ENTITY_TYPES,
    FALKOR_INDEX_EXISTS_ERROR,
    FALKOR_MISSING_GRAPH_ERROR,
    GRAPH_NAME_PREFIX,
    KEY_INDEXED_LABELS,
    OMITTED_GRAPH_PROPS,
    PROPERTY_INDEXES,
    VECTOR_INDEXED_LABELS,
    VECTOR_SIMILARITY,
    VECTOR_WRITE_BATCH,
)
from app.db.falkor import select_graph
from app.repo.models import Community, GraphEntity, GraphEvent, GraphRelation

log = logging.getLogger(__name__)

WRITE_BATCH = 1000  # rows per write query for plain (vector-free) data


@dataclass(frozen=True)
class GraphScope:
    book_id: str
    sid: str | None = None  # None = canon
    max_page: int | None = None  # None = the whole book; else hide everything after this page

    @classmethod
    def canon(cls, book_id: str, max_page: int | None = None) -> "GraphScope":
        return cls(book_id=book_id, max_page=max_page)


def graph_name(book_id: str) -> str:
    return f"{GRAPH_NAME_PREFIX}{book_id}"


def _graph(book_id: str) -> Graph:
    return select_graph(graph_name(book_id))


def visible(scope: GraphScope, *aliases: str) -> str:
    """Cypher predicate restricting each alias (node or edge) to what `scope` may see.

    Entities, events, relations and chunk nodes all carry `first_page`; an item without one
    (unknown page) is never hidden.
    """
    if scope.sid is not None:
        raise NotImplementedError("storyline scopes are not supported yet (canon only)")
    parts = [f"{a}.sid IS NULL" for a in aliases]
    if scope.max_page is not None:
        cap = int(scope.max_page)  # an int, never user text, so it is safe to inline
        parts += [f"({a}.first_page IS NULL OR {a}.first_page <= {cap})" for a in aliases]
    return " AND ".join(parts)


def _props(value: Any) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in OMITTED_GRAPH_PROPS}


def _entity(scope: GraphScope, value: Any) -> GraphEntity:
    """An entity as `scope` may see it. With a page cap the description is rebuilt from the
    per-page notes up to that page (the stored one summarises the whole book), and `last_page`
    is clamped. A graph published before notes were stored shows no description under a cap."""
    props = _props(value)
    cap = scope.max_page
    if cap is not None:
        notes, pages = props.get("notes") or [], props.get("note_pages") or []
        props["description"] = " ".join(n for n, p in zip(notes, pages) if p <= cap)
        if props.get("last_page") is not None:
            props["last_page"] = min(props["last_page"], cap)
    return GraphEntity.model_validate(props)


def _read(scope: GraphScope, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
    return _graph(scope.book_id).ro_query(cypher, params or {}).result_set


def _write(book_id: str, cypher: str, params: dict[str, Any] | None = None) -> None:
    _graph(book_id).query(cypher, params or {})


def _batches(rows: Sequence[Any], size: int):
    for i in range(0, len(rows), size):
        yield list(rows[i : i + size])


# --- name index cache ------------------------------------------------------

# (book, sid, page cap) -> (loaded at, rows). Every name search needs all entity names, so they
# are kept briefly in memory; writes in this process drop the entry, other processes catch up
# within the TTL.
_INDEX_CACHE: dict[tuple[str, str | None, int | None], tuple[float, list[dict[str, Any]]]] = {}


def invalidate_cache(book_id: str | None = None) -> None:
    """Forget cached name indexes (all of them, or one book's)."""
    for key in [k for k in _INDEX_CACHE if book_id is None or k[0] == book_id]:
        _INDEX_CACHE.pop(key, None)


# --- schema --------------------------------------------------------------


def _create_index(book_id: str, cypher: str) -> None:
    try:
        _write(book_id, cypher)
    except Exception as exc:  # FalkorDB errors if the index already exists
        if FALKOR_INDEX_EXISTS_ERROR not in str(exc).lower():
            raise


def ensure_indexes(book_id: str) -> None:
    """Range indexes for the properties the read queries look nodes up or filter by."""
    for label in KEY_INDEXED_LABELS:
        _create_index(book_id, f"CREATE INDEX FOR (n:{label}) ON (n.key)")
    for label, prop in PROPERTY_INDEXES:
        _create_index(book_id, f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")


def ensure_vector_indexes(book_id: str, dimension: int) -> None:
    """Vector indexes over the `embedding` of entities, events and chunks (cosine distance)."""
    for label in VECTOR_INDEXED_LABELS:
        _create_index(
            book_id,
            f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
            f"OPTIONS {{dimension: {int(dimension)}, similarityFunction: '{VECTOR_SIMILARITY}'}}",
        )


def drop_graph(book_id: str, missing_ok: bool = False) -> None:
    invalidate_cache(book_id)
    try:
        _graph(book_id).delete()
    except Exception as exc:  # FalkorDB errors if the graph does not exist
        if not (missing_ok and FALKOR_MISSING_GRAPH_ERROR in str(exc).lower()):
            raise


# --- canon writes --------------------------------------------------------


def _payload(item: GraphEntity | GraphEvent | GraphRelation) -> dict[str, Any]:
    if item.sid is not None:
        raise ValueError("canon writes must not carry a sid")
    return item.model_dump(exclude_none=True)


def _batched_write(book_id: str, cypher: str, rows: Sequence[dict[str, Any]], size: int) -> None:
    for part in _batches(rows, size):
        _write(book_id, cypher, {"rows": part})


def upsert_entities(book_id: str, entities: Sequence[GraphEntity]) -> None:
    """Entities get their type as a second label (`:Entity:Character`) when it is a known type."""
    invalidate_cache(book_id)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for e in entities:
        by_type.setdefault(e.type, []).append(_payload(e))
    for type_, rows in by_type.items():
        labels = f"Entity:{type_}" if type_ in ENTITY_TYPES else "Entity"  # never raw input
        _batched_write(
            book_id,
            f"""
            UNWIND $rows AS row
            MERGE (n:{labels} {{key: row.key}})
            SET n += row
            SET n.embedding = CASE WHEN row.embedding IS NULL THEN NULL ELSE vecf32(row.embedding) END
            """,
            rows,
            VECTOR_WRITE_BATCH,
        )


def upsert_events(book_id: str, events: Sequence[GraphEvent]) -> None:
    rows = [_payload(e) for e in events]
    _batched_write(
        book_id,
        """
        UNWIND $rows AS row
        MERGE (n:Event {key: row.key})
        SET n += row
        SET n.embedding = CASE WHEN row.embedding IS NULL THEN NULL ELSE vecf32(row.embedding) END
        """,
        rows,
        VECTOR_WRITE_BATCH,
    )


def upsert_relations(book_id: str, relations: Sequence[GraphRelation]) -> None:
    rows = [_payload(r) for r in relations]
    _batched_write(
        book_id,
        """
        UNWIND $rows AS row
        MATCH (a:Entity {key: row.src}), (b:Entity {key: row.dst})
        MERGE (a)-[r:REL {id: row.id}]->(b)
        SET r.rel = row.rel, r.description = row.description, r.weight = row.weight,
            r.story_pos = row.story_pos, r.first_page = row.first_page,
            r.chunk_ids = row.chunk_ids
        """,
        rows,
        WRITE_BATCH,
    )


def upsert_event_links(
    book_id: str,
    involves: Sequence[dict[str, Any]] = (),
    at: Sequence[dict[str, Any]] = (),
    before: Sequence[dict[str, Any]] = (),
    in_chapter: Sequence[dict[str, Any]] = (),
) -> None:
    """Structural event edges. Rows: involves {event, entity, role}, at {event, place},
    before {src, dst} (event keys), in_chapter {event, chapter} (chapter title)."""
    queries = (
        (
            involves,
            """
            UNWIND $rows AS row
            MATCH (e:Event {key: row.event}), (n:Entity {key: row.entity})
            MERGE (e)-[r:INVOLVES]->(n)
            SET r.role = row.role
            """,
        ),
        (
            at,
            """
            UNWIND $rows AS row
            MATCH (e:Event {key: row.event}), (p:Entity {key: row.place})
            MERGE (e)-[:AT]->(p)
            """,
        ),
        (
            before,
            """
            UNWIND $rows AS row
            MATCH (a:Event {key: row.src}), (b:Event {key: row.dst})
            MERGE (a)-[:BEFORE]->(b)
            """,
        ),
        (
            in_chapter,
            """
            UNWIND $rows AS row
            MATCH (e:Event {key: row.event})
            MERGE (c:Chapter {title: row.chapter})
            MERGE (e)-[:IN_CHAPTER]->(c)
            """,
        ),
    )
    for rows, cypher in queries:
        if rows:
            _batched_write(book_id, cypher, list(rows), WRITE_BATCH)


def upsert_chunk_vectors(book_id: str, rows: Sequence[dict[str, Any]]) -> None:
    """One `:Chunk` node per page of the book, for finding passages by meaning.
    Rows: {id, seq, chapter, first_page, embedding}."""
    _batched_write(
        book_id,
        """
        UNWIND $rows AS row
        MERGE (c:Chunk {id: row.id})
        SET c.seq = row.seq, c.chapter = row.chapter, c.first_page = row.first_page,
            c.embedding = vecf32(row.embedding)
        """,
        rows,
        VECTOR_WRITE_BATCH,
    )


def write_communities(book_id: str, communities: Sequence[Community]) -> None:
    """Replace the book's `:Community` nodes, `IN_COMMUNITY` edges and `community_id` values.

    An entity is linked to its community at every level; `community_id` is the most detailed
    (lowest-level) one.
    """
    _write(book_id, "MATCH (c:Community) DETACH DELETE c")
    _write(book_id, "MATCH (n:Entity) WHERE n.community_id IS NOT NULL REMOVE n.community_id")
    if not communities:
        return
    nodes = [
        {"cid": c.cid, "level": c.level, "title": c.title, "summary": c.summary,
         "size": len(c.entity_keys)}
        for c in communities
    ]  # fmt: skip
    _batched_write(
        book_id,
        """
        UNWIND $rows AS row
        MERGE (c:Community {cid: row.cid})
        SET c.level = row.level, c.title = row.title, c.summary = row.summary, c.size = row.size
        """,
        nodes,
        WRITE_BATCH,
    )
    links = [{"entity": k, "cid": c.cid} for c in communities for k in c.entity_keys]
    _batched_write(
        book_id,
        """
        UNWIND $rows AS row
        MATCH (n:Entity {key: row.entity}), (c:Community {cid: row.cid})
        MERGE (n)-[:IN_COMMUNITY]->(c)
        """,
        links,
        WRITE_BATCH,
    )
    finest: dict[str, int] = {}
    for c in sorted(communities, key=lambda c: -c.level):  # lowest level written last, so it wins
        for k in c.entity_keys:
            finest[k] = c.cid
    _batched_write(
        book_id,
        "UNWIND $rows AS row MATCH (n:Entity {key: row.entity}) SET n.community_id = row.cid",
        [{"entity": k, "cid": cid} for k, cid in finest.items()],
        WRITE_BATCH,
    )


# --- reads ---------------------------------------------------------------


def get_entity(scope: GraphScope, key: str) -> GraphEntity | None:
    rows = _read(
        scope,
        f"MATCH (n:Entity {{key: $key}}) WHERE {visible(scope, 'n')} RETURN properties(n)",
        {"key": key},
    )
    return _entity(scope, rows[0][0]) if rows else None


def get_entities(scope: GraphScope, keys: Sequence[str]) -> list[GraphEntity]:
    rows = _read(
        scope,
        f"MATCH (n:Entity) WHERE n.key IN $keys AND {visible(scope, 'n')} RETURN properties(n)",
        {"keys": list(keys)},
    )
    return [_entity(scope, r[0]) for r in rows]


def get_event(scope: GraphScope, key: str) -> GraphEvent | None:
    rows = _read(
        scope,
        f"MATCH (n:Event {{key: $key}}) WHERE {visible(scope, 'n')} RETURN properties(n)",
        {"key": key},
    )
    return GraphEvent.model_validate(_props(rows[0][0])) if rows else None


def events_between(scope: GraphScope, pos_from: float, pos_to: float) -> list[GraphEvent]:
    """Events with `pos_from <= story_pos <= pos_to`, in narrative order."""
    rows = _read(
        scope,
        f"""
        MATCH (n:Event)
        WHERE n.story_pos >= $pos_from AND n.story_pos <= $pos_to AND {visible(scope, 'n')}
        RETURN properties(n) ORDER BY n.story_pos
        """,
        {"pos_from": pos_from, "pos_to": pos_to},
    )
    return [GraphEvent.model_validate(_props(r[0])) for r in rows]


def entity_relations(
    scope: GraphScope,
    key: str,
    limit: int = DEFAULT_RELATION_LIMIT,
    rel_types: Sequence[str] | None = None,
) -> list[tuple[GraphRelation, GraphEntity]]:
    """REL edges touching `key` (either direction) with the entity on the other end, strongest
    first, optionally limited to `rel_types`."""
    type_filter = "AND r.rel IN $rel_types" if rel_types else ""
    rows = _read(
        scope,
        f"""
        MATCH (n:Entity {{key: $key}})-[r:REL]-(m:Entity)
        WHERE {visible(scope, 'n', 'r', 'm')} {type_filter}
        RETURN properties(r), startNode(r).key, endNode(r).key, properties(m)
        ORDER BY r.weight DESC
        LIMIT $limit
        """,
        {"key": key, "limit": limit, "rel_types": list(rel_types or [])},
    )
    return [
        (
            GraphRelation.model_validate({**r_props, "src": src, "dst": dst}),
            _entity(scope, m_props),
        )
        for r_props, src, dst, m_props in rows
    ]


def entity_index(scope: GraphScope) -> list[dict[str, Any]]:
    """Name, aliases and popularity of every entity: all the fields name search needs.

    The list is cached for `ENTITY_INDEX_TTL_SECONDS` and shared between callers: treat it as
    read-only.
    """
    cache_key = (scope.book_id, scope.sid, scope.max_page)
    hit = _INDEX_CACHE.get(cache_key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < ENTITY_INDEX_TTL_SECONDS:
        return hit[1]
    rows = _read(
        scope,
        f"""
        MATCH (n:Entity) WHERE {visible(scope, 'n')}
        RETURN n.key, n.name, n.type, n.aliases, n.mention_count
        """,
    )
    index = [
        {"key": k, "name": name, "type": type_, "aliases": aliases or [], "mention_count": count or 0}
        for k, name, type_, aliases, count in rows
    ]
    _INDEX_CACHE[cache_key] = (now, index)
    return index


def expand(
    scope: GraphScope,
    keys: Sequence[str],
    rel_types: Sequence[str] | None = None,
    per_node_limit: int | None = None,
) -> list[tuple[str, GraphRelation, GraphEntity]]:
    """One hop out of `keys` over REL edges (either direction), optionally limited to `rel_types`.
    Each row is (key it was reached from, the edge, the entity on the other end), strongest edges
    first. With `per_node_limit`, only that many of the strongest edges are taken from each key,
    so one hub cannot flood the result; the cut is made inside the database.
    """
    type_filter = "AND r.rel IN $rel_types" if rel_types else ""
    where = f"n.key IN $keys {type_filter} AND {visible(scope, 'n', 'r', 'm')}"
    if per_node_limit:
        cypher = f"""
        MATCH (n:Entity)-[r:REL]-(m:Entity)
        WHERE {where}
        WITH n, r, m ORDER BY r.weight DESC
        WITH n, collect({{r: r, m: m}})[0..$per_node] AS top
        UNWIND top AS t
        RETURN n.key, properties(t.r), startNode(t.r).key, endNode(t.r).key, properties(t.m)
        """
    else:
        cypher = f"""
        MATCH (n:Entity)-[r:REL]-(m:Entity)
        WHERE {where}
        RETURN n.key, properties(r), startNode(r).key, endNode(r).key, properties(m)
        ORDER BY r.weight DESC
        """
    rows = _read(
        scope,
        cypher,
        {"keys": list(keys), "rel_types": list(rel_types or []), "per_node": per_node_limit or 0},
    )
    out = [
        (
            origin,
            GraphRelation.model_validate({**r_props, "src": src, "dst": dst}),
            _entity(scope, m_props),
        )
        for origin, r_props, src, dst, m_props in rows
    ]
    if per_node_limit:  # collect() loses the global order; keep the strongest first
        out.sort(key=lambda row: -row[1].weight)
    return out


def entity_events(scope: GraphScope, key: str) -> list[tuple[GraphEvent, str]]:
    """Events involving `key` with the entity's role in each, in narrative order."""
    rows = _read(
        scope,
        f"""
        MATCH (e:Event)-[i:INVOLVES]->(n:Entity {{key: $key}})
        WHERE {visible(scope, 'e', 'n')}
        RETURN properties(e), i.role ORDER BY e.story_pos
        """,
        {"key": key},
    )
    return [(GraphEvent.model_validate(_props(e)), role) for e, role in rows]


def chapter_events(scope: GraphScope, chapter: str) -> list[tuple[GraphEvent, list[str]]]:
    """Events of one chapter (exact title) in narrative order, each with its participants' keys."""
    rows = _read(
        scope,
        f"""
        MATCH (e:Event {{chapter: $chapter}}) WHERE {visible(scope, 'e')}
        OPTIONAL MATCH (e)-[:INVOLVES]->(n:Entity)
        RETURN properties(e), collect(n.key) ORDER BY e.story_pos
        """,
        {"chapter": chapter},
    )
    return [(GraphEvent.model_validate(_props(e)), [k for k in keys if k]) for e, keys in rows]


# --- vector search ---------------------------------------------------------


def _similar(
    scope: GraphScope, label: str, vector: Sequence[float], k: int, returns: str
) -> list[list[Any]]:
    """Nearest `k` nodes of `label` visible to `scope`, as rows of `returns` plus the cosine
    similarity (1 = same direction). Empty when the book has no vector index yet."""
    if not vector or k <= 0:
        return []
    fetch = k * 3 if scope.max_page is not None else k  # the page filter runs after the search
    try:
        rows = _read(
            scope,
            f"""
            CALL db.idx.vector.queryNodes('{label}', 'embedding', $k, vecf32($vector))
            YIELD node, score
            WHERE {visible(scope, 'node')}
            RETURN {returns}, score
            """,
            {"k": fetch, "vector": list(vector)},
        )
    except ResponseError as exc:  # no embeddings were stored for this book
        log.debug("vector search on %s unavailable: %s", label, exc)
        return []
    return [[*row[:-1], 1.0 - float(row[-1])] for row in rows[:k]]


def similar_entities(
    scope: GraphScope, vector: Sequence[float], k: int = 5
) -> list[tuple[str, float]]:
    """(entity key, similarity) of the entities whose description is closest to `vector`."""
    return [(key, sim) for key, sim in _similar(scope, "Entity", vector, k, "node.key")]


def similar_events(
    scope: GraphScope, vector: Sequence[float], k: int = 5
) -> list[tuple[GraphEvent, float]]:
    """(event, similarity) of the events whose summary is closest to `vector`."""
    rows = _similar(scope, "Event", vector, k, "properties(node)")
    return [(GraphEvent.model_validate(_props(props)), sim) for props, sim in rows]


def similar_chunks(
    scope: GraphScope, vector: Sequence[float], k: int = 20
) -> list[tuple[str, float]]:
    """(chunk id, similarity) of the passages closest to `vector`."""
    return [(cid, sim) for cid, sim in _similar(scope, "Chunk", vector, k, "node.id")]
