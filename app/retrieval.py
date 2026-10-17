"""Semantic retrieval: embed a book's chunks, entities, events and community summaries, and
search them by meaning.

Indexing runs at the end of ingestion (`index_book`). Search embeds the query once and ranks a
book's vectors by cosine similarity; tools combine that with keyword or name matches through
reciprocal rank fusion (`fuse`), which needs no score calibration between the two.

Everything is optional: with no `AZURE_EMBEDDING_MODEL`, `enabled()` is False, nothing is indexed,
and searches return no semantic hits, so the tools fall back to keyword and name matching.
"""

import logging
from collections import defaultdict
from collections.abc import Sequence

import numpy as np

from app.constants import EMBED_BATCH, EMBED_MAX_CHARS, RRF_K, SEMANTIC_CANDIDATES
from app.llm import get_embeddings
from app.merge.publish import entity_description, root_level
from app.repo import chunks, communities, vectors
from app.repo.vectors import VectorKind

log = logging.getLogger(__name__)

# (book_id, kind) -> (index version, refs, matrix); reloaded when the book is re-indexed.
_cache: dict[tuple[str, str], tuple[str, list[str], np.ndarray]] = {}


def enabled() -> bool:
    return get_embeddings() is not None


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """One row per text. Requires an embedding model."""
    model = get_embeddings()
    if model is None:
        raise RuntimeError("no embedding model configured (AZURE_EMBEDDING_MODEL)")
    rows: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [t[:EMBED_MAX_CHARS].strip() or "(empty)" for t in texts[i : i + EMBED_BATCH]]
        rows.extend(model.embed_documents(batch))
    return np.asarray(rows, dtype=np.float32)


def embed_query(text: str) -> np.ndarray | None:
    """The query as a unit vector, or None without an embedding model."""
    model = get_embeddings()
    if model is None or not text.strip():
        return None
    vec = np.asarray(model.embed_query(text[:EMBED_MAX_CHARS]), dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def entity_text(entity: dict) -> str:
    aliases = ", ".join(a for a in entity.get("aliases", []) if a != entity["name"])
    also = f" Also called {aliases}." if aliases else ""
    return f"{entity['name']} ({entity['type']}).{also} {entity_description(entity)}".strip()


def _store(book_id: str, kind: VectorKind, refs: list[str], texts: list[str]) -> int:
    matrix = embed_texts(texts) if texts else np.zeros((0, 0), dtype=np.float32)
    vectors.replace_book(book_id, kind, refs, matrix)
    return len(refs)


def index_communities(book_id: str) -> int:
    """Embed the book's community summaries (refs are cids), replacing earlier ones."""
    found = communities.list_communities(book_id)
    return _store(book_id, "community", [str(c.cid) for c in found],
                  [f"{c.title}. {c.summary}" for c in found])  # fmt: skip


def index_book(book_id: str) -> dict[str, int]:
    """Embed the book's chunks, its merged graph's entities and events, and its communities,
    replacing any earlier vectors. Returns stats for the book (empty without an embedding
    model)."""
    if not enabled():
        log.info("no embedding model configured; book %s gets keyword search only", book_id)
        return {}
    pages = chunks.list_chunks(book_id)
    g = root_level(book_id).graph
    total = (
        _store(book_id, "chunk", [c.id for c in pages], [c.text for c in pages])
        + _store(book_id, "entity", [e["key"] for e in g.entities], [entity_text(e) for e in g.entities])
        + _store(book_id, "event", [e["key"] for e in g.events], [e["summary"] for e in g.events])
        + index_communities(book_id)
    )
    return {"vectors": total}


def _matrix(book_id: str, kind: VectorKind) -> tuple[list[str], np.ndarray]:
    current = vectors.version(book_id)
    if current is None:
        return [], np.zeros((0, 0), dtype=np.float32)
    cached = _cache.get((book_id, kind))
    if cached is None or cached[0] != current:
        refs, matrix = vectors.load(book_id, kind)
        cached = _cache[(book_id, kind)] = (current, refs, matrix)
    return cached[1], cached[2]


def semantic(
    book_id: str, kind: VectorKind, query_vec: np.ndarray | None, limit: int = SEMANTIC_CANDIDATES
) -> list[tuple[str, float]]:
    """(ref, cosine similarity) of the `limit` vectors closest to `query_vec`, best first."""
    if query_vec is None:
        return []
    refs, matrix = _matrix(book_id, kind)
    if not refs:
        return []
    if matrix.shape[1] != query_vec.shape[0]:
        log.warning("book %s was indexed with another embedding model; re-index it", book_id)
        return []
    scores = matrix @ query_vec
    top = np.argsort(-scores, kind="stable")[:limit]
    return [(refs[i], float(scores[i])) for i in top]


def fuse(*rankings: Sequence[str]) -> list[tuple[str, float]]:
    """Reciprocal rank fusion of ref rankings (best first each), best first."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, ref in enumerate(ranking):
            scores[ref] += 1.0 / (RRF_K + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])
