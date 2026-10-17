"""The routes the web UI calls: graph, storylines, and streamed chat.

Reuses the storyline test world (in-memory graph, mongomock). The agent is replaced by a function
that saves messages the way the real one does, so these tests cover the API and its shaping.
"""

import json

import pytest
from fastapi.testclient import TestClient
from test_storyline import RAVI, VIK, world  # noqa: F401  (world is a fixture)

from app import auth
from app.api import workspace
from app.config import get_settings
from app.mcp_server import storyline_tools as tools
from app.repo import books, sessions, storylines
from app.repo.models import Message
from app.storyline.schema import Fact


@pytest.fixture
def api(world, mongo, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    monkeypatch.setenv("AUTH_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "get_settings", get_settings)
    mongo["books"].update_one({"_id": world.book}, {"$set": {"status": "ready", "visibility": "public"}})
    from app.main import app

    world.agent_calls = []
    world.reply = "He left because of a debt."
    world.on_run = None

    async def run(book_id, session_id, text, on_progress=None, **_):
        world.agent_calls.append((session_id, text))
        if on_progress:
            on_progress("Reading the passages…")
        if world.on_run:
            world.on_run(book_id, session_id)
        chunk_id = world.chunk_ids[0]
        asked = sessions.add_message(Message(session_id=session_id, role="user", content=text))
        replied = sessions.add_message(Message(
            session_id=session_id, role="assistant", content=world.reply, route="local",
            citations=[{"chunk_id": chunk_id, "chapter": "Chapter 1", "page_start": 1, "page_end": 1, "seq": 0}],
            trace=[{"tool": "get_chunks", "args": {"chunk_ids": [chunk_id]}, "error": False}],
            retrieval={"seeds": [RAVI], "expanded": ["asha|character"], "hops": 1},
        ))  # fmt: skip
        return {"message_ids": {"user": asked.id, "assistant": replied.id}}

    monkeypatch.setattr(workspace, "get_agent", lambda name: type("A", (), {"run": staticmethod(run)}))
    with TestClient(app) as client:
        world.client = client
        yield world
    get_settings.cache_clear()


def resolve_all(book_id, session_id):
    """Decide the whole ripple queue of the scenario, in the given chat."""
    for ref, action, extra in (("e2", "rewrite", {"new_summary": "Vik wounds Ravi", "affects": ["Asha"]}),
                               ("r1", "hide", {}), ("e3", "rewrite", {"new_summary": "Asha keeps vigil"}),
                               ("e4", "keep", {})):  # fmt: skip
        tools.resolve_item(book_id, session_id, ref, action, **extra)


def sign_up(client, email="ravi@example.com"):
    res = client.post("/auth/signup", json={"name": "Ravi", "email": email, "password": "secret-pass"})
    return {"Authorization": f"Bearer {res.json()['token']}"}


def sse(res):
    """The events of an SSE response as [(event, data)]."""
    out = []
    for block in res.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


# --- the library ---------------------------------------------------------


def test_the_library_route_is_the_books_one_not_a_stripped_down_list(api):
    [book] = api.client.get("/books?scope=public").json()
    assert book["id"] == api.book and "cover_hue" in book and book["visibility"] == "public"


# --- graph ---------------------------------------------------------------


def test_canon_graph_has_entities_events_with_participants_and_relations(api):
    g = api.client.get(f"/books/{api.book}/graph").json()
    assert {e["key"] for e in g["entities"]} == {RAVI, "asha|character", VIK}
    assert g["events"][1] == {"key": "e2", "summary": "Vik kills Ravi", "story_pos": 1000.0,
                              "chapter": "Chapter 2", "first_page": 2, "sid": None,
                              "involves": [VIK, RAVI]}  # fmt: skip
    assert {r["rel"] for r in g["relations"]} == {"FRIEND_OF", "KILLS"} and g["hidden"] == []
    assert set(g["entities"][0]) >= {"key", "name", "type", "aliases", "mention_count", "community_id", "sid"}


def test_a_book_that_is_not_ready_has_an_empty_graph(api, mongo):
    mongo["books"].update_one({"_id": api.book}, {"$set": {"status": "merging"}})
    g = api.client.get(f"/books/{api.book}/graph").json()
    assert g["entities"] == [] and g["events"] == []


def test_a_storyline_graph_shows_its_changes_and_lists_what_it_hid(api):
    headers = sign_up(api.client)
    sl = api.client.post(f"/books/{api.book}/storylines", json={"title": "Ravi lives", "premise": "p"}, headers=headers).json()
    api.client.post(f"/books/{api.book}/chat", json={"storyline_id": sl["id"], "text": "go"}, headers=headers)
    session = sessions.find_session(sl["owner_id"], api.book, sl["id"])
    tools.start_draft(api.book, session.id, "e2", "Ravi survives", [Fact(entity="Ravi", attribute="status", value="alive")])
    for ref, action, extra in (("e2", "rewrite", {"new_summary": "Vik wounds Ravi"}), ("r1", "hide", {}),
                               ("e3", "keep", {}), ("e4", "keep", {})):  # fmt: skip
        tools.resolve_item(api.book, session.id, ref, action, **extra)

    g = api.client.get(f"/books/{api.book}/graph?storyline_id={sl['id']}", headers=headers).json()
    assert {"e2", "r1"} <= set(g["hidden"])
    assert any(e["sid"] == sl["id"] and e["summary"] == "Vik wounds Ravi" for e in g["events"])
    assert any(e["key"] == "e2" for e in g["events"])  # hidden items are listed, not removed
    canon = api.client.get(f"/books/{api.book}/graph").json()
    assert canon["hidden"] == [] and all(e["sid"] is None for e in canon["events"])


# --- storylines ----------------------------------------------------------


def test_storyline_lifecycle_and_visibility(api):
    owner, other = sign_up(api.client), sign_up(api.client, "asha@example.com")
    made = api.client.post(f"/books/{api.book}/storylines", json={"title": "Ravi lives", "premise": "What if?"}, headers=owner)
    assert made.status_code == 201
    sl = made.json()
    assert (sl["title"], sl["premise"], sl["visibility"], sl["owner_name"], sl["head_op_seq"]) == ("Ravi lives", "What if?", "private", "Ravi", 0)

    assert api.client.get(f"/storylines/{sl['id']}", headers=owner).status_code == 200
    assert api.client.get(f"/storylines/{sl['id']}", headers=other).status_code == 404  # private
    assert api.client.get(f"/storylines/{sl['id']}").status_code == 404
    assert api.client.get("/storylines?scope=public").json() == []

    patched = api.client.patch(f"/storylines/{sl['id']}", json={"title": "Ravi survives", "visibility": "public"}, headers=owner).json()
    assert patched["title"] == "Ravi survives" and patched["visibility"] == "public"
    assert api.client.patch(f"/storylines/{sl['id']}", json={"title": "x"}, headers=other).status_code == 403
    assert [s["id"] for s in api.client.get("/storylines?scope=public").json()] == [sl["id"]]
    assert [s["id"] for s in api.client.get(f"/books/{api.book}/storylines", headers=other).json()] == [sl["id"]]
    assert [s["id"] for s in api.client.get("/storylines?scope=mine", headers=owner).json()] == [sl["id"]]
    assert api.client.get("/storylines?scope=mine").status_code == 401


def test_creating_a_storyline_needs_sign_in_and_a_ready_book(api, mongo):
    assert api.client.post(f"/books/{api.book}/storylines", json={"title": "x"}).status_code == 401
    mongo["books"].update_one({"_id": api.book}, {"$set": {"status": "merging"}})
    res = api.client.post(f"/books/{api.book}/storylines", json={"title": "x"}, headers=sign_up(api.client))
    assert res.status_code == 409


def test_unbuilt_features_say_so_instead_of_pretending(api):
    owner = sign_up(api.client)
    sl = api.client.post(f"/books/{api.book}/storylines", json={"title": "t"}, headers=owner).json()
    base = f"/storylines/{sl['id']}"
    assert api.client.get(f"{base}/checkpoints", headers=owner).json() == []
    for path in (f"{base}/checkpoints", f"{base}/checkpoints/c1/restore", f"{base}/fork", f"{base}/star"):
        res = api.client.post(path, headers=owner)
        assert res.status_code == 501 and "not available yet" in res.json()["detail"]


# --- chat ----------------------------------------------------------------


def test_chat_streams_phase_tokens_and_a_done_turn(api):
    headers = sign_up(api.client)
    res = api.client.post(f"/books/{api.book}/chat", json={"storyline_id": None, "text": "Why did Ravi leave?"}, headers=headers)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = sse(res)
    kinds = [k for k, _ in events]
    assert kinds[0] == "phase" and kinds[-1] == "done" and "token" in kinds
    assert [d["label"] for k, d in events if k == "phase"] == ["Opening the book…", "Reading the passages…"]
    assert "".join(d["text"] for k, d in events if k == "token") == api.reply

    turn = events[-1][1]
    assert turn["storyline"] is None and len(turn["graph"]["entities"]) == 3
    assert (turn["user"]["role"], turn["user"]["content"]) == ("user", "Why did Ravi leave?")
    reply = turn["assistant"]
    assert reply["route"] == "local" and reply["content"] == api.reply
    assert reply["citations"] == [{"chapter": "Chapter 1", "page": 1, "quote": "Ravi met Asha."}]
    assert reply["trace"]["seeds"] == [RAVI] and reply["trace"]["expanded"] == ["asha|character"]
    assert reply["trace"]["steps"][0]["label"] == "Reading the passages"
    assert "diff" not in reply


def test_history_returns_the_saved_turns_and_chat_reuses_one_session(api):
    headers = sign_up(api.client)
    assert api.client.get(f"/books/{api.book}/messages", headers=headers).json() == []
    for text in ("first", "second"):
        api.client.post(f"/books/{api.book}/chat", json={"text": text}, headers=headers)
    assert len({sid for sid, _ in api.agent_calls}) == 1
    history = api.client.get(f"/books/{api.book}/messages", headers=headers).json()
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "first"), ("assistant", api.reply), ("user", "second"), ("assistant", api.reply)]  # fmt: skip
    assert history[1]["trace"]["rewritten_question"] == "first"  # the question before the answer
    assert history[1]["citations"][0]["quote"] == "Ravi met Asha."
    assert api.client.get(f"/books/{api.book}/messages").json() == []  # signed out: nothing


def test_the_turn_that_makes_the_edit_reports_the_diff_and_the_new_storyline(api, monkeypatch):
    headers = sign_up(api.client)
    started = {}

    def edit(book_id, session_id):
        tools.start_draft(book_id, session_id, "e2", "Ravi survives",
                          [Fact(entity="Ravi", attribute="status", value="alive")])  # fmt: skip
        resolve_all(book_id, session_id)
        started["session"] = session_id

    api.on_run = edit
    events = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "What if Ravi lived?"}, headers=headers))
    turn = events[-1][1]
    sl = turn["storyline"]
    assert sl["title"] == "Ravi survives" and sl["head_op_seq"] > 0
    assert any(e["sid"] == sl["id"] for e in turn["graph"]["events"]) and "e2" in turn["graph"]["hidden"]
    diff = turn["assistant"]["diff"]
    assert diff["op_seq"] == sl["head_op_seq"] and diff["divergence_pos"] == 1000.0
    assert len(diff["added"]) == 2 and {"e2", "e3"} <= set(diff["hidden"])  # two rewrites
    # saved, so the history shows it after a reload
    history = api.client.get(f"/books/{api.book}/messages?storyline_id={sl['id']}", headers=headers).json()
    assert history[-1]["diff"] == diff

    # the canon chat is a fresh session now; the storyline keeps the conversation that made it
    assert api.client.get(f"/books/{api.book}/messages", headers=headers).json() == []
    api.on_run = None
    again = sse(api.client.post(f"/books/{api.book}/chat", json={"storyline_id": sl["id"], "text": "continue"}, headers=headers))
    assert again[-1][1]["storyline"]["id"] == sl["id"] and "diff" not in again[-1][1]["assistant"]
    assert api.agent_calls[-1][0] == started["session"]  # same chat, bound to the storyline


def test_chat_errors_before_streaming_are_normal_http_errors(api, mongo):
    owner, other = sign_up(api.client), sign_up(api.client, "asha@example.com")
    assert api.client.post(f"/books/{api.book}/chat", json={"text": "hi"}).status_code == 401
    assert api.client.post("/books/nope/chat", json={"text": "hi"}, headers=owner).status_code == 404
    assert api.client.post(f"/books/{api.book}/chat", json={"text": ""}, headers=owner).status_code == 422
    sl = api.client.post(f"/books/{api.book}/storylines", json={"title": "t"}, headers=owner).json()
    assert api.client.post(f"/books/{api.book}/chat", json={"storyline_id": sl["id"], "text": "hi"}, headers=other).status_code == 404
    api.client.patch(f"/storylines/{sl['id']}", json={"visibility": "public"}, headers=owner)
    res = api.client.post(f"/books/{api.book}/chat", json={"storyline_id": sl["id"], "text": "hi"}, headers=other)
    assert res.status_code == 403  # readable, but only its owner edits it
    mongo["books"].update_one({"_id": api.book}, {"$set": {"status": "merging"}})
    assert api.client.post(f"/books/{api.book}/chat", json={"text": "hi"}, headers=owner).status_code == 409


def test_an_agent_failure_becomes_an_error_event_with_a_reference(api, caplog):
    headers = sign_up(api.client)

    def explode(book_id, session_id):
        raise RuntimeError("secret internal detail")

    api.on_run = explode
    events = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "hi"}, headers=headers,
                                 ))  # fmt: skip
    kind, data = events[-1]
    assert kind == "error" and "reference" in data["detail"]
    assert "secret internal detail" not in data["detail"]  # internals stay in the logs


def test_a_refusal_the_user_can_act_on_is_passed_through(api):
    headers = sign_up(api.client)

    def refuse(book_id, session_id):
        raise ValueError("the story has no event 'zzz'")

    api.on_run = refuse
    kind, data = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "hi"}, headers=headers))[-1]
    assert kind == "error" and "no event 'zzz'" in data["detail"]


def test_the_pieces_rebuild_the_reply_exactly():
    for text in ("", "one", "one two three four five six seven", "a  b\nc  d e f g h i j"):
        assert "".join(workspace._pieces(text)) == text
