"""Per-page graph generation: one LLM call per page, pages sent in batches.

For each page: ask the LLM for a `ChunkExtraction`, ground it against the page text, then save the
page's mentions and its level-0 graph. The level-0 graph is saved last and doubles as the "done"
marker, so rerunning skips finished pages and retries only the ones that failed.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel

from app.config import get_settings
from app.extraction.page_graph import PageGraph, build_page_graph
from app.extraction.prompt import build_messages
from app.extraction.schema import ChunkExtraction
from app.llm import get_chat_model
from app.repo import chunks as chunk_repo
from app.repo import graph_levels, mentions
from app.repo.models import Chunk


@dataclass
class RunReport:
    done: list[int] = field(default_factory=list)  # chunk seqs generated in this run
    skipped: list[int] = field(default_factory=list)  # already had a level-0 graph
    failed: dict[int, str] = field(default_factory=dict)  # chunk seq -> error
    dropped: int = 0  # ungrounded or unresolved items discarded across the run


def save_page_graph(chunk: Chunk, page: PageGraph) -> None:
    mentions.delete_for_chunk(chunk.id)  # a retried page must not duplicate its mentions
    mentions.insert_mentions(page.mentions)
    graph_levels.save_level_graph(page.to_level(chunk))  # last: marks the page as done


def generate_page_graphs(
    book_id: str,
    chunks: Sequence[Chunk] | None = None,
    *,
    llm: BaseChatModel | None = None,
    batch_size: int | None = None,
    force: bool = False,
) -> RunReport:
    """Generate and save the level-0 graph of every page of `book_id`.

    `chunks` defaults to all the book's chunks. Pages that already have a level-0 graph are skipped
    unless `force`. A page that fails is reported and left unsaved; the run continues.
    """
    batch_size = batch_size or get_settings().extraction_batch_size
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    pages = list(chunks) if chunks is not None else chunk_repo.list_chunks(book_id)
    structured = (llm or get_chat_model()).with_structured_output(
        ChunkExtraction, method="function_calling"
    )

    report = RunReport()
    todo: list[Chunk] = []
    for chunk in pages:
        if not force and graph_levels.get_level_graph(book_id, 0, chunk.seq):
            report.skipped.append(chunk.seq)
        else:
            todo.append(chunk)

    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        results = structured.batch(
            [build_messages(c) for c in batch],
            config={"max_concurrency": batch_size},
            return_exceptions=True,
        )
        for chunk, result in zip(batch, results):
            if isinstance(result, BaseException) or result is None:
                report.failed[chunk.seq] = repr(result) if result is not None else "empty response"
                continue
            page = build_page_graph(chunk, result)
            save_page_graph(chunk, page)
            report.done.append(chunk.seq)
            report.dropped += page.dropped
    return report
