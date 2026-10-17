"""Write a book's merged graph (the root of the merge tree) to FalkorDB as its canon graph.

Replaces whatever canon graph the book had, so it is safe to rerun after a new merge.
Story relations become `REL` edges; event edges (INVOLVES, AT, BEFORE, IN_CHAPTER) are built
here from the events' participants, place, order and chapter.
"""

from app.repo import chunks, graph, graph_levels
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


def publish_book_graph(book_id: str) -> dict[str, int]:
    """Load the merged graph into FalkorDB. Returns counts of what was written."""
    g = _root(book_id).graph
    entities = [
        GraphEntity(
            key=e["key"],
            name=e["name"],
            type=e["type"],
            aliases=e["aliases"],
            description=_description(e),
            first_page=e["first_page"],
            last_page=e["last_page"],
            mention_count=e["mention_count"],
        )
        for e in g.entities
    ]
    events = [
        GraphEvent(
            key=e["key"],
            summary=e["summary"],
            story_pos=e["story_pos"],
            story_time=e["story_time"],
            chapter=e["chapter"],
            first_page=e["first_page"],
        )
        for e in g.events
    ]
    relations = [
        GraphRelation(
            src=r["src"],
            dst=r["dst"],
            rel=r["rel"],
            description=r["description"],
            weight=r["weight"],
            story_pos=r["pos_start"],
        )
        for r in g.relations
    ]
    ordered = sorted(g.events, key=lambda e: e["story_pos"])
    involves = [
        {"event": e["key"], "entity": p["key"], "role": p["role"]}
        for e in g.events
        for p in e["participants"]
    ]
    at = [{"event": e["key"], "place": e["place"]} for e in g.events if e["place"]]
    pairs = zip(ordered, ordered[1:], strict=False)
    before = [{"src": a["key"], "dst": b["key"]} for a, b in pairs]
    in_chapter = [{"event": e["key"], "chapter": e["chapter"]} for e in g.events if e["chapter"]]

    graph.drop_graph(book_id, missing_ok=True)
    graph.ensure_indexes(book_id)
    graph.upsert_entities(book_id, entities)
    graph.upsert_events(book_id, events)
    graph.upsert_relations(book_id, relations)
    graph.upsert_event_links(book_id, involves, at, before, in_chapter)
    return {
        "entities": len(entities),
        "events": len(events),
        "relations": len(relations),
        "event_links": len(involves) + len(at) + len(before) + len(in_chapter),
    }
