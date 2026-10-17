import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.repo.service.parsing import PDFParseError, ScannedPDFError, parse_pdf

BODY = [
    "Ravi walked through the crowded streets of Delhi, wander-",
    "ing between market stalls and thinking about home.",
]


def _make_pdf(pages: list[list[str]], title="Test Book", author="A. Writer", header="WHATIF NOVEL"):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)
    c.setAuthor(author)
    _, height = letter
    for number, lines in enumerate(pages, start=1):
        if header:
            c.drawString(72, height - 40, header)
        y = height - 100
        for line in lines:
            c.drawString(72, y, line)
            y -= 16
        c.drawString(300, 30, str(number))
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def book_pdf():
    pages = [
        ["Chapter 1: The Departure", "", *BODY],
        [f"Page two body text number {i} goes here with enough words." for i in range(4)],
        ["Chapter 2: The Return", "", *BODY],
        [f"Final page body text number {i} with plenty of words." for i in range(4)],
    ]
    return _make_pdf(pages)


def test_metadata_and_hash(book_pdf):
    book = parse_pdf(book_pdf)
    assert book.title == "Test Book"
    assert book.author == "A. Writer"
    assert book.page_count == 4
    assert len(book.content_hash) == 64
    assert parse_pdf(book_pdf).content_hash == book.content_hash


def test_cleaning_removes_headers_page_numbers_and_hyphens(book_pdf):
    book = parse_pdf(book_pdf)
    assert "WHATIF NOVEL" not in book.text
    assert "wandering between market stalls" in book.text
    assert not any(line.strip().isdigit() for line in book.text.splitlines())


def test_chapters_detected_with_pages(book_pdf):
    book = parse_pdf(book_pdf)
    assert [(c.title, c.page) for c in book.chapters] == [
        ("Chapter 1: The Departure", 1),
        ("Chapter 2: The Return", 3),
    ]
    offset = book.text.index("Final page")
    assert book.chapter_at(offset) == "Chapter 2: The Return"
    assert book.chapter_at(0) == "Chapter 1: The Departure"


def test_page_spans_match_text(book_pdf):
    book = parse_pdf(book_pdf)
    for span in book.page_spans:
        assert book.text[span.char_start : span.char_end] == book.pages[span.page - 1]
    assert book.page_at(book.text.index("Final page")) == 4


def test_spans_for_range_are_chunk_relative(book_pdf):
    book = parse_pdf(book_pdf)
    start = book.page_spans[1].char_start - 10  # straddles pages 1 and 2
    end = book.page_spans[1].char_start + 30
    spans = book.spans_for_range(start, end)
    assert [s.page for s in spans] == [1, 2]
    assert spans[0].char_start == 0
    assert spans[-1].char_end == end - start


def test_accepts_path(tmp_path, book_pdf):
    path = tmp_path / "book.pdf"
    path.write_bytes(book_pdf)
    assert parse_pdf(path).page_count == 4
    assert parse_pdf(str(path)).page_count == 4


def test_scanned_pdf_raises():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.rect(100, 100, 200, 200, fill=1)  # image-like page, no text
    c.showPage()
    c.save()
    with pytest.raises(ScannedPDFError):
        parse_pdf(buf.getvalue())


def test_garbage_raises():
    with pytest.raises(PDFParseError):
        parse_pdf(b"this is not a pdf")
