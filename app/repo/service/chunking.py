"""Chunking: `ParsedBook` -> `Chunk` records (ingestion step 3).

One chunk per non-empty page. Pure functions, no database access.

A chunk's text is the exact page text, so offsets inside a chunk map straight to its page.
`seq` follows reading order and skips blank pages, so it is not the page number; use
`Chunk.page_spans` for that.
"""

from __future__ import annotations

from app.repo.models import Chunk
from app.repo.service.parsing import ParsedBook


def chunk_book(book_id: str, parsed: ParsedBook) -> list[Chunk]:
    """One `Chunk` per non-empty page of `parsed`, in page order."""
    chunks: list[Chunk] = []
    for span in parsed.page_spans:
        chunks.append(
            Chunk(
                book_id=book_id,
                seq=len(chunks),
                chapter=parsed.chapter_at(span.char_start),
                text=parsed.text[span.char_start : span.char_end],
                page_spans=parsed.spans_for_range(span.char_start, span.char_end),
            )
        )
    return chunks
