import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, crypto
from app.api import reading
from app.main import app
from app.repo import books, communities, graph, sessions, users
from app.repo.models import Book, Community, GraphEntity, GraphEvent, GraphRelation, Message, User

SETTINGS = SimpleNamespace(auth_secret="test-secret", secrets_key="")


@pytest.fixture
def client(mongo, monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    monkeypatch.setattr(auth, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(crypto, "get_settings", lambda: SETTINGS)
    with TestClient(app) as c:
        yield c


def user(email="ravi@example.com") -> tuple[User, dict]:
    u = users.create_user(User(name="Ravi", email=email, password_hash="x"))
    return u, {"Authorization": f"Bearer {auth.issue_token(u.id)}"}


def book(**kw) -> Book:
    fields = {"title": "Kim", "content_hash": "h", "visibility": "public", "status": "ready", **kw}
    return books.create_book(Book(**fields))


def events_of(res) -> list[tuple[str, dict]]:
    out = []
    for block in res.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


REASONING = {
    "chain": [{"kind": "relation", "id": "r1", "src_key": "ravi", "dst_key": "asha", "src": "Ravi",
               "dst": "Asha", "rel": "OWES", "description": "", "why": "Ravi owes Asha."}],
    "conclusion": "So he left.",
}


def fake_stream(monkeypatch, *, fail=False):
    """Replace the agent: phases and tokens, then a saved turn like the real one."""
    seen = []

    async def stream(book_id, session_id, question, **_):
        seen.append((book_id, session_id, question))
        yield "phase", "Searching the graph and the text…"
        if fail:
            raise RuntimeError("LLM exploded")
        yield "step", {"tool": "retrieve", "args": {"query": question}, "why": "Look for Ravi.",
                       "found": "1 entities (Ravi)", "error": False}
        yield "token", "A "
        yield "token", "debt."
        yield "reasoning", REASONING
        u = sessions.add_message(Message(session_id=session_id, role="user", content=question))
        a = sessions.add_message(Message(
            session_id=session_id, role="assistant", content="A debt.",
            citations=[{"chunk_id": "c1", "chapter": "Chapter 1", "page_start": 3, "page_end": 3,
                        "seq": 0, "quote": "Ravi left Delhi because of the debt."}],
            trace=[{"tool": "retrieve", "args": {"query": question}, "error": False}],
            graph_refs={"seeds": ["ravi"], "expanded": ["asha"], "edges": ["r1"], "communities": [3]},
            reasoning=REASONING,
        ))  # fmt: skip
        yield "done", {"answer": "A debt.", "actions": [], "user_message": u, "assistant_message": a}

    monkeypatch.setattr(reading.book_qa, "stream", stream)
    return seen


# --- graph and storylines ------------------------------------------------------


def test_graph(client, monkeypatch):
    b = book()
    communities.insert_communities([Community(book_id=b.id, cid=1, title="Friends", summary="…")])
    monkeypatch.setattr(graph, "book_graph", lambda scope: (
        [GraphEntity(key="ravi", name="Ravi", type="Character", embedding=[0.1], community_id=1)],
        [(GraphEvent(key="e1", summary="Ravi leaves", story_pos=0, chapter="Chapter 1"), ["ravi"])],
        [GraphRelation(id="r1", src="ravi", dst="asha", rel="FRIEND_OF")],
    ))
    _, h = user()
    out = client.get(f"/books/{b.id}/graph", headers=h).json()
    assert out["entities"][0]["key"] == "ravi" and "embedding" not in out["entities"][0]
    assert out["entities"][0]["community_id"] == 1
    assert out["events"] == [{"key": "e1", "summary": "Ravi leaves", "story_pos": 0, "chapter": "Chapter 1",
                              "first_page": None, "sid": None, "involves": ["ravi"]}]
    assert out["relations"][0] == {"id": "r1", "src": "ravi", "dst": "asha", "rel": "FRIEND_OF",
                                   "description": "", "weight": 1.0, "sid": None}
    assert out["communities"] == [{"cid": 1, "title": "Friends", "summary": "…"}]
    assert out["hidden"] == []


def test_book_access(client):
    owner, owner_h = user()
    other, other_h = user("x@example.com")
    private = book(visibility="private", owner_id=owner.id)
    assert client.get(f"/books/{private.id}/storylines", headers=owner_h).json() == []
    assert client.get(f"/books/{private.id}/storylines", headers=other_h).status_code == 404
    assert client.get(f"/books/{private.id}/storylines").status_code == 401
    shared = book(visibility="private", owner_id=owner.id, shared_with=[other.id])
    assert client.get(f"/books/{shared.id}/storylines", headers=other_h).status_code == 200

    unready = book(status="merging")
    assert client.get(f"/books/{unready.id}/graph", headers=owner_h).status_code == 409
    assert client.get(f"/books/{book().id}/graph?storyline_id=s1", headers=owner_h).status_code == 404


# --- chat ----------------------------------------------------------------------


def test_ask_streams_and_saves(client, monkeypatch):
    seen = fake_stream(monkeypatch)
    u, h = user()
    b = book()

    res = client.post(f"/books/{b.id}/chat", json={"text": "Why did Ravi leave?", "storyline_id": None},
                      headers=h)
    assert res.headers["content-type"].startswith("text/event-stream")
    evs = events_of(res)
    assert [e for e, _ in evs] == ["phase", "step", "token", "token", "reasoning", "done"]
    assert evs[4][1] == REASONING
    assert evs[0][1] == {"label": "Searching the graph and the text…"}
    assert evs[1][1] == {"label": "Searching the graph and the text", "tool": "retrieve",
                         "detail": 'query: "Why did Ravi leave?"', "why": "Look for Ravi.",
                         "found": "1 entities (Ravi)"}
    assert "".join(d["text"] for e, d in evs if e == "token") == "A debt."

    turn = evs[-1][1]
    assert turn["user"]["content"] == "Why did Ravi leave?" and turn["storyline"] is None
    a = turn["assistant"]
    assert a["citations"] == [{"chapter": "Chapter 1", "page": 3,
                               "quote": "Ravi left Delhi because of the debt."}]
    assert a["route"] == "local" and a["trace"]["communities"] == []  # retrieve's are not "global"
    assert a["trace"]["reasoning"] == REASONING
    assert a["trace"]["seeds"] == ["ravi"] and a["trace"]["edges"] == ["r1"] and a["trace"]["hops"] == 1
    # a turn saved without why/found (older messages) still reads
    assert a["trace"]["steps"] == [{"label": "Searching the graph and the text", "tool": "retrieve",
                                    "detail": 'query: "Why did Ravi leave?"', "why": "", "found": ""}]

    assert turn["chat"]["id"] == seen[0][1] and turn["chat"]["storyline_id"] is None

    # without a chat_id the question goes to the latest chat
    client.post(f"/books/{b.id}/chat", json={"text": "And then?"}, headers=h)
    assert seen[0][1] == seen[1][1]
    assert sessions.get_session(seen[0][1]).user_id == u.id

    history = client.get(f"/books/{b.id}/messages", headers=h).json()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[1]["trace"]["rewritten_question"] == "Why did Ravi leave?"
    assert history[3]["trace"]["rewritten_question"] == "And then?"
    assert "citations" not in history[0]


def test_ask_errors(client, monkeypatch):
    _, h = user()
    b = book()
    assert client.post(f"/books/{b.id}/chat", json={"text": "hi"}).status_code == 401
    assert client.post(f"/books/{b.id}/chat", json={"text": ""}, headers=h).status_code == 422
    assert client.post(f"/books/{b.id}/chat", json={"text": "hi", "storyline_id": "s1"},
                       headers=h).status_code == 404  # no such storyline
    assert client.post(f"/books/{book(status='extracting').id}/chat", json={"text": "hi"},
                       headers=h).status_code == 409

    fake_stream(monkeypatch, fail=True)
    evs = events_of(client.post(f"/books/{b.id}/chat", json={"text": "hi"}, headers=h))
    assert evs[-1] == ("error", {"detail": reading.ANSWER_FAILED})  # no internals leak


def test_history_is_per_user(client, monkeypatch):
    fake_stream(monkeypatch)
    _, h = user()
    _, other_h = user("x@example.com")
    b = book()
    assert client.get(f"/books/{b.id}/messages").status_code == 401
    assert client.get(f"/books/{b.id}/messages", headers=h).json() == []
    client.post(f"/books/{b.id}/chat", json={"text": "Q"}, headers=h)
    assert len(client.get(f"/books/{b.id}/messages", headers=h).json()) == 2
    assert client.get(f"/books/{b.id}/messages", headers=other_h).json() == []
    assert client.get(f"/books/{b.id}/messages?storyline_id=s1", headers=h).json() == []


def test_many_chats_per_book(client, monkeypatch):
    seen = fake_stream(monkeypatch)
    _, h = user()
    _, other_h = user("x@example.com")
    b = book()
    ask = lambda **body: events_of(client.post(f"/books/{b.id}/chat", json={"text": "Q", **body}, headers=h))[-1][1]

    first = ask()["chat"]["id"]
    second = ask(new_chat=True)["chat"]["id"]
    assert first != second
    assert ask(chat_id=first)["chat"]["id"] == first  # an older chat can be continued

    chats = client.get(f"/books/{b.id}/chats", headers=h).json()
    assert [c["id"] for c in chats] == [first, second]  # most recently active first
    assert client.get(f"/books/{b.id}/chats", headers=other_h).json() == []

    assert len(client.get(f"/books/{b.id}/messages?chat_id={first}", headers=h).json()) == 4
    assert len(client.get(f"/books/{b.id}/messages?chat_id={second}", headers=h).json()) == 2
    assert client.get(f"/books/{b.id}/messages?chat_id={first}", headers=other_h).status_code == 404

    # a chat can only be continued by its owner, on its own book and storyline
    res = client.post(f"/books/{b.id}/chat", json={"text": "Q", "chat_id": first}, headers=other_h)
    assert res.status_code == 404
    res = client.post(f"/books/{book().id}/chat", json={"text": "Q", "chat_id": first}, headers=h)
    assert res.status_code == 404
    assert len(seen) == 3


def test_global_route_when_communities_used():
    m = Message(session_id="s", role="assistant", content="Themes.",
                trace=[{"tool": "search_communities", "args": {"query": "themes"}, "error": True}],
                graph_refs={"communities": [0, 2]})
    out = reading.message_out(m, "What are the themes?")
    assert out["route"] == "global" and out["trace"]["communities"] == [0, 2]
    assert out["trace"]["steps"][0]["label"] == "Reading the big picture (failed)"
    assert out["trace"]["hops"] == 0
