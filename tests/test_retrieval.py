# ruff: noqa: F811  (tests take the imported `world` fixture as a parameter)
from types import SimpleNamespace

import numpy as np
import pytest

from app import ingestion, retrieval
from app.mcp_server import graph_tools, rag_tools, text_tools
from app.repo import books, chunks, graph, mentions, vectors
from app.repo.models import Book, Mention
from tests.test_mcp_tools import EVENTS, world  # noqa: F401  (fixture)

# Words that mean the same thing share a dimension, so meaning matches without shared words.
CONCEPTS = [
    {"betray", "betrayed", "betrayal", "betrays", "treachery", "traitor", "backstab"},
    {"left", "leaves", "leave", "journey", "departed", "suitcase", "travel", "dawn"},
    {"forgave", "forgive", "forgiveness", "mercy"},
    {"delhi", "city"},
]


class ConceptEmbeddings:
    """Bag of concepts: a text's vector counts the words of each concept it contains."""

    def __init__(self):
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        found = [w.strip(".,?!'\"").lower() for w in text.split()]
        return [float(sum(w in c for w in found)) + 1e-3 for c in CONCEPTS]

    def embed_documents(self, texts):
        self.calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        self.calls += 1
        return self._vec(text)


@pytest.fixture
def embeddings(monkeypatch):
    model = ConceptEmbeddings()
    monkeypatch.setattr(retrieval, "get_embeddings", lambda: model)
    return model


def index_world(book_id, model):
    """Index the `world` book: its chunks, entities and events."""
    pages = chunks.list_chunks(book_id)
    entity_texts = {
        "ravi|character": "Ravi Sharma left Delhi on a journey.",
        "asha|character": "Asha, a traitor who betrayed her friend.",
        "meera|character": "Meera could never forgive.",
        "delhi|place": "Delhi, a city.",
        "zed|character": "Zed.",
    }
    for kind, refs, texts in (
        ("chunk", [c.id for c in pages], [c.text for c in pages]),
        ("entity", list(entity_texts), list(entity_texts.values())),
        ("event", [e.key for e in EVENTS], [e.summary for e in EVENTS]),
    ):
        vectors.replace_book(book_id, kind, refs, np.asarray(model.embed_documents(texts)))


# --- storage and search ----------------------------------------------------


def test_vectors_roundtrip_and_version(mongo):
    assert vectors.version("b") is None and vectors.load("b", "chunk")[0] == []
    vectors.replace_book("b", "chunk", ["c1", "c2"], np.array([[3.0, 4.0], [0.0, 2.0]]))
    first = vectors.version("b")
    refs, m = vectors.load("b", "chunk")
    assert refs == ["c1", "c2"] and m.dtype == np.float32
    assert np.allclose(m, [[0.6, 0.8], [0.0, 1.0]])  # stored unit length

    vectors.replace_book("b", "chunk", ["c3"], np.array([[1.0, 0.0]]))
    assert vectors.load("b", "chunk")[0] == ["c3"] and vectors.version("b") != first
    with pytest.raises(ValueError):
        vectors.replace_book("b", "chunk", ["x"], np.zeros((2, 2)))
    assert vectors.delete_book("b") == 2  # the vector and the version document


def test_semantic_ranks_and_reloads_after_reindex(mongo, embeddings):
    vectors.replace_book("b", "chunk", ["far", "near"], np.array([[0, 0, 1, 0], [1, 0, 0, 0.1]]))
    q = retrieval.embed_query("treachery")
    assert [r for r, _ in retrieval.semantic("b", "chunk", q)] == ["near", "far"]

    vectors.replace_book("b", "chunk", ["new"], np.array([[1, 0, 0, 0]]))
    assert [r for r, _ in retrieval.semantic("b", "chunk", q)] == ["new"]  # cache refreshed
    assert retrieval.semantic("b", "chunk", None) == []
    assert retrieval.semantic("b", "chunk", np.ones(7, dtype=np.float32)) == []  # other model


def test_fuse_rewards_agreement():
    fused = [r for r, _ in retrieval.fuse(["a", "b", "c"], ["c", "b"], ["b"])]
    assert fused[0] == "b" and set(fused) == {"a", "b", "c"}


def test_without_model_nothing_is_embedded(mongo):
    assert not retrieval.enabled()
    assert retrieval.embed_query("anything") is None
    assert retrieval.index_book("b") == {}


def test_index_book(mongo, embeddings, monkeypatch):
    from app.repo.models import Chunk

    chunks.insert_chunks([Chunk(book_id="b", seq=i, text=t) for i, t in enumerate(["a", "b"])])
    root = SimpleNamespace(graph=SimpleNamespace(
        entities=[{"key": "ravi", "name": "Ravi", "type": "Character", "aliases": ["Ravi", "R."],
                   "description": "A traveller."}],
        events=[{"key": "e1", "summary": "Ravi leaves."}],
    ))  # fmt: skip
    monkeypatch.setattr(retrieval, "root_level", lambda book_id: root)

    assert retrieval.index_book("b") == {"vectors": 4}
    assert len(vectors.load("b", "chunk")[0]) == 2
    assert vectors.load("b", "entity")[0] == ["ravi"] and vectors.load("b", "event")[0] == ["e1"]
    assert retrieval.entity_text(root.graph.entities[0]) == (
        "Ravi (Character). Also called R.. A traveller.")


# --- tools -------------------------------------------------------------------


def test_search_chunks_without_embeddings_is_keyword_search(world):
    out = text_tools.search_chunks(world, "who betrayed Meera")
    assert out["results"][0]["match"] == "keyword" and out["total_matches"] == 1


def test_search_chunks_finds_by_meaning(world, embeddings):
    index_world(world, embeddings)
    out = text_tools.search_chunks(world, "treachery")  # no chunk contains the word
    assert out["results"][0]["chapter"] == "Chapter 2"
    assert out["results"][0]["match"] == "meaning" and out["total_matches"] == 0

    both = text_tools.search_chunks(world, "Meera betrayed")
    assert both["results"][0]["match"] == "both"
    # the chapter filter applies to meaning matches too
    assert all(r["chapter"] == "Chapter 1"
               for r in text_tools.search_chunks(world, "treachery", chapter="1")["results"])


def test_find_entities_falls_back_to_meaning(world, embeddings):
    index_world(world, embeddings)
    named = graph_tools.find_entities(world, "Where did Ravi go?")["entities"]
    assert named[0]["key"] == "ravi|character" and {e["match"] for e in named} == {"name"}

    meant = graph_tools.find_entities(world, "the traitor")["entities"]
    assert meant[0]["key"] == "asha|character"
    assert (meant[0]["match"], meant[0]["matched"]) == ("meaning", None)


@pytest.fixture
def events(monkeypatch):
    monkeypatch.setattr(graph, "get_event", lambda scope, key: next(
        (e for e in EVENTS if e.key == key), None))


def test_retrieve_without_embeddings(world, events):
    page = chunks.list_chunks(world)[0]
    mentions.insert_mentions([Mention(book_id=world, node_key="ravi|character", chunk_id=page.id,
                                      chunk_seq=0, quote="Ravi", char_start=0, char_end=4, page=1)])
    out = rag_tools.retrieve(world, "What did Ravi do?")

    assert out["semantic"] is False and out["events"] == []
    assert [e["key"] for e in out["entities"]] == ["ravi|character"]
    assert {(r["src"], r["rel"], r["dst"]) for r in out["relations"]} == {
        ("Ravi Sharma", "FRIEND_OF", "Asha"), ("Ravi Sharma", "LIVES_IN", "Delhi")}
    # found by keyword ("Ravi") and by the seed's mention; keyword is reported first
    assert out["passages"][0]["chunk_id"] == page.id and out["passages"][0]["match"] == "keyword"


def test_retrieve_with_embeddings(world, events, embeddings):
    index_world(world, embeddings)
    out = rag_tools.retrieve(world, "Who committed treachery?", limit=1)

    assert out["semantic"] is True
    assert out["entities"][0]["key"] == "asha|character"
    assert out["entities"][0]["match"] == "meaning"
    assert out["events"][0]["key"] == "event|1|0"  # "Asha betrays Meera"
    assert len(out["passages"]) == 1 and out["passages"][0]["chapter"] == "Chapter 2"
    assert "betrayed" in out["passages"][0]["text"]


def test_retrieve_is_registered():
    import asyncio

    from app.mcp_server.server import mcp

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert "book_id" in tools["retrieve"].inputSchema["required"]


# --- ingestion -----------------------------------------------------------------


def _fake_pipeline(monkeypatch, index):
    monkeypatch.setattr(ingestion, "generate_page_graphs",
                        lambda book_id: SimpleNamespace(blocked=[], failed={}))
    monkeypatch.setattr(ingestion, "merge_page_graphs",
                        lambda book_id: SimpleNamespace(root=object(), failed={}))
    monkeypatch.setattr(ingestion, "publish_book_graph", lambda book_id: {"entities": 1})
    monkeypatch.setattr(retrieval, "index_book", index)
    monkeypatch.setattr(ingestion, "build_communities", lambda book_id: {"communities": 3})
    parsed = SimpleNamespace(page_count=1, chapters=[])
    monkeypatch.setattr(ingestion, "chunk_book", lambda book_id, p: [])
    return parsed


def test_ingestion_records_vectors(mongo, monkeypatch):
    book = books.create_book(Book(title="Kim", content_hash="h"))
    parsed = _fake_pipeline(monkeypatch, lambda book_id: {"vectors": 12})
    ingestion.ingest_book(book.id, parsed)
    got = books.get_book(book.id)
    assert got.status == "ready" and got.stats["vectors"] == 12 and got.stats["communities"] == 3


def test_community_failure_leaves_book_ready(mongo, monkeypatch):
    book = books.create_book(Book(title="Kim", content_hash="h"))
    parsed = _fake_pipeline(monkeypatch, lambda book_id: {"vectors": 1})

    def broken(book_id):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(ingestion, "build_communities", broken)
    ingestion.ingest_book(book.id, parsed)
    got = books.get_book(book.id)
    assert got.status == "ready" and got.stats["vectors"] == 1  # embedding still ran


def test_embedding_failure_leaves_book_ready(mongo, monkeypatch):
    book = books.create_book(Book(title="Kim", content_hash="h"))

    def broken(book_id):
        raise RuntimeError("embedding API down")

    ingestion.ingest_book(book.id, _fake_pipeline(monkeypatch, broken))
    got = books.get_book(book.id)
    assert got.status == "ready" and "vectors" not in got.stats
