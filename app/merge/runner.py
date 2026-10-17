"""Merge a book's level-0 page graphs into one graph: a binary tree of adjacent merges.

Node (level, index) covers chunks [index * 2**level, (index + 1) * 2**level) and is built from
nodes (level - 1, 2 * index) and (level - 1, 2 * index + 1). There are no level barriers: a node
is merged as soon as its two children exist. A node with a single child is a copy of it.

Each pair of entities is compared once, at the merge where their branches meet. Clear cases are
decided in code (`app.merge.candidates`); the rest go to the LLM judge in one batched call per
merge (split past `MERGE_MAX_CANDIDATES_PER_CALL` pairs). A merge with nothing ambiguous makes no
LLM call. A "same" verdict is applied only at `MERGE_MIN_SAME_CONFIDENCE` or above, one-to-one.

A call the provider refuses (content filter) is treated as no answer: the pairs it held stay
separate and the entities keep their per-page notes.

Every saved node is a checkpoint: a node already in `graph_levels` is loaded instead of rebuilt,
so a resumed run skips finished subtrees. A failed merge is reported and left unsaved, and
independent subtrees keep going. Last, the root's entities get final descriptions (batched).

The tree shape depends on the chunk count: delete levels >= 1 after re-chunking a book.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable

from app.config import get_settings
from app.constants import (
    FINAL_DESCRIPTION_BATCH,
    MERGE_MAX_CANDIDATES_PER_CALL,
    MERGE_MIN_SAME_CONFIDENCE,
)
from app.extraction.runner import is_blocked
from app.extraction.schema import slug
from app.llm import get_chat_model, get_embeddings
from app.merge.candidates import Candidates, Vectors, find_candidates, names
from app.merge.combine import Resolution, combine, from_leaf
from app.merge.prompt import build_description_messages, build_merge_messages
from app.merge.schema import FinalDescriptions, MergeVerdicts
from app.repo import books, graph_levels
from app.repo import chunks as chunk_repo
from app.repo.models import GraphLevel, GraphPayload

NodeId = tuple[int, int]  # (level, index)


@dataclass
class MergeReport:
    root: NodeId | None = None  # set once the whole book is one graph
    merged: list[NodeId] = field(default_factory=list)  # nodes built in this run
    skipped: list[NodeId] = field(default_factory=list)  # already saved by an earlier run
    failed: dict[NodeId, str] = field(default_factory=dict)  # node -> error
    llm_calls: int = 0
    blocked_calls: int = 0  # refused by the content filter
    same_by_code: int = 0  # entity + event pairs merged without asking the LLM
    same_by_llm: int = 0
    described: int = 0  # root entities given a final description by the LLM in this run
    description_errors: list[str] = field(default_factory=list)


def tree_height(n_chunks: int) -> int:
    """Level of the root. At least 1, so the root is always in the merged shape."""
    return max(1, (n_chunks - 1).bit_length())


def children(level: int, index: int, n_chunks: int) -> tuple[NodeId, NodeId | None]:
    right_start = (2 * index + 1) << (level - 1)
    right = (level - 1, 2 * index + 1) if right_start < n_chunks else None
    return (level - 1, 2 * index), right


class _SubtreeFailed(Exception):
    """A node below failed; the failure is reported where it happened."""


def _payload(node: GraphLevel) -> GraphPayload:
    return from_leaf(node) if node.level == 0 else node.graph


def _embed_text(entity: dict) -> str:
    notes = " ".join(n["text"] for n in entity["descriptions"])
    return f"{entity['name']} ({entity['type']}): {notes}".strip()


class _Merger:
    def __init__(
        self,
        book_id: str,
        n_chunks: int,
        llm: BaseChatModel,
        embeddings: Embeddings | None,
        concurrency: int,
    ):
        self.book_id = book_id
        self.n_chunks = n_chunks
        self.book = books.get_book(book_id)
        self.judge = llm.with_structured_output(MergeVerdicts, method="function_calling")
        self.describer = llm.with_structured_output(FinalDescriptions, method="function_calling")
        self.embeddings = embeddings
        self.limit = asyncio.Semaphore(concurrency)  # LLM + embedding calls in flight
        self.vectors: dict[str, np.ndarray] = {}  # embedding text -> unit vector, for this run
        self.report = MergeReport()

    # --- tree --------------------------------------------------------------------------------

    async def build(self, level: int, index: int) -> GraphLevel:
        saved = graph_levels.get_level_graph(self.book_id, level, index)
        if saved is not None:
            if level == 0:
                return self._with_chunk_refs(saved)
            self.report.skipped.append((level, index))
            return saved
        if level == 0:
            self.report.failed[(level, index)] = "no level-0 graph"
            raise _SubtreeFailed

        left_id, right_id = children(level, index, self.n_chunks)
        ids = [left_id] if right_id is None else [left_id, right_id]
        kids = await asyncio.gather(*(self.build(*i) for i in ids), return_exceptions=True)
        if any(isinstance(k, BaseException) for k in kids):
            raise _SubtreeFailed
        try:
            graph = await self._merge(*kids) if len(kids) == 2 else _payload(kids[0])
        except Exception as exc:
            self.report.failed[(level, index)] = repr(exc)
            raise _SubtreeFailed from exc

        first, last = kids[0], kids[-1]
        node = GraphLevel(
            book_id=self.book_id,
            level=level,
            index=index,
            seq_start=first.seq_start,
            seq_end=last.seq_end,
            page_start=first.page_start,
            page_end=last.page_end,
            chunk_ids=[c for k in kids for c in k.chunk_ids],
            graph=graph,
        )
        graph_levels.save_level_graph(node)
        self.report.merged.append((level, index))
        return node

    def _with_chunk_refs(self, leaf: GraphLevel) -> GraphLevel:
        """Fill chunk id and pages on level-0 records saved before they carried them."""
        if leaf.chunk_ids:
            return leaf
        chunk = chunk_repo.list_chunks(self.book_id, leaf.seq_start, leaf.seq_start)[0]
        spans = chunk.page_spans
        return leaf.model_copy(
            update={
                "chunk_ids": [chunk.id],
                "page_start": spans[0].page if spans else None,
                "page_end": spans[-1].page if spans else None,
            }
        )

    # --- one merge ---------------------------------------------------------------------------

    async def _merge(self, left: GraphLevel, right: GraphLevel) -> GraphPayload:
        lg, rg = _payload(left), _payload(right)
        lv, rv = await asyncio.gather(self._embed(lg.entities), self._embed(rg.entities))
        found = find_candidates(lg, rg, lv, rv)
        resolution = Resolution(entities=dict(found.entities), events=dict(found.events))
        self.report.same_by_code += len(found.entities) + len(found.events)
        if found.ambiguous_entities or found.ambiguous_events:
            await self._judge(left, right, lg, rg, found, resolution)
        return combine(lg, rg, resolution)

    async def _embed(self, entities: list[dict]) -> Vectors | None:
        if self.embeddings is None or not entities:
            return None
        texts = {e["key"]: _embed_text(e) for e in entities}
        missing = list(dict.fromkeys(t for t in texts.values() if t not in self.vectors))
        if missing:
            async with self.limit:
                raw = await self.embeddings.aembed_documents(missing)
            for text, values in zip(missing, raw, strict=True):
                vec = np.asarray(values, dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                self.vectors[text] = vec / norm if norm else vec
        return {key: self.vectors[text] for key, text in texts.items()}

    async def _call(self, runnable: Runnable, messages: list[BaseMessage]):
        """The structured result, or None if the content filter refused the prompt."""
        async with self.limit:
            self.report.llm_calls += 1
            try:
                result = await runnable.ainvoke(messages)
            except Exception as exc:
                if not is_blocked(exc):
                    raise
                self.report.blocked_calls += 1
                return None
        if result is None:
            raise ValueError("empty LLM response")
        return result

    async def _judge(
        self,
        left: GraphLevel,
        right: GraphLevel,
        lg: GraphPayload,
        rg: GraphPayload,
        found: Candidates,
        resolution: Resolution,
    ) -> None:
        entities = [(f"E{i}", c) for i, c in enumerate(found.ambiguous_entities, 1)]
        events = [(f"V{i}", c) for i, c in enumerate(found.ambiguous_events, 1)]
        items = [*entities, *events]
        prompts = []
        for start in range(0, len(items), MERGE_MAX_CANDIDATES_PER_CALL):
            part = items[start : start + MERGE_MAX_CANDIDATES_PER_CALL]
            prompts.append(
                build_merge_messages(
                    self.book,
                    left,
                    right,
                    lg,
                    rg,
                    [x for x in part if x[0].startswith("E")],
                    [x for x in part if x[0].startswith("V")],
                )
            )
        results = [
            r for r in await asyncio.gather(*(self._call(self.judge, p) for p in prompts)) if r
        ]
        entity_verdicts = {v.id: v for r in results for v in r.entities}
        event_verdicts = {v.id: v for r in results for v in r.events}

        def accepted(pairs, verdicts):
            same = [
                (verdicts[id_], c)
                for id_, c in pairs
                if id_ in verdicts
                and verdicts[id_].decision == "same"
                and verdicts[id_].confidence >= MERGE_MIN_SAME_CONFIDENCE
            ]
            return sorted(same, key=lambda vc: -vc[0].confidence)  # most confident first

        # One-to-one: each node merges with at most one node from the other side.
        for mapping, pairs, verdicts in (
            (resolution.entities, entities, entity_verdicts),
            (resolution.events, events, event_verdicts),
        ):
            used = set(mapping.values())
            for verdict, c in accepted(pairs, verdicts):
                rk, lk = c.right["key"], c.left["key"]
                if rk in mapping or lk in used:
                    continue
                mapping[rk] = lk
                used.add(lk)
                self.report.same_by_llm += 1
                name = getattr(verdict, "canonical_name", "").strip()
                if name and slug(name) in {*names(c.left), *names(c.right)}:
                    resolution.names[lk] = name  # only a name the text actually used

    # --- final descriptions ------------------------------------------------------------------

    async def describe(self, root: GraphLevel) -> None:
        """Give each root entity one `description`. One note is used as is; several go to the
        LLM in batches. Entities that already have one (an earlier run) are skipped."""
        todo, changed = [], False
        for e in root.graph.entities:
            if "description" in e:
                continue
            if len(e["descriptions"]) > 1:
                todo.append(e)
            else:
                e["description"] = e["descriptions"][0]["text"] if e["descriptions"] else ""
                changed = True
        if changed:
            graph_levels.save_level_graph(root)

        async def run(batch: list[dict]) -> None:
            result = await self._call(self.describer, build_description_messages(batch))
            if result is None:
                return
            written = {d.key: d.description.strip() for d in result.descriptions}
            for e in batch:
                if written.get(e["key"]):
                    e["description"] = written[e["key"]]
                    self.report.described += 1
            graph_levels.save_level_graph(root)

        size = FINAL_DESCRIPTION_BATCH
        batches = [todo[i : i + size] for i in range(0, len(todo), size)]
        results = await asyncio.gather(*(run(b) for b in batches), return_exceptions=True)
        self.report.description_errors = [repr(r) for r in results if isinstance(r, BaseException)]


async def amerge_page_graphs(
    book_id: str,
    *,
    llm: BaseChatModel | None = None,
    embeddings: Embeddings | Literal[False] | None = None,
    concurrency: int | None = None,
    describe: bool = True,
) -> MergeReport:
    """Merge all level-0 page graphs of `book_id` into one graph at (`tree_height`, 0).

    `embeddings` defaults to the configured model (none if `AZURE_EMBEDDING_MODEL` is unset);
    pass False to compare names only. Every page needs a level-0 graph first.
    """
    concurrency = concurrency or get_settings().merge_concurrency
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    n_chunks = chunk_repo.count_chunks(book_id)
    if n_chunks == 0:
        raise ValueError(f"book {book_id} has no chunks")
    missing = n_chunks - graph_levels.count_level(book_id, 0)
    if missing:
        raise ValueError(f"{missing} page(s) have no level-0 graph; run generate_page_graphs first")

    if embeddings is None:
        embeddings = get_embeddings()
    merger = _Merger(book_id, n_chunks, llm or get_chat_model(), embeddings or None, concurrency)
    root_id = (tree_height(n_chunks), 0)
    try:
        root = await merger.build(*root_id)
    except _SubtreeFailed:
        return merger.report
    merger.report.root = root_id
    if describe:
        await merger.describe(root)
    return merger.report


def merge_page_graphs(book_id: str, **kwargs) -> MergeReport:
    """Synchronous `amerge_page_graphs`."""
    return asyncio.run(amerge_page_graphs(book_id, **kwargs))
