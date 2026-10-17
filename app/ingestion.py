"""Ingestion pipeline: parsed PDF -> chunks -> page graphs -> merged graph -> FalkorDB.

Runs in the background after an upload. Every step is resumable: chunks are written once, finished
page graphs and merge nodes are skipped, and publish replaces the canon graph. So rerunning a
failed book picks up where it stopped.
"""

from app.constants import EXTRACTION_ATTEMPTS, MERGE_ATTEMPTS
from app.extraction.runner import generate_page_graphs
from app.merge.publish import publish_book_graph
from app.merge.runner import merge_page_graphs, tree_height
from app.repo import books, chunks, graph_levels
from app.repo.models import Book
from app.repo.service.chunking import chunk_book
from app.repo.service.parsing import ParsedBook

_DETAIL = {
    "queued": "Waiting to start…",
    "parsing": "Reading the pages…",
    "extracting": "Finding characters, places and events…",
    "merging": "Weaving page graphs into one story…",
    "indexing": "Writing the story graph…",
    "ready": "The story graph is ready.",
}


def progress(book: Book) -> dict:
    """Ingestion progress for the UI: status, 0-1 progress within it, and a readable detail."""
    done = 0.0
    if book.status in ("extracting", "merging"):
        n = chunks.count_chunks(book.id)
        if n and book.status == "extracting":
            done = graph_levels.count_level(book.id, 0) / n
        elif n:
            levels = range(1, tree_height(n) + 1)
            total = sum(-(-n // 2**lv) for lv in levels)  # nodes per level: ceil(n / 2**level)
            done = sum(graph_levels.count_level(book.id, lv) for lv in levels) / total
    detail = (
        (book.error or "Ingestion failed.") if book.status == "failed" else _DETAIL[book.status]
    )
    return {"book_id": book.id, "status": book.status, "progress": min(done, 1.0), "detail": detail}


def ingest_book(book_id: str, parsed: ParsedBook) -> None:
    """Run every ingestion step for `book_id`, recording status and stats on the book."""
    try:
        books.set_status(book_id, "parsing")
        if chunks.count_chunks(book_id) == 0:
            chunks.insert_chunks(chunk_book(book_id, parsed))
        books.update_stats(
            book_id,
            {"pages": parsed.page_count, "chapters": len(parsed.chapters),
             "chunks": chunks.count_chunks(book_id)},
        )  # fmt: skip

        books.set_status(book_id, "extracting")
        blocked = 0
        for _ in range(EXTRACTION_ATTEMPTS):
            extracted = generate_page_graphs(book_id)  # skips pages already done
            blocked += len(extracted.blocked)
            if not extracted.failed:
                break
        else:
            seq, error = next(iter(extracted.failed.items()))
            raise RuntimeError(
                f"extraction failed on {len(extracted.failed)} page(s), e.g. chunk {seq}: {error}"
            )
        if blocked:
            books.update_stats(book_id, {"blocked_pages": blocked})

        books.set_status(book_id, "merging")
        for _ in range(MERGE_ATTEMPTS):
            merged = merge_page_graphs(book_id)  # skips nodes already merged
            if merged.root is not None:
                break
        else:
            node, error = next(iter(merged.failed.items()))
            raise RuntimeError(
                f"merge failed at {len(merged.failed)} node(s), e.g. node {node}: {error}"
            )

        books.set_status(book_id, "indexing")
        books.update_stats(book_id, publish_book_graph(book_id))
        books.set_status(book_id, "ready")
    except Exception as exc:
        books.set_status(book_id, "failed", repr(exc))
