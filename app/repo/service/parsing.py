"""PDF parsing: bytes/path -> cleaned text with page offsets and detected chapters.

Pure functions, no database access. Ingestion step 2 ("parse and clean") calls
`parse_pdf`, then the chunker uses `ParsedBook.text`, `.page_spans` and `.chapters`
to build `Chunk` records (see `ParsedBook.spans_for_range`).

Page numbers always come from the PDF itself (the page an offset falls on), never from
LLM output.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

try:  # pypdf is the maintained successor of PyPDF2 and has the same API
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    from PyPDF2 import PdfReader  # type: ignore[no-redef]

from app.repo.models import PageSpan

PAGE_SEPARATOR = "\n\n"
MIN_CHARS_PER_PAGE = 20  # below this average we assume a scan with no text layer
HEADER_FOOTER_MIN_PAGES = 4  # don't try to detect running headers on tiny documents
HEADER_FOOTER_MIN_REPEATS = 3  # ...and a line must repeat on at least this many pages
HEADER_FOOTER_FRACTION = 0.4  # a line repeated on >= 40% of pages is a header/footer
EDGE_LINES = 3  # how many lines at the top/bottom of a page count as "edge"

_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    "sixteen|seventeen|eighteen|nineteen|twenty"
)
# "Chapter 3", "CHAPTER III: The Road", "Part Two - Exile", or a bare "Prologue"/"Epilogue".
_CHAPTER_RE = re.compile(
    rf"^\s*(?:(?i:chapter|part|book)\s+(?:\d{{1,3}}|[IVXLC]{{1,7}}|(?i:{_NUMBER_WORDS}))\b[\s.:\-–—]*.{{0,80}}"
    r"|(?i:prologue|epilogue|preface|foreword|afterword|introduction))\s*[.:]?\s*$"
)
_PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?[-–—]?\s*(\d{1,4}|[ivxlc]{1,7})\s*[-–—]?\s*$", re.I)


class PDFParseError(Exception):
    """The PDF could not be read at all (corrupt, encrypted, wrong type)."""


class ScannedPDFError(PDFParseError):
    """The PDF has no usable text layer; it needs OCR."""


@dataclass
class Chapter:
    title: str
    char_start: int  # offset into ParsedBook.text
    page: int  # 1-based page the heading appears on


@dataclass
class ParsedBook:
    title: str | None
    author: str | None
    content_hash: str
    text: str
    pages: list[str]
    page_spans: list[PageSpan]  # offsets into `text`; one entry per non-empty page
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_at(self, offset: int) -> int:
        """1-based page containing `offset` in `text`."""
        for span in self.page_spans:
            if span.char_start <= offset < span.char_end:
                return span.page
        return self.page_spans[-1].page if self.page_spans else 1

    def chapter_at(self, offset: int) -> str | None:
        """Title of the chapter containing `offset`, or None before the first heading."""
        current = None
        for chapter in self.chapters:
            if chapter.char_start > offset:
                break
            current = chapter.title
        return current

    def spans_for_range(self, start: int, end: int) -> list[PageSpan]:
        """Page spans overlapping text[start:end], with offsets relative to `start`.

        This is the shape `Chunk.page_spans` expects: offsets within the chunk text.
        """
        out: list[PageSpan] = []
        for span in self.page_spans:
            lo, hi = max(span.char_start, start), min(span.char_end, end)
            if lo < hi:
                out.append(PageSpan(page=span.page, char_start=lo - start, char_end=hi - start))
        return out


# --- cleaning ------------------------------------------------------------


def _norm_line(line: str) -> str:
    """Normalise a line for header/footer comparison: digits collapse so 'p. 12' == 'p. 13'."""
    return re.sub(r"\d+", "#", line.strip().lower())


def _detect_running_lines(raw_pages: list[str]) -> set[str]:
    """Normalised lines that repeat at the top/bottom of many pages (headers, footers)."""
    if len(raw_pages) < HEADER_FOOTER_MIN_PAGES:
        return set()
    counts: Counter[str] = Counter()
    for page in raw_pages:
        lines = [ln for ln in page.splitlines() if ln.strip()]
        edge = lines[:EDGE_LINES] + lines[-EDGE_LINES:]
        counts.update({_norm_line(ln) for ln in edge})
    threshold = max(HEADER_FOOTER_MIN_REPEATS, int(len(raw_pages) * HEADER_FOOTER_FRACTION))
    return {line for line, n in counts.items() if n >= threshold and len(line) < 120}


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and len(stripped) <= 100 and bool(_CHAPTER_RE.match(stripped))


def _clean_page(raw: str, running: set[str]) -> str:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").splitlines()
    # Drop running headers/footers and bare page numbers, but only near the page edges.
    n = len(lines)
    kept = []
    for i, line in enumerate(lines):
        near_edge = i < EDGE_LINES or i >= n - EDGE_LINES
        if near_edge and (_norm_line(line) in running or _PAGE_NUMBER_RE.match(line)):
            continue
        if _is_heading(line):
            # Force paragraph breaks around headings so line-joining can't fold them into prose.
            kept.extend(["", line.strip(), ""])
        else:
            kept.append(line.rstrip())
    text = "\n".join(kept)
    # Re-join words hyphenated across a line break: "wander-\ning" -> "wandering".
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Single newlines inside a paragraph become spaces; blank lines stay as paragraph breaks.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- chapters ------------------------------------------------------------


def _find_chapters(text: str, page_spans: list[PageSpan]) -> list[Chapter]:
    """Detect chapter headings: short standalone paragraphs like 'Chapter 3: The Road'."""
    chapters: list[Chapter] = []
    pos = 0
    for para in text.split("\n\n"):
        stripped = para.strip()
        if "\n" not in stripped and _is_heading(stripped):
            start = text.find(para, pos)
            page = next((s.page for s in page_spans if s.char_start <= start < s.char_end), 1)
            chapters.append(Chapter(title=stripped, char_start=start, page=page))
        pos += len(para) + len(PAGE_SEPARATOR)
    return chapters


# --- public API ----------------------------------------------------------


def parse_pdf(source: str | Path | bytes) -> ParsedBook:
    """Parse a PDF (path or raw bytes) into cleaned text, page spans and chapters.

    Raises `ScannedPDFError` when there is no text layer and `PDFParseError` for
    unreadable or password-protected files.
    """
    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    else:
        data = bytes(source)

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PDFParseError("PDF is password-protected")
        raw_pages = [(page.extract_text() or "") for page in reader.pages]
        meta = reader.metadata
    except PDFParseError:
        raise
    except Exception as exc:  # pypdf raises many exception types for bad files
        raise PDFParseError(f"could not read PDF: {exc}") from exc

    if not raw_pages:
        raise PDFParseError("PDF has no pages")
    if sum(len(p.strip()) for p in raw_pages) / len(raw_pages) < MIN_CHARS_PER_PAGE:
        raise ScannedPDFError("PDF has no text layer; OCR is required")

    running = _detect_running_lines(raw_pages)
    pages = [_clean_page(p, running) for p in raw_pages]

    # Build the full text and record where each page lands in it.
    parts: list[str] = []
    spans: list[PageSpan] = []
    offset = 0
    for number, page_text in enumerate(pages, start=1):
        if not page_text:
            continue
        if parts:
            offset += len(PAGE_SEPARATOR)
        spans.append(PageSpan(page=number, char_start=offset, char_end=offset + len(page_text)))
        parts.append(page_text)
        offset += len(page_text)
    text = PAGE_SEPARATOR.join(parts)

    title = (meta.title or "").strip() if meta else ""
    author = (meta.author or "").strip() if meta else ""
    return ParsedBook(
        title=title or None,
        author=author or None,
        content_hash=hashlib.sha256(data).hexdigest(),
        text=text,
        pages=pages,
        page_spans=spans,
        chapters=_find_chapters(text, spans),
    )
