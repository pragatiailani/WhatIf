"""LLM output for one page -> grounded level-0 graph plus mentions. Pure, no LLM or database.

Every item must have an evidence quote found in the page (`locate_quote`). Items that cannot be
grounded, and references to entities that were dropped or never extracted, are discarded and
counted in `PageGraph.dropped`.
"""

from dataclasses import dataclass

from app.extraction.evidence import Evidence, locate_quote
from app.extraction.schema import ChunkExtraction, entity_key, event_key, slug
from app.repo.models import Chunk, GraphLevel, GraphPayload, Mention

STORY_POS_PER_CHUNK = 1000  # story_pos = chunk.seq * 1000 + order within the chunk


@dataclass
class PageGraph:
    graph: GraphPayload
    mentions: list[Mention]
    dropped: int  # items discarded: ungrounded quote, unknown entity, empty name

    def to_level(self, chunk: Chunk) -> GraphLevel:
        """The level-0 record for this page (index = chunk seq)."""
        return GraphLevel(
            book_id=chunk.book_id,
            level=0,
            index=chunk.seq,
            seq_start=chunk.seq,
            seq_end=chunk.seq,
            graph=self.graph,
        )


def _mention(chunk: Chunk, node_key: str, ev: Evidence) -> Mention:
    return Mention(
        book_id=chunk.book_id,
        node_key=node_key,
        chunk_id=chunk.id,
        chunk_seq=chunk.seq,
        quote=ev.quote,
        char_start=ev.char_start,
        char_end=ev.char_end,
        page=ev.page,
    )


def build_page_graph(chunk: Chunk, extraction: ChunkExtraction) -> PageGraph:
    mentions: list[Mention] = []
    dropped = 0

    # --- entities: merge repeats within the page by key ---------------------------------
    entities: dict[str, dict] = {}
    by_name: dict[str, str] = {}  # slug of a name or alias -> entity key
    for item in extraction.entities:
        ev = locate_quote(chunk, item.evidence)
        if not slug(item.name) or ev is None:
            dropped += 1
            continue
        key = entity_key(item.name, item.type)
        aliases = [a for a in item.aliases if slug(a) and slug(a) != slug(item.name)]
        if key in entities:
            known = entities[key]
            known["aliases"] += [a for a in aliases if a not in known["aliases"]]
            if len(item.description) > len(known["description"]):
                known["description"] = item.description
            known["mention_count"] += 1
            known["first_page"] = min(known["first_page"], ev.page)
            known["last_page"] = max(known["last_page"], ev.page)
        else:
            entities[key] = {
                "key": key,
                "name": item.name.strip(),
                "type": item.type,
                "aliases": list(dict.fromkeys(aliases)),
                "description": item.description,
                "first_page": ev.page,
                "last_page": ev.page,
                "mention_count": 1,
            }
        for name in (item.name, *aliases):
            by_name.setdefault(slug(name), key)
        mentions.append(_mention(chunk, key, ev))

    def resolve(name: str | None) -> str | None:
        return by_name.get(slug(name)) if name else None

    # --- events --------------------------------------------------------------------------
    events: list[dict] = []
    for order, item in enumerate(extraction.events):
        ev = locate_quote(chunk, item.evidence)
        if ev is None:
            dropped += 1
            continue
        key = event_key(chunk.seq, order)
        participants = []
        for p in item.participants:
            entity = resolve(p.name)
            if entity is None:
                dropped += 1
            else:
                participants.append({"key": entity, "role": p.role})
        place = resolve(item.place)
        if item.place and place is None:
            dropped += 1
        events.append(
            {
                "key": key,
                "summary": item.summary,
                "story_pos": float(chunk.seq * STORY_POS_PER_CHUNK + order),
                "story_time": item.story_time,
                "chapter": chunk.chapter,
                "first_page": ev.page,
                "participants": participants,
                "place": place,
            }
        )
        mentions.append(_mention(chunk, key, ev))

    # --- relations -----------------------------------------------------------------------
    relations: dict[tuple[str, str, str], dict] = {}
    for item in extraction.relations:
        ev = locate_quote(chunk, item.evidence)
        src, dst = resolve(item.source), resolve(item.target)
        if ev is None or src is None or dst is None or src == dst:
            dropped += 1
            continue
        edge = relations.get((src, dst, item.rel))
        if edge:
            edge["weight"] += 1.0
        else:
            relations[(src, dst, item.rel)] = {
                "src": src,
                "dst": dst,
                "rel": item.rel,
                "description": item.description,
                "weight": 1.0,
                "story_pos": float(chunk.seq * STORY_POS_PER_CHUNK),
                "page": ev.page,
            }

    graph = GraphPayload(
        entities=list(entities.values()), events=events, relations=list(relations.values())
    )
    return PageGraph(graph=graph, mentions=mentions, dropped=dropped)
