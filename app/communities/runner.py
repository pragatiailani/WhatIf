"""Build a book's communities: detect groups, summarise each with the LLM, write them out.

Level 0 holds the groups (cid 1, 2, ... most connected first); level 1 holds one overview of the
whole book (cid OVERVIEW_CID), written from the group summaries. Summaries go to MongoDB; FalkorDB
gets a Community node per group, IN_COMMUNITY edges and `community_id` on each member entity.
Rebuilding replaces everything, so it is safe to rerun.
"""

import asyncio
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from app.communities.detect import detect, entity_graph
from app.communities.prompt import (
    BookOverview,
    CommunityReport,
    build_community_messages,
    build_overview_messages,
)
from app.config import get_settings
from app.constants import OVERVIEW_CID
from app.extraction.runner import is_blocked
from app.llm import get_chat_model
from app.merge.publish import root_level
from app.repo import books, communities, graph
from app.repo.models import Community, GraphPayload

log = logging.getLogger(__name__)


def _fallback(members: list[str], payload: GraphPayload) -> CommunityReport:
    """Used when the content filter refuses a group: its names and descriptions, unsummarised."""
    by_key = {e["key"]: e for e in payload.entities}
    names = [by_key[k]["name"] for k in members[:4]]
    notes = [by_key[k].get("description", "") for k in members[:4]]
    return CommunityReport(title=", ".join(names), summary=" ".join(n for n in notes if n))


async def abuild_communities(book_id: str, *, llm: BaseChatModel | None = None) -> dict[str, int]:
    """Detect and summarise the book's communities. Returns stats for the book."""
    book = books.get_book(book_id)
    if book is None:
        raise ValueError(f"unknown book '{book_id}'")
    payload = root_level(book_id).graph
    groups = detect(entity_graph(payload))

    model = llm or get_chat_model()
    for_groups = model.with_structured_output(CommunityReport, method="function_calling")
    for_book = model.with_structured_output(BookOverview, method="function_calling")
    limit = asyncio.Semaphore(get_settings().merge_concurrency)

    async def summarise(structured, messages: list[BaseMessage]):
        """The structured result, or None if the content filter refused the prompt."""
        async with limit:
            try:
                return await structured.ainvoke(messages)
            except Exception as exc:
                if not is_blocked(exc):
                    raise
                return None

    reports = await asyncio.gather(
        *(summarise(for_groups, build_community_messages(book.title, g, payload)) for g in groups)
    )
    level0 = []
    for cid, (members, report) in enumerate(zip(groups, reports, strict=True), 1):
        report = report or _fallback(members, payload)
        level0.append(Community(book_id=book_id, cid=cid, level=0, title=report.title.strip(),
                                summary=report.summary.strip(), entity_keys=members))
    found = list(level0)
    if level0:
        overview = await summarise(for_book, build_overview_messages(book.title, level0))
        if overview is not None:
            found.append(Community(book_id=book_id, cid=OVERVIEW_CID, level=1,
                                   title=overview.title.strip(), summary=overview.summary.strip()))

    communities.replace_book(book_id, found)
    graph.set_communities(
        book_id,
        [{"cid": c.cid, "title": c.title} for c in level0],
        [{"key": k, "cid": c.cid} for c in level0 for k in c.entity_keys],
    )
    log.info("book %s: %d communities", book_id, len(level0))
    return {"communities": len(level0)}


def build_communities(book_id: str, **kwargs) -> dict[str, int]:
    """Synchronous `abuild_communities`."""
    return asyncio.run(abuild_communities(book_id, **kwargs))
