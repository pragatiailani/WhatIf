"""FalkorDB access. Every read goes through `visible()`, the sid/hidden-set filter hook.

Only canon scopes are supported for now; storyline scopes (sid + hidden sets) plug into
`visible()` once the storyline repo exists.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from falkordb import Graph

from app.constants import (
    DEFAULT_RELATION_LIMIT,
    ENTITY_TYPES,
    FALKOR_INDEX_EXISTS_ERROR,
    FALKOR_MISSING_GRAPH_ERROR,
    GRAPH_NAME_PREFIX,
    KEY_INDEXED_LABELS,
    OMITTED_GRAPH_PROPS,
)
from app.db.falkor import select_graph
from app.repo.models import GraphEntity, GraphEvent, GraphRelation


@dataclass(frozen=True)
class GraphScope:
    book_id: str
    sid: str | None = None  # None = canon

    @classmethod
    def canon(cls, book_id: str) -> "GraphScope":
        return cls(book_id=book_id)


def graph_name(book_id: str) -> str:
    return f"{GRAPH_NAME_PREFIX}{book_id}"


def _graph(book_id: str) -> Graph:
    return select_graph(graph_name(book_id))


def visible(scope: GraphScope, *aliases: str) -> str:
    """Cypher predicate restricting each alias (node or edge) to what `scope` may see."""
    if scope.sid is not None:
        raise NotImplementedError("storyline scopes are not supported yet (canon only)")
    return " AND ".join(f"{a}.sid IS NULL" for a in aliases)


def _props(value: Any) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in OMITTED_GRAPH_PROPS}


def _read(scope: GraphScope, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
    return _graph(scope.book_id).ro_query(cypher, params or {}).result_set


def _write(book_id: str, cypher: str, params: dict[str, Any] | None = None) -> None:
    _graph(book_id).query(cypher, params or {})


# --- schema --------------------------------------------------------------


def ensure_indexes(book_id: str) -> None:
    for label in KEY_INDEXED_LABELS:
        try:
            _write(book_id, f"CREATE INDEX FOR (n:{label}) ON (n.key)")
        except Exception as exc:  # FalkorDB errors if the index already exists
            if FALKOR_INDEX_EXISTS_ERROR not in str(exc).lower():
                raise


def drop_graph(book_id: str, missing_ok: bool = False) -> None:
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


def upsert_entities(book_id: str, entities: Sequence[GraphEntity]) -> None:
    """Entities get their type as a second label (`:Entity:Character`) when it is a known type."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for e in entities:
        by_type.setdefault(e.type, []).append(_payload(e))
    for type_, rows in by_type.items():
        labels = f"Entity:{type_}" if type_ in ENTITY_TYPES else "Entity"  # never raw input
        _write(
            book_id,
            f"""
            UNWIND $rows AS row
            MERGE (n:{labels} {{key: row.key}})
            SET n += row
            SET n.embedding = CASE WHEN row.embedding IS NULL THEN NULL ELSE vecf32(row.embedding) END
            """,
            {"rows": rows},
        )


def upsert_events(book_id: str, events: Sequence[GraphEvent]) -> None:
    rows = [_payload(e) for e in events]
    _write(
        book_id,
        """
        UNWIND $rows AS row
        MERGE (n:Event {key: row.key})
        SET n += row
        SET n.embedding = CASE WHEN row.embedding IS NULL THEN NULL ELSE vecf32(row.embedding) END
        """,
        {"rows": rows},
    )


def upsert_relations(book_id: str, relations: Sequence[GraphRelation]) -> None:
    rows = [_payload(r) for r in relations]
    _write(
        book_id,
        """
        UNWIND $rows AS row
        MATCH (a:Entity {key: row.src}), (b:Entity {key: row.dst})
        MERGE (a)-[r:REL {id: row.id}]->(b)
        SET r.rel = row.rel, r.description = row.description, r.weight = row.weight,
            r.story_pos = row.story_pos
        """,
        {"rows": rows},
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
            _write(book_id, cypher, {"rows": list(rows)})


# --- reads ---------------------------------------------------------------


def get_entity(scope: GraphScope, key: str) -> GraphEntity | None:
    rows = _read(
        scope,
        f"MATCH (n:Entity {{key: $key}}) WHERE {visible(scope, 'n')} RETURN properties(n)",
        {"key": key},
    )
    return GraphEntity.model_validate(_props(rows[0][0])) if rows else None


def get_entities(scope: GraphScope, keys: Sequence[str]) -> list[GraphEntity]:
    rows = _read(
        scope,
        f"MATCH (n:Entity) WHERE n.key IN $keys AND {visible(scope, 'n')} RETURN properties(n)",
        {"keys": list(keys)},
    )
    return [GraphEntity.model_validate(_props(r[0])) for r in rows]


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
    scope: GraphScope, key: str, limit: int = DEFAULT_RELATION_LIMIT
) -> list[tuple[GraphRelation, GraphEntity]]:
    """REL edges touching `key` (either direction) with the entity on the other end."""
    rows = _read(
        scope,
        f"""
        MATCH (n:Entity {{key: $key}})-[r:REL]-(m:Entity)
        WHERE {visible(scope, 'n', 'r', 'm')}
        RETURN properties(r), startNode(r).key, endNode(r).key, properties(m)
        ORDER BY r.weight DESC
        LIMIT $limit
        """,
        {"key": key, "limit": limit},
    )
    return [
        (
            GraphRelation.model_validate({**r_props, "src": src, "dst": dst}),
            GraphEntity.model_validate(_props(m_props)),
        )
        for r_props, src, dst, m_props in rows
    ]


def entity_index(scope: GraphScope) -> list[dict[str, Any]]:
    """Name, aliases and popularity of every entity: all the fields name search needs."""
    rows = _read(
        scope,
        f"""
        MATCH (n:Entity) WHERE {visible(scope, 'n')}
        RETURN n.key, n.name, n.type, n.aliases, n.mention_count
        """,
    )
    return [
        {"key": k, "name": name, "type": type_, "aliases": aliases or [], "mention_count": count or 0}
        for k, name, type_, aliases, count in rows
    ]


def expand(
    scope: GraphScope, keys: Sequence[str], rel_types: Sequence[str] | None = None
) -> list[tuple[str, GraphRelation, GraphEntity]]:
    """One hop out of `keys` over REL edges (either direction), optionally limited to `rel_types`.
    Each row is (key it was reached from, the edge, the entity on the other end)."""
    type_filter = "AND r.rel IN $rel_types" if rel_types else ""
    rows = _read(
        scope,
        f"""
        MATCH (n:Entity)-[r:REL]-(m:Entity)
        WHERE n.key IN $keys {type_filter} AND {visible(scope, 'n', 'r', 'm')}
        RETURN n.key, properties(r), startNode(r).key, endNode(r).key, properties(m)
        ORDER BY r.weight DESC
        """,
        {"keys": list(keys), "rel_types": list(rel_types or [])},
    )
    return [
        (
            origin,
            GraphRelation.model_validate({**r_props, "src": src, "dst": dst}),
            GraphEntity.model_validate(_props(m_props)),
        )
        for origin, r_props, src, dst, m_props in rows
    ]


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
