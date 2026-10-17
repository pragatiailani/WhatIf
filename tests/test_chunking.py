from app.repo.models import PageSpan
from app.repo.service.chunking import chunk_book
from app.repo.service.parsing import Chapter, ParsedBook


def _book(pages: list[str], chapters: dict[int, str] | None = None) -> ParsedBook:
    """Build a ParsedBook from page texts ('' = blank page); `chapters` maps page number -> title."""
    parts, spans, offset = [], [], 0
    chapter_list = []
    for number, page in enumerate(pages, start=1):
        if not page:
            continue
        if parts:
            offset += 2
        spans.append(PageSpan(page=number, char_start=offset, char_end=offset + len(page)))
        if chapters and number in chapters:
            chapter_list.append(Chapter(title=chapters[number], char_start=offset, page=number))
        parts.append(page)
        offset += len(page)
    return ParsedBook(
        title=None,
        author=None,
        content_hash="h",
        text="\n\n".join(parts),
        pages=pages,
        page_spans=spans,
        chapters=chapter_list,
    )


def test_one_chunk_per_page_with_exact_text():
    pages = ["Page one text.", "Page two text.", "Page three text."]
    chunks = chunk_book("b1", _book(pages))
    assert [c.text for c in chunks] == pages
    assert [c.seq for c in chunks] == [0, 1, 2]
    assert all(c.book_id == "b1" for c in chunks)


def test_page_span_covers_whole_chunk_and_gives_page_number():
    chunks = chunk_book("b1", _book(["First page.", "Second page."]))
    for chunk, number in zip(chunks, (1, 2)):
        assert chunk.page_spans == [PageSpan(page=number, char_start=0, char_end=len(chunk.text))]


def test_blank_pages_are_skipped_but_keep_real_page_numbers():
    chunks = chunk_book("b1", _book(["First.", "", "", "Fourth."]))
    assert [c.seq for c in chunks] == [0, 1]
    assert [c.page_spans[0].page for c in chunks] == [1, 4]


def test_chapter_is_the_one_in_effect_at_page_start():
    parsed = _book(
        ["Prologue text.", "Chapter 1\n\nOpening.", "More of one.", "Chapter 2\n\nNext."],
        chapters={2: "Chapter 1", 4: "Chapter 2"},
    )
    assert [c.chapter for c in chunk_book("b1", parsed)] == [
        None,
        "Chapter 1",
        "Chapter 1",
        "Chapter 2",
    ]


def test_ids_are_unique():
    chunks = chunk_book("b1", _book(["a" * 30, "b" * 30, "c" * 30]))
    assert len({c.id for c in chunks}) == 3


def test_book_without_text_gives_no_chunks():
    assert chunk_book("b1", _book([])) == []
