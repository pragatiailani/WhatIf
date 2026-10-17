# ruff: noqa: F811  (tests take the imported `world` fixture as a parameter)
from types import SimpleNamespace

import numpy as np
import pytest

from app import retrieval
from app.communities import detect as detect_mod
from app.communities import prompt, runner
from app.communities.prompt import BookOverview, CommunityReport
from app.mcp_server import graph_tools, rag_tools
from app.repo import books, communities, graph, vectors
from app.repo.models import Book, Community, GraphPayload
from tests.test_mcp_tools import world  # noqa: F401  (fixture)
from tests.test_retrieval import ConceptEmbeddings, events, index_world  # noqa: F401


def ent(key, name, desc=""):
    return {"key": key, "name": name, "type": "Character", "aliases": [name], "description": desc}


def rel(src, dst, rel_="FRIEND_OF", weight=2, desc=""):
    return {"src": src, "dst": dst, "rel": rel_, "weight": weight, "description": desc}


def event(key, pos, keys, summary, chapter="Chapter 1"):
    return {"key": key, "story_pos": pos, "chapter": chapter, "summary": summary,
            "participants": [{"key": k, "role": "actor"} for k in keys]}  # fmt: skip


# Two tight groups (farm animals, the humans) joined by one weak edge, plus a loose pair.
PAYLOAD = GraphPayload(
    entities=[
        ent("napoleon", "Napoleon", "A pig who takes power."),
        ent("snowball", "Snowball", "A pig who plans the windmill."),
        ent("boxer", "Boxer", "A loyal horse."),
        ent("squealer", "Squealer", "Napoleon's mouthpiece."),
        ent("jones", "Mr. Jones", "The drunken farmer."),
        ent("pilkington", "Mr. Pilkington", "A neighbouring farmer."),
        ent("frederick", "Mr. Frederick", "Another neighbour."),
        ent("moses", "Moses", "A raven."),
        ent("clover", "Clover", "A mare."),
    ],
    relations=[
        rel("napoleon", "snowball", "RIVAL_OF", 5, "They fight over the windmill."),
        rel("napoleon", "squealer", "LEADS", 4),
        rel("boxer", "napoleon", "TRUSTS", 3),
        rel("snowball", "boxer", "HELPS", 2),
        rel("jones", "pilkington", "FRIEND_OF", 4),
        rel("pilkington", "frederick", "RIVAL_OF", 4),
        rel("jones", "frederick", "MEETS", 2),
        rel("napoleon", "pilkington", "MEETS", 0.5),
        rel("moses", "clover", "TALKS_TO", 1),
    ],
    events=[
        event("e1", 0, ["napoleon", "snowball", "boxer"], "The animals drive out Mr. Jones."),
        event("e2", 2000, ["napoleon", "squealer"], "Napoleon seizes power.", "Chapter 5"),
        event("e3", 1000, ["jones", "pilkington"], "Jones drinks with Pilkington."),
        event("e4", 3000, ["boxer"], "Boxer works alone."),
    ],
)


# --- detection ---------------------------------------------------------------


def test_entity_graph_weights():
    g = detect_mod.entity_graph(PAYLOAD)
    assert g["napoleon"]["snowball"]["weight"] == 5 + 0.5  # relation + the event they share
    assert g["napoleon"]["squealer"]["weight"] == 4 + 0.5
    assert g["snowball"]["boxer"]["weight"] == 2 + 0.5
    assert not g.has_edge("boxer", "boxer")  # one-person events add nothing
    payload = GraphPayload(entities=[ent("a", "A")], relations=[rel("a", "ghost"), rel("a", "a")])
    assert detect_mod.entity_graph(payload).number_of_edges() == 0


def test_detect_groups():
    groups = detect_mod.detect(detect_mod.entity_graph(PAYLOAD))
    assert [set(g) for g in groups] == [
        {"napoleon", "snowball", "boxer", "squealer"},
        {"jones", "pilkington", "frederick"},
    ]  # the pair moses/clover is too small
    assert groups[0][0] == "napoleon"  # most connected member first
    assert detect_mod.detect(detect_mod.entity_graph(PAYLOAD)) == groups  # deterministic
    assert detect_mod.detect(detect_mod.entity_graph(GraphPayload())) == []


def test_detect_is_stable_across_processes():
    """String hashing changes per process; detection must not depend on it."""
    import os
    import subprocess
    import sys

    code = ("from app.communities.detect import detect, entity_graph;"
            "from tests.test_communities import PAYLOAD;"
            "print(detect(entity_graph(PAYLOAD)))")  # fmt: skip
    outputs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed}).stdout
        for seed in ("1", "2", "3")
    }  # fmt: skip
    assert len(outputs) == 1


def test_community_prompt_shows_the_group():
    members = ["napoleon", "snowball", "boxer", "squealer"]
    text = prompt.build_community_messages("Animal Farm", members, PAYLOAD)[1].content
    assert "Book: Animal Farm" in text and "- Napoleon (Character): A pig who takes power." in text
    assert "Napoleon RIVAL_OF Snowball: They fight over the windmill." in text
    assert "Pilkington" not in text  # relations leaving the group are not shown
    assert text.index("drive out Mr. Jones") < text.index("seizes power")  # story order
    assert "[Chapter 5] Napoleon seizes power." in text
    assert "Boxer works alone" not in text  # events need two members


# --- runner ------------------------------------------------------------------


class FakeLLM:
    """Stands in for the chat model: `with_structured_output` returns itself."""

    def __init__(self, blocked_if: str | None = None, fail: bool = False):
        self.prompts: list[str] = []
        self.blocked_if = blocked_if
        self.fail = fail

    def with_structured_output(self, schema, **_):
        assert schema in (CommunityReport, BookOverview)
        return self

    async def ainvoke(self, messages):
        text = messages[1].content
        self.prompts.append(text)
        if self.fail:
            raise RuntimeError("rate limited")
        if self.blocked_if and self.blocked_if in text and "## " not in text:
            raise RuntimeError("The response was filtered due to the content filter")
        if "## " in text:  # the overview prompt lists the groups as sections
            return BookOverview(title="A farm revolution", summary="The animals rebel.")
        first = text.split("\n- ")[1].split(" (")[0]
        return CommunityReport(title=f"{first}'s circle", summary=f"About {first}.")


@pytest.fixture
def built(mongo, monkeypatch):
    book = books.create_book(Book(title="Animal Farm", content_hash="h"))
    monkeypatch.setattr(runner, "root_level", lambda book_id: SimpleNamespace(graph=PAYLOAD))
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(merge_concurrency=2))
    writes = []
    monkeypatch.setattr(graph, "set_communities", lambda b, found, members: writes.append(
        (b, found, members)))
    return SimpleNamespace(book=book, writes=writes)


def test_build_communities(built):
    llm = FakeLLM()
    assert runner.build_communities(built.book.id, llm=llm) == {"communities": 2}

    found = communities.list_communities(built.book.id)
    assert [(c.cid, c.level, c.title) for c in found] == [
        (0, 1, "A farm revolution"), (1, 0, "Napoleon's circle"), (2, 0, "Mr. Pilkington's circle")]
    assert found[1].entity_keys[0] == "napoleon" and found[0].entity_keys == []
    assert "## Napoleon's circle" in llm.prompts[-1]  # the overview reads the group summaries

    (book_id, nodes, members), = built.writes
    assert book_id == built.book.id
    assert nodes == [{"cid": 1, "title": "Napoleon's circle"},
                     {"cid": 2, "title": "Mr. Pilkington's circle"}]
    assert {"key": "jones", "cid": 2} in members and len(members) == 7

    runner.build_communities(built.book.id, llm=FakeLLM())  # a rebuild replaces, not adds
    assert len(communities.list_communities(built.book.id)) == 3


def test_blocked_group_falls_back_to_names(built):
    runner.build_communities(built.book.id, llm=FakeLLM(blocked_if="Mr. Pilkington"))
    humans = communities.get_communities(built.book.id, [2])[0]
    # Pilkington is the best-connected member (two strong ties plus the link to Napoleon)
    assert humans.title == "Mr. Pilkington, Mr. Jones, Mr. Frederick"
    assert "drunken farmer" in humans.summary


def test_llm_errors_propagate(built):
    with pytest.raises(RuntimeError, match="rate limited"):
        runner.build_communities(built.book.id, llm=FakeLLM(fail=True))
    assert communities.list_communities(built.book.id) == []


def test_no_groups_no_overview(built, monkeypatch):
    lonely = GraphPayload(entities=[ent("a", "A")])
    monkeypatch.setattr(runner, "root_level", lambda book_id: SimpleNamespace(graph=lonely))
    llm = FakeLLM()
    assert runner.build_communities(built.book.id, llm=llm) == {"communities": 0}
    assert llm.prompts == [] and communities.list_communities(built.book.id) == []


# --- repo --------------------------------------------------------------------


def _store(book_id):
    communities.replace_book(book_id, [
        Community(book_id=book_id, cid=0, level=1, title="Overview", summary="The whole story."),
        Community(book_id=book_id, cid=1, title="Betrayal circle", summary="Asha and Meera fall out.",
                  entity_keys=["asha|character", "meera|character"]),
        Community(book_id=book_id, cid=2, title="Ravi's journey", summary="Ravi leaves Delhi.",
                  entity_keys=["ravi|character", "delhi|place", "asha|character"]),
    ])  # fmt: skip


def test_repo_lookups(mongo):
    _store("b")
    assert [c.cid for c in communities.get_communities("b", [2, 9, 0])] == [2, 0]
    assert [c.cid for c in communities.for_entities("b", ["asha|character", "meera|character"])] == [1, 2]
    assert communities.for_entities("b", ["nobody"]) == []


# --- tools -------------------------------------------------------------------


def test_search_communities_falls_back_to_overview(world):
    _store(world)
    out = graph_tools.search_communities(world, "what is this book about")
    assert [c["cid"] for c in out["communities"]] == [0, 1, 2] and "note" in out
    keyword = graph_tools.search_communities(world, "betrayal")
    assert [c["cid"] for c in keyword["communities"]] == [1] and "note" not in keyword


@pytest.fixture
def embeddings(monkeypatch):
    model = ConceptEmbeddings()
    monkeypatch.setattr(retrieval, "get_embeddings", lambda: model)
    return model


def test_search_communities_by_meaning(world, embeddings):
    _store(world)
    assert retrieval.index_communities(world) == 3
    out = graph_tools.search_communities(world, "treachery", limit=1)  # no keyword match
    assert [c["cid"] for c in out["communities"]] == [1]
    assert vectors.load(world, "community")[0] == ["0", "1", "2"]


def test_retrieve_includes_seed_communities(world, events, embeddings):
    _store(world)
    index_world(world, embeddings)
    out = rag_tools.retrieve(world, "Where did Ravi go?")
    assert [c["title"] for c in out["communities"]][0] == "Ravi's journey"
    assert len(out["communities"]) <= 2


def test_index_book_embeds_communities(mongo, embeddings, monkeypatch):
    _store("b")
    monkeypatch.setattr(retrieval, "root_level", lambda book_id: SimpleNamespace(
        graph=GraphPayload()))
    assert retrieval.index_book("b") == {"vectors": 3}
    assert np.allclose(np.linalg.norm(vectors.load("b", "community")[1], axis=1), 1)
