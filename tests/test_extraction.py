from typing import get_args

from app.constants import ENTITY_TYPES
from app.extraction.evidence import locate_quote, page_at
from app.extraction.schema import (
    ChunkExtraction,
    EntityType,
    ExtractedRelation,
    entity_key,
    event_key,
)
from app.repo.models import Chunk, PageSpan

TEXT = (
    "Ravi walked through the crowded streets of Delhi, thinking about home.\n\n"
    "“I will leave tomorrow,” he told Meera. She said nothing, and the train to Mumbai "
    "left at dawn."
)
SPLIT = TEXT.index("\n\n") + 2  # page 2 starts at the second paragraph


def _chunk(text: str = TEXT) -> Chunk:
    return Chunk(
        book_id="b1",
        seq=3,
        text=text,
        page_spans=[
            PageSpan(page=7, char_start=0, char_end=SPLIT - 2),
            PageSpan(page=8, char_start=SPLIT, char_end=len(text)),
        ],
    )


def test_exact_quote_gets_offsets_and_page():
    ev = locate_quote(_chunk(), "crowded streets of Delhi")
    assert ev is not None
    assert TEXT[ev.char_start : ev.char_end] == "crowded streets of Delhi"
    assert (ev.page, ev.score) == (7, 1.0)


def test_page_comes_from_offset_not_quote():
    ev = locate_quote(_chunk(), "the train to Mumbai left at dawn")
    assert ev is not None and ev.page == 8


def test_whitespace_differences_are_tolerated():
    ev = locate_quote(_chunk(), "streets   of\nDelhi")
    assert ev is not None
    assert ev.quote == "streets of Delhi"


def test_curly_quotes_normalized_by_llm_still_match():
    ev = locate_quote(_chunk(), '"I will leave tomorrow," he told Meera.')
    assert ev is not None
    assert ev.quote.startswith("“I will leave tomorrow")
    assert ev.quote.endswith("Meera.")
    assert 0.8 <= ev.score <= 1.0


def test_invented_quote_is_rejected():
    assert locate_quote(_chunk(), "Ravi flew to Paris on a private jet") is None
    assert locate_quote(_chunk(), "   ") is None


def test_page_at_without_spans():
    assert page_at(Chunk(book_id="b", seq=0, text="x"), 0) is None
    assert locate_quote(Chunk(book_id="b", seq=0, text="hello world"), "hello") is None


def test_relation_vocabulary_is_normalized():
    def rel(value):
        return ExtractedRelation(source="a", target="b", rel=value, evidence="q").rel

    assert rel("friend of") == "FRIEND_OF"
    assert rel("Works-For") == "WORKS_FOR"
    assert rel("secretly admires") == "OTHER"
    assert rel("involves") == "OTHER"  # structural edges are built in code, not extracted


def test_entity_type_literal_matches_constants():
    assert get_args(EntityType) == ENTITY_TYPES


def test_keys():
    assert entity_key("Ravi  Sharma", "Character") == "ravi_sharma|character"
    assert entity_key("Zoë", "Character") == "zoe|character"
    assert event_key(3, 1) == "event|3|1"


def test_chunk_extraction_defaults_empty():
    assert ChunkExtraction().model_dump() == {"entities": [], "events": [], "relations": []}
