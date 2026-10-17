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
    FALKOR_INDEX_EXISTS_ERROR,
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


def drop_graph(book_id: str) -> None:
    _graph(book_id).delete()


# --- canon writes --------------------------------------------------------


def _payload(item: GraphEntity | GraphEvent | GraphRelation) -> dict[str, Any]:
    if item.sid is not None:
        raise ValueError("canon writes must not carry a sid")
    return item.model_dump(exclude_none=True)


def upsert_entities(book_id: str, entities: Sequence[GraphEntity]) -> None:
    rows = [_payload(e) for e in entities]
    _write(
        book_id,
        """
        UNWIND $rows AS row
        MERGE (n:Entity {key: row.key})
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
