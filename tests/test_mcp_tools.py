import asyncio

import pytest

from app.mcp_server import graph_tools, session_tools, text_tools
from app.mcp_server.matching import match_entities, resolve_entity
from app.mcp_server.server import mcp
from app.repo import books, chunks, communities, graph, sessions
from app.repo.models import (
    Book,
    ChatSession,
    Chunk,
    Community,
    GraphEntity,
    GraphEvent,
    GraphRelation,
    Message,
    PageSpan,
)


def ent(key, name, type_="Character", aliases=(), count=1):
    return GraphEntity(key=key, name=name, type=type_, aliases=list(aliases), mention_count=count)


ENTITIES = [
    ent("ravi|character", "Ravi Sharma", aliases=["Ravi", "Mr. Sharma"], count=9),
    ent("asha|character", "Asha", count=5),
    ent("meera|character", "Meera", count=3),
    ent("delhi|place", "Delhi", "Place", count=4),
    ent("zed|character", "Zed", count=1),
]
RELATIONS = [
    GraphRelation(id="r1", src="ravi|character", dst="asha|character", rel="FRIEND_OF", weight=3),
    GraphRelation(id="r2", src="asha|character", dst="meera|character", rel="BETRAYS", weight=2),
    GraphRelation(id="r3", src="ravi|character", dst="delhi|place", rel="LIVES_IN", weight=1),
]
BY_KEY = {e.key: e for e in ENTITIES}
EVENTS = [
    GraphEvent(key="event|0|0", summary="Ravi leaves Delhi", story_pos=0, chapter="Chapter 1", first_page=1),
    GraphEvent(key="event|1|0", summary="Asha betrays Meera", story_pos=1000, chapter="Chapter 2", first_page=2),
]


@pytest.fixture
def world(mongo, monkeypatch):
    book = books.create_book(Book(title="Kim", content_hash="h"))
    spans = lambda p: [PageSpan(page=p, char_start=0, char_end=10)]  # noqa: E731
    chunks.insert_chunks(
        [
            Chunk(book_id=book.id, seq=0, chapter="Chapter 1", page_spans=spans(1),
                  text="Ravi left Delhi at dawn, carrying only a battered suitcase."),
            Chunk(book_id=book.id, seq=1, chapter="Chapter 2", page_spans=spans(2),
                  text="Asha betrayed Meera. Meera never forgave the betrayal."),
        ]
    )

    def expand(scope, keys, rel_types=None):
        out = []
        for r in RELATIONS:
            for here, there in ((r.src, r.dst), (r.dst, r.src)):
                if here in keys and (not rel_types or r.rel in rel_types):
                    out.append((here, r, BY_KEY[there]))
        return out

    monkeypatch.setattr(graph, "entity_index", lambda scope: [
        {"key": e.key, "name": e.name, "type": e.type, "aliases": e.aliases,
         "mention_count": e.mention_count} for e in ENTITIES])
    monkeypatch.setattr(graph, "get_entities", lambda scope, keys: [BY_KEY[k] for k in keys if k in BY_KEY])
    monkeypatch.setattr(graph, "get_entity", lambda scope, key: BY_KEY.get(key))
    monkeypatch.setattr(graph, "expand", expand)
    monkeypatch.setattr(
        graph, "entity_relations",
        lambda scope, key, limit=50, rel_types=None: [
            (r, other) for _, r, other in expand(scope, [key], rel_types)][:limit])
    monkeypatch.setattr(graph, "entity_events", lambda scope, key: (
        [(EVENTS[0], "actor")] if key == "ravi|character" else []))
    monkeypatch.setattr(graph, "chapter_events", lambda scope, chapter: [
        (e, ["ravi|character"] if e is EVENTS[0] else ["asha|character", "meera|character"])
        for e in EVENTS if e.chapter == chapter])
    return book.id


# --- matching ------------------------------------------------------------


def index():
    return [{"key": e.key, "name": e.name, "type": e.type, "aliases": e.aliases,
             "mention_count": e.mention_count} for e in ENTITIES]


def test_match_entities_by_name_alias_and_partial():
    hits = match_entities("Why did Ravi betray Asha?", index(), 10)
    assert [h["key"] for h in hits][:2] == ["ravi|character", "asha|character"]
    assert hits[0]["matched"] == "Ravi"  # alias is a whole-phrase hit
    assert match_entities("tell me about Sharma", index(), 10)[0]["key"] == "ravi|character"
    assert match_entities("what is the weather", index(), 10) == []


def test_resolve_entity():
    assert resolve_entity("ravi|character", index())["name"] == "Ravi Sharma"
    assert resolve_entity("mr sharma", index())["key"] == "ravi|character"
    with pytest.raises(ValueError, match="no entity"):
        resolve_entity("Gandalf", index())
    twins = index() + [{"key": "asha|place", "name": "Asha", "type": "Place", "aliases": [],
                        "mention_count": 1}]
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_entity("Asha", twins)


# --- graph tools ---------------------------------------------------------


def test_unknown_book(world):
    with pytest.raises(ValueError, match="unknown book"):
        graph_tools.find_entities("nope", "Ravi")


def test_find_entities(world):
    out = graph_tools.find_entities(world, "Where did Ravi go?")
    assert out["entities"][0]["key"] == "ravi|character"


def test_neighbors_hops(world):
    one = graph_tools.get_neighbors(world, "Ravi")
    assert {n["entity"]["key"] for n in one["neighbors"]} == {"asha|character", "delhi|place"}
    two = graph_tools.get_neighbors(world, "Ravi", hops=2)
    meera = [n for n in two["neighbors"] if n["entity"]["key"] == "meera|character"][0]
    assert (meera["hop"], meera["via"], meera["rel"]) == (2, "asha|character", "BETRAYS")
    with pytest.raises(ValueError, match="hops"):
        graph_tools.get_neighbors(world, "Ravi", hops=3)


def test_find_path(world):
    out = graph_tools.find_path(world, "Ravi", "Meera")
    assert out["found"] and out["hops"] == 2
    assert [(s["from"], s["to"]) for s in out["path"]] == [("Ravi Sharma", "Asha"), ("Asha", "Meera")]
    assert graph_tools.find_path(world, "Ravi", "Zed") == {
        "found": False, "max_hops": 6, "path": []}


def test_get_relations_filter(world):
    out = graph_tools.get_relations(world, "Asha", "betrays")
    assert [r["other"]["key"] for r in out["relations"]] == ["meera|character"]
    assert out["relations"][0]["direction"] == "out"
    assert len(graph_tools.get_relations(world, "Asha")["relations"]) == 2
    with pytest.raises(ValueError, match="Valid types"):
        graph_tools.get_relations(world, "Asha", "frenemies")


def test_search_communities(world):
    assert "note" in graph_tools.search_communities(world, "betrayal")
    communities.insert_communities([
        Community(book_id=world, cid=1, title="Betrayal circle", summary="Asha and Meera"),
        Community(book_id=world, cid=2, title="Delhi", summary="Ravi's home city"),
    ])
    out = graph_tools.search_communities(world, "betrayal among friends")
    assert [c["cid"] for c in out["communities"]] == [1]


# --- text tools ----------------------------------------------------------


def test_get_chunks_is_scoped_to_book(world):
    other = chunks.list_chunks(world)
    chunks.insert_chunks([Chunk(book_id="other", seq=0, text="secret")])
    foreign = chunks.list_chunks("other")[0].id
    out = text_tools.get_chunks(world, [other[0].id, foreign, "nope"])
    assert [c["seq"] for c in out["chunks"]] == [0]
    assert out["missing"] == [foreign, "nope"]
    with pytest.raises(ValueError, match="at most"):
        text_tools.get_chunks(world, ["x"] * 11)


def test_search_chunks(world):
    out = text_tools.search_chunks(world, "who betrayed Meera")
    assert out["results"][0]["chapter"] == "Chapter 2"
    assert "betrayed" in out["results"][0]["snippet"]
    assert text_tools.search_chunks(world, "suitcase", chapter="1")["results"][0]["seq"] == 0
    assert text_tools.search_chunks(world, "suitcase", chapter="Chapter 2")["results"] == []
    with pytest.raises(ValueError, match="no chapter"):
        text_tools.search_chunks(world, "suitcase", chapter="Chapter 9")


def test_timeline_and_chapter_summary(world):
    ids = {c.seq: c.id for c in chunks.list_chunks(world)}
    timeline = text_tools.get_timeline(world, "Ravi")
    assert timeline["events"][0]["summary"] == "Ravi leaves Delhi"
    assert timeline["events"][0]["chunk_id"] == ids[0] and timeline["events"][0]["role"] == "actor"

    summary = text_tools.get_chapter_summary(world, "Chapter 2")
    assert summary["chunk_ids"] == [ids[1]]
    assert summary["events"][0]["main_participants"] == ["Asha", "Meera"]
    assert {e["key"] for e in summary["main_entities"]} == {"asha|character", "meera|character"}


# --- session tools -------------------------------------------------------


def test_session_tools(mongo):
    s = sessions.create_session(ChatSession(book_id="b", title="Chat", summary="Talked about Ravi."))
    for i in range(3):
        sessions.add_message(Message(session_id=s.id, role="user", content=f"m{i}"))
    sessions.add_message(Message(session_id="other", role="user", content="not mine"))

    assert [m["content"] for m in session_tools.get_history(s.id, 2)["messages"]] == ["m1", "m2"]
    assert session_tools.get_session_summary(s.id)["summary"] == "Talked about Ravi."

    mid = sessions.last_messages(s.id, 1)[0].id
    assert session_tools.save_note(s.id, " keep this ", mid)["saved"]
    assert [n.text for n in sessions.list_notes(s.id)] == ["keep this"]
    with pytest.raises(ValueError, match="not in this session"):
        session_tools.save_note(s.id, "x", sessions.last_messages("other", 1)[0].id)
    with pytest.raises(ValueError, match="empty"):
        session_tools.save_note(s.id, "  ")
    with pytest.raises(ValueError, match="unknown session"):
        session_tools.get_history("nope")


# --- server --------------------------------------------------------------


def test_server_registers_all_tools():
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert set(tools) == {
        "retrieve", "find_entities", "get_neighbors", "find_path", "get_relations", "search_communities",
        "get_chunks", "search_chunks", "get_chapter_summary", "get_timeline",
        "get_history", "get_session_summary", "save_note",
        "get_dependents", "get_state_at", "start_draft", "get_open_items", "resolve_item",
        "verify_draft", "commit_draft", "discard_draft", "read_chapter", "generate_passage",
    }
    assert "book_id" in tools["find_path"].inputSchema["required"]
    assert "session_id" in tools["save_note"].inputSchema["required"]
