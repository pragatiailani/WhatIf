import pytest

from app.extraction.page_graph import build_page_graph
from app.extraction.prompt import build_messages
from app.extraction.runner import generate_page_graphs
from app.extraction.schema import (
    ChunkExtraction,
    EventParticipant,
    ExtractedEntity,
    ExtractedEvent,
    ExtractedRelation,
)
from app.repo import chunks, graph_levels, mentions
from app.repo.models import Chunk, PageSpan

PAGE = (
    "Ravi Sharma left Delhi at dawn. His friend Meera waved from the station in Delhi. "
    "Later Ravi, the clerk, reached Mumbai."
)


def _chunk(seq=2, page=11, text=PAGE, book="b1") -> Chunk:
    return Chunk(
        book_id=book,
        seq=seq,
        chapter="Chapter 1",
        text=text,
        page_spans=[PageSpan(page=page, char_start=0, char_end=len(text))],
    )


def _extraction() -> ChunkExtraction:
    return ChunkExtraction(
        entities=[
            ExtractedEntity(
                name="Ravi Sharma", type="Character", aliases=["Ravi", "the clerk"],
                description="Leaves Delhi.", evidence="Ravi Sharma left Delhi at dawn",
            ),
            ExtractedEntity(
                name="Meera", type="Character", description="Ravi's friend.",
                evidence="His friend Meera waved",
            ),
            ExtractedEntity(
                name="Delhi", type="Place", description="A city.", evidence="left Delhi at dawn"
            ),
            ExtractedEntity(
                name="Ravi Sharma", type="Character", aliases=["R."],
                description="A clerk who reaches Mumbai.", evidence="Ravi, the clerk, reached Mumbai",
            ),
            ExtractedEntity(  # invented: not on the page
                name="Kabir", type="Character", description="x", evidence="Kabir drew his sword"
            ),
        ],
        events=[
            ExtractedEvent(
                summary="Ravi leaves Delhi",
                participants=[
                    EventParticipant(name="Ravi", role="actor"),
                    EventParticipant(name="Nobody", role="witness"),
                ],
                place="Delhi",
                evidence="Ravi Sharma left Delhi at dawn",
            ),
            ExtractedEvent(summary="Kabir attacks", evidence="Kabir drew his sword"),
        ],
        relations=[
            ExtractedRelation(
                source="Ravi", target="Meera", rel="friend of", evidence="His friend Meera"
            ),
            ExtractedRelation(
                source="Ravi", target="Meera", rel="friend of", evidence="His friend Meera"
            ),
            ExtractedRelation(source="Ravi", target="Kabir", rel="KILLS", evidence="His friend Meera"),
            ExtractedRelation(source="Ravi", target="Ravi", rel="KNOWS", evidence="His friend Meera"),
        ],
    )


def test_entities_merged_grounded_and_keyed():
    page = build_page_graph(_chunk(), _extraction())
    by_key = {e["key"]: e for e in page.graph.entities}
    assert set(by_key) == {"ravi_sharma|character", "meera|character", "delhi|place"}

    ravi = by_key["ravi_sharma|character"]
    assert ravi["mention_count"] == 2
    assert ravi["aliases"] == ["Ravi", "the clerk", "R."]
    assert ravi["description"] == "A clerk who reaches Mumbai."  # the longer one wins
    assert ravi["first_page"] == ravi["last_page"] == 11


def test_events_resolve_participants_and_use_story_pos():
    page = build_page_graph(_chunk(seq=2), _extraction())
    assert len(page.graph.events) == 1  # Kabir's event has no grounding
    event = page.graph.events[0]
    assert event["key"] == "event|2|0"
    assert event["story_pos"] == 2000.0
    assert event["participants"] == [{"key": "ravi_sharma|character", "role": "actor"}]
    assert event["place"] == "delhi|place"
    assert (event["chapter"], event["first_page"]) == ("Chapter 1", 11)


def test_relations_deduped_and_invalid_ones_dropped():
    page = build_page_graph(_chunk(), _extraction())
    assert len(page.graph.relations) == 1
    rel = page.graph.relations[0]
    assert (rel["src"], rel["dst"], rel["rel"]) == (
        "ravi_sharma|character", "meera|character", "FRIEND_OF",
    )
    assert rel["weight"] == 2.0


def test_mentions_carry_matched_text_offsets_and_page():
    chunk = _chunk()
    page = build_page_graph(chunk, _extraction())
    assert page.dropped == 5  # Kabir entity, "Nobody", Kabir event, Kabir relation, self relation
    for m in page.mentions:
        assert chunk.text[m.char_start : m.char_end] == m.quote
        assert (m.page, m.chunk_seq, m.chunk_id) == (11, 2, chunk.id)
    assert {m.node_key for m in page.mentions} == {
        "ravi_sharma|character", "meera|character", "delhi|place", "event|2|0",
    }


def test_level_record_is_level_zero_at_chunk_seq():
    chunk = _chunk(seq=5)
    level = build_page_graph(chunk, ChunkExtraction()).to_level(chunk)
    assert (level.book_id, level.level, level.index) == ("b1", 0, 5)
    assert (level.seq_start, level.seq_end) == (5, 5)


def test_prompt_contains_page_text_and_context():
    system, human = build_messages(_chunk())
    assert "EXACTLY" in system.content
    assert "Chapter: Chapter 1" in human.content and "Page 11" in human.content
    assert PAGE in human.content


# --- runner ------------------------------------------------------------------------------


class FakeLLM:
    """Stands in for a chat model: returns a canned ChunkExtraction per page, or raises."""

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.batches: list[int] = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def batch(self, inputs, config=None, return_exceptions=False):
        self.batches.append(len(inputs))
        out = []
        for messages in inputs:
            text = messages[1].content
            if any(marker in text for marker in self.fail_on):
                out.append(RuntimeError("llm down"))
            else:
                out.append(_extraction())
        return out


def _store_pages(n: int) -> list[Chunk]:
    pages = [_chunk(seq=i, page=i + 1, text=PAGE + f" Page marker {i}.") for i in range(n)]
    chunks.insert_chunks(pages)
    return pages


def test_runner_batches_saves_graph_and_mentions(mongo):
    _store_pages(5)
    llm = FakeLLM()
    report = generate_page_graphs("b1", llm=llm, batch_size=2)

    assert llm.batches == [2, 2, 1]
    assert report.done == [0, 1, 2, 3, 4] and not report.failed and not report.skipped
    assert report.dropped == 5 * 5
    assert graph_levels.count_level("b1", 0) == 5
    stored = graph_levels.get_level_graph("b1", 0, 3)
    assert len(stored.graph.entities) == 3
    assert len(mentions.list_for_node("b1", "ravi_sharma|character")) == 10  # 2 quotes per page


def test_runner_reports_failures_and_resumes(mongo):
    _store_pages(4)
    report = generate_page_graphs("b1", llm=FakeLLM(fail_on=["Page marker 2"]), batch_size=10)
    assert report.done == [0, 1, 3]
    assert list(report.failed) == [2] and "llm down" in report.failed[2]
    assert graph_levels.get_level_graph("b1", 0, 2) is None

    again = generate_page_graphs("b1", llm=FakeLLM(), batch_size=10)
    assert again.skipped == [0, 1, 3] and again.done == [2]
    assert len(mentions.list_for_node("b1", "ravi_sharma|character")) == 8  # 2 per page, no duplicates


def test_force_regenerates_without_duplicate_mentions(mongo):
    _store_pages(2)
    generate_page_graphs("b1", llm=FakeLLM())
    report = generate_page_graphs("b1", llm=FakeLLM(), force=True)
    assert report.done == [0, 1] and not report.skipped
    assert len(mentions.list_for_node("b1", "meera|character")) == 2


def test_invalid_batch_size(mongo):
    with pytest.raises(ValueError):
        generate_page_graphs("b1", llm=FakeLLM(), batch_size=-1)
