"""Ingestion pipeline: parsed PDF -> chunks -> page graphs -> merged graph -> FalkorDB ->
communities -> embeddings.

Runs in the background after an upload. Every step is resumable: chunks are written once, finished
page graphs and merge nodes are skipped, and publish replaces the canon graph. So rerunning a
failed book picks up where it stopped.
"""

import logging
import time

from app import retrieval
from app.communities import build_communities
from app.constants import EXTRACTION_ATTEMPTS, MERGE_ATTEMPTS
from app.extraction.runner import generate_page_graphs
from app.logging_config import Timer, fields
from app.merge.publish import publish_book_graph
from app.merge.runner import merge_page_graphs, tree_height
from app.repo import books, chunks, graph_levels
from app.repo.models import Book, BookStatus
from app.repo.service.chunking import chunk_book
from app.repo.service.parsing import ParsedBook

log = logging.getLogger(__name__)

_DETAIL = {
    "queued": "Waiting to start…",
    "parsing": "Reading the pages…",
    "extracting": "Finding characters, places and events…",
    "merging": "Weaving page graphs into one story…",
    "indexing": "Writing the story graph and its summaries…",
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


def _n(report: object, name: str) -> int:
    """How many items a pass report lists under `name`, or 0 if it does not track that. Log lines
    must never be able to fail an ingestion."""
    return len(getattr(report, name, None) or ())


def _stage(book_id: str, status: BookStatus) -> None:
    log.info("stage=%s book=%s", status, book_id)
    books.set_status(book_id, status)


def ingest_book(book_id: str, parsed: ParsedBook) -> None:
    """Run every ingestion step for `book_id`, recording status and stats on the book."""
    log.info("ingestion started book=%s pages=%s chapters=%s", book_id, parsed.page_count, len(parsed.chapters))
    started = time.perf_counter()
    try:
        _stage(book_id, "parsing")
        if chunks.count_chunks(book_id) == 0:
            chunks.insert_chunks(chunk_book(book_id, parsed))
        else:
            log.info("chunks already saved, skipping book=%s", book_id)
        books.update_stats(
            book_id,
            {"pages": parsed.page_count, "chapters": len(parsed.chapters),
             "chunks": chunks.count_chunks(book_id)},
        )  # fmt: skip

        _stage(book_id, "extracting")
        blocked = 0
        for attempt in range(1, EXTRACTION_ATTEMPTS + 1):
            with Timer() as t:
                extracted = generate_page_graphs(book_id)  # skips pages already done
            blocked += len(extracted.blocked)
            log.info(
                "extraction pass %d/%d book=%s done=%d skipped=%d failed=%d blocked=%d dropped=%d in %dms",
                attempt, EXTRACTION_ATTEMPTS, book_id, _n(extracted, "done"), _n(extracted, "skipped"),
                _n(extracted, "failed"), _n(extracted, "blocked"), getattr(extracted, "dropped", 0), t.ms,
            )  # fmt: skip
            if not extracted.failed:
                break
            log.warning("extraction pass %d left %d failed page(s) book=%s", attempt, len(extracted.failed), book_id)
        else:
            seq, error = next(iter(extracted.failed.items()))
            raise RuntimeError(
                f"extraction failed on {len(extracted.failed)} page(s), e.g. chunk {seq}: {error}"
            )
        if blocked:
            log.warning("content filter blocked %d page(s) book=%s", blocked, book_id)
            books.update_stats(book_id, {"blocked_pages": blocked})

        _stage(book_id, "merging")
        for attempt in range(1, MERGE_ATTEMPTS + 1):
            with Timer() as t:
                merged = merge_page_graphs(book_id)  # skips nodes already merged
            log.info(
                "merge pass %d/%d book=%s merged=%d skipped=%d failed=%d llm_calls=%d in %dms",
                attempt, MERGE_ATTEMPTS, book_id, _n(merged, "merged"), _n(merged, "skipped"),
                _n(merged, "failed"), getattr(merged, "llm_calls", 0), t.ms,
            )  # fmt: skip
            if merged.root is not None:
                break
        else:
            node, error = next(iter(merged.failed.items()))
            raise RuntimeError(
                f"merge failed at {len(merged.failed)} node(s), e.g. node {node}: {error}"
            )

        _stage(book_id, "indexing")
        published = publish_book_graph(book_id)
        books.update_stats(book_id, published)
        try:
            books.update_stats(book_id, build_communities(book_id))
        except Exception:  # big-picture questions fall back to chapters and the graph
            log.exception("building communities for book %s failed", book_id)
        try:
            books.update_stats(book_id, retrieval.index_book(book_id))
        except Exception:  # the book still works with keyword search; re-index it later
            log.exception("embedding book %s failed; it has keyword search only", book_id)
        books.set_status(book_id, "ready")
        log.info(
            "ingestion finished book=%s %s in %.1fs", book_id, fields(published), time.perf_counter() - started
        )
    except Exception as exc:
        log.exception("ingestion failed book=%s after %.1fs", book_id, time.perf_counter() - started)
        books.set_status(book_id, "failed", repr(exc))
