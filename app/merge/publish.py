"""Publish the merged book graph to FalkorDB.

MongoDB remains the source of truth for original chunk text and mentions. FalkorDB stores
entities, events, relations, and chunk embeddings for graph/vector retrieval.
"""

from app.constants import DEFAULT_EMBEDDING_BATCH_SIZE, MAX_EDGE_CHUNK_IDS
from app.llm import get_embeddings
from app.repo import chunks, graph, graph_levels, mentions
from app.repo.models import GraphEntity, GraphEvent, GraphLevel, GraphRelation


def _root(book_id: str) -> GraphLevel:
    top = graph_levels.max_level(book_id)
    root = graph_levels.get_level_graph(book_id, top, 0) if top else None
    n_chunks = chunks.count_chunks(book_id)
    if root is None or (root.seq_start, root.seq_end) != (0, n_chunks - 1):
        raise ValueError(f"book {book_id} has no finished merge; run merge_page_graphs first")
    return root


def _description(entity: dict) -> str:
    if entity.get("description"):
        return entity["description"]
    return " ".join(n["text"] for n in entity.get("descriptions", []))


def _embed(texts: list[str]) -> list[list[float] | None]:
    """Embed texts in batches; return aligned None values when embeddings are disabled."""
    if not texts:
        return []
    model = get_embeddings()
    if model is None:
        return [None] * len(texts)
    vectors: list[list[float] | None] = []
    for start in range(0, len(texts), DEFAULT_EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + DEFAULT_EMBEDDING_BATCH_SIZE]
        vectors.extend(model.embed_documents(batch))
    return vectors


def _page_bounds(chunk) -> tuple[int | None, int | None]:
    pages = [span.page for span in chunk.page_spans]
    return (min(pages), max(pages)) if pages else (None, None)


def publish_book_graph(book_id: str) -> dict[str, int]:
    """Load the merged graph into FalkorDB. Returns counts of what was written."""
    g = _root(book_id).graph

    entity_descriptions = [_description(e) for e in g.entities]
    entity_texts = [
        f"{e['name']} ({e['type']}): {desc}".strip()
        for e, desc in zip(g.entities, entity_descriptions, strict=True)
    ]
    event_texts = [e["summary"] for e in g.events]
    entity_vectors = _embed(entity_texts)
    event_vectors = _embed(event_texts)

    entities = [
        GraphEntity(
            key=e["key"],
            name=e["name"],
            type=e["type"],
            aliases=e.get("aliases", []),
            description=entity_descriptions[i],
            embedding=entity_vectors[i],
            first_page=e.get("first_page"),
            last_page=e.get("last_page"),
            mention_count=e.get("mention_count", 0),
        )
        for i, e in enumerate(g.entities)
    ]
    events = [
        GraphEvent(
            key=e["key"],
            summary=e["summary"],
            story_pos=e["story_pos"],
            story_time=e.get("story_time"),
            chapter=e.get("chapter"),
            first_page=e.get("first_page"),
            embedding=event_vectors[i],
            chunk_ids=list(e.get("chunk_ids", [])),
        )
        for i, e in enumerate(g.events)
    ]
    relations = []
    for r in g.relations:
        pages = sorted(set(r.get("pages", [])))
        relations.append(
            GraphRelation(
                src=r["src"],
                dst=r["dst"],
                rel=r["rel"],
                description=r.get("description", ""),
                weight=r.get("weight", 1.0),
                story_pos=r.get("pos_start"),
                first_page=pages[0] if pages else None,
                pages=pages,
                chunk_ids=list(r.get("chunk_ids", []))[:MAX_EDGE_CHUNK_IDS],
            )
        )

    # Each entity source records (chunk_seq, pre-merge node key). Rekey with the chunk sequence
    # so same-named page-level nodes from separate chunks do not accidentally collide.
    mention_mappings: list[tuple[int, str, str]] = []
    for e in g.entities:
        for source in e.get("sources", []):
            if len(source) >= 2:
                mention_mappings.append((int(source[0]), str(source[1]), str(e["key"])))
    for e in g.events:
        for source in e.get("sources", []):
            if len(source) >= 2:
                mention_mappings.append((int(source[0]), str(source[1]), str(e["key"])))

    # Prepare all embeddings before dropping the current graph. A model failure therefore does
    # not leave an existing published graph half-deleted.
    book_chunks = chunks.list_chunks(book_id)
    chunk_texts = [c.text for c in book_chunks]
    chunk_vectors = _embed(chunk_texts)
    vector_dimension = next(
        (len(v) for v in [*entity_vectors, *event_vectors, *chunk_vectors] if v), None
    )

    graph.drop_graph(book_id, missing_ok=True)
    graph.ensure_indexes(book_id)
    graph.upsert_entities(book_id, entities)
    graph.upsert_events(book_id, events)
    graph.upsert_relations(book_id, relations)

    ordered = sorted(g.events, key=lambda e: e["story_pos"])
    involves = [
        {"event": e["key"], "entity": p["key"], "role": p["role"]}
        for e in g.events
        for p in e.get("participants", [])
    ]
    at = [{"event": e["key"], "place": e["place"]} for e in g.events if e.get("place")]
    before = [
        {"src": a["key"], "dst": b["key"]}
        for a, b in zip(ordered, ordered[1:], strict=False)
    ]
    in_chapter = [
        {"event": e["key"], "chapter": e["chapter"]}
        for e in g.events
        if e.get("chapter")
    ]
    graph.upsert_event_links(book_id, involves, at, before, in_chapter)

    if vector_dimension:
        graph.upsert_chunk_vectors(
            book_id,
            [
                {
                    "key": chunk.id,
                    "book_id": book_id,
                    "seq": chunk.seq,
                    "chapter": chunk.chapter,
                    "page_start": _page_bounds(chunk)[0],
                    "page_end": _page_bounds(chunk)[1],
                    "embedding": vector,
                }
                for chunk, vector in zip(book_chunks, chunk_vectors, strict=True)
                if vector is not None
            ],
        )
        graph.ensure_vector_indexes(book_id, vector_dimension)

    mentions.rekey_by_sources(book_id, mention_mappings)

    return {
        "entities": len(entities),
        "events": len(events),
        "relations": len(relations),
        "event_links": len(involves) + len(at) + len(before) + len(in_chapter),
        "chunk_vectors": sum(vector is not None for vector in chunk_vectors),
    }
