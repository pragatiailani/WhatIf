"""Storylines in the reading page's API: graph, storyline routes, an edit made in chat, publishing.

The edit test runs the real streaming agent with a scripted model and the real storyline tools, so
it covers what `book_qa.stream` adds for edits (route, diff, the chat binding to a new storyline).
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from test_agents import FakeChat, call
from test_storyline import RAVI, VIK, world  # noqa: F401  (world is a fixture)

from app import auth, crypto
from app.agents import book_qa
from app.main import app
from app.mcp_server import storyline_tools as sl_tools
from app.repo import sessions, storylines, users
from app.repo.models import Storyline, User
from app.repo.storylines import StorylinesUnavailable
from app.storyline import publish
from app.storyline.schema import Fact

SETTINGS = SimpleNamespace(auth_secret="test-secret", secrets_key="")


@pytest.fixture
def api(world, mongo, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    monkeypatch.setattr(auth, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(crypto, "get_settings", lambda: SETTINGS)
    mongo["books"].update_one({"_id": world.book}, {"$set": {"status": "ready", "visibility": "public"}})
    with TestClient(app) as client:
        world.client = client
        yield world


def person(email="ravi@example.com"):
    u = users.create_user(User(name="Ravi", email=email, password_hash="x"))
    return u, {"Authorization": f"Bearer {auth.issue_token(u.id)}"}


def sse(res):
    out = []
    for block in res.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


# --- graph ---------------------------------------------------------------


def test_canon_graph_and_a_storyline_graph_that_lists_what_it_hid(api):
    _, h = person()
    canon = api.client.get(f"/books/{api.book}/graph").json()
    assert {e["key"] for e in canon["entities"]} == {RAVI, "asha|character", VIK}
    assert canon["events"][1]["involves"] == [VIK, RAVI] and canon["hidden"] == []

    sl = api.client.post(f"/books/{api.book}/storylines", json={"title": "Ravi lives"}, headers=h).json()
    session = sessions.book_chat(sl["owner_id"], api.book, sl["id"])
    sl_tools.start_draft(api.book, session.id, "e2", "Ravi survives",
                         [Fact(entity="Ravi", attribute="status", value="alive")])  # fmt: skip
    sl_tools.resolve_item(api.book, session.id, "e2", "rewrite", new_summary="Vik wounds Ravi")
    sl_tools.resolve_item(api.book, session.id, "r1", "hide")

    g = api.client.get(f"/books/{api.book}/graph?storyline_id={sl['id']}", headers=h).json()
    assert {"e2", "r1"} <= set(g["hidden"])
    assert any(e["key"] == "e2" for e in g["events"])  # hidden items are listed, not removed
    assert any(e["sid"] == sl["id"] and e["summary"] == "Vik wounds Ravi" for e in g["events"])
    assert api.client.get(f"/books/{api.book}/graph").json()["hidden"] == []  # canon is untouched

    other, other_h = person("asha@example.com")
    assert api.client.get(f"/books/{api.book}/graph?storyline_id={sl['id']}", headers=other_h).status_code == 404


# --- storyline routes ----------------------------------------------------


def test_storyline_lifecycle_visibility_and_unbuilt_features(api):
    owner, owner_h = person()
    other, other_h = person("asha@example.com")
    made = api.client.post(f"/books/{api.book}/storylines", json={"title": "Ravi lives", "premise": "What if?"}, headers=owner_h)
    assert made.status_code == 201
    sl = made.json()
    assert (sl["title"], sl["premise"], sl["visibility"], sl["owner_name"], sl["head_op_seq"]) == (
        "Ravi lives", "What if?", "private", "Ravi", 0)  # fmt: skip

    assert api.client.get(f"/storylines/{sl['id']}", headers=owner_h).status_code == 200
    assert api.client.get(f"/storylines/{sl['id']}", headers=other_h).status_code == 404  # private
    assert api.client.get("/storylines?scope=public").json() == []

    patched = api.client.patch(f"/storylines/{sl['id']}", json={"title": "Ravi survives", "visibility": "public"}, headers=owner_h).json()
    assert (patched["title"], patched["visibility"]) == ("Ravi survives", "public")
    assert api.client.patch(f"/storylines/{sl['id']}", json={"title": "x"}, headers=other_h).status_code == 403
    assert [s["id"] for s in api.client.get("/storylines?scope=public").json()] == [sl["id"]]
    assert [s["id"] for s in api.client.get(f"/books/{api.book}/storylines", headers=other_h).json()] == [sl["id"]]
    assert [s["id"] for s in api.client.get("/storylines?scope=mine", headers=owner_h).json()] == [sl["id"]]
    assert api.client.get("/storylines?scope=mine").status_code == 401

    base = f"/storylines/{sl['id']}"
    assert api.client.get(f"{base}/checkpoints", headers=owner_h).json() == []
    for path in (f"{base}/checkpoints", f"{base}/checkpoints/c1/restore", f"{base}/fork", f"{base}/star"):
        res = api.client.post(path, headers=owner_h)
        assert res.status_code == 501 and "not available yet" in res.json()["detail"]


def test_creating_a_storyline_needs_sign_in_and_a_ready_book(api, mongo):
    assert api.client.post(f"/books/{api.book}/storylines", json={"title": "x"}).status_code == 401
    mongo["books"].update_one({"_id": api.book}, {"$set": {"status": "merging"}})
    _, h = person()
    assert api.client.post(f"/books/{api.book}/storylines", json={"title": "x"}, headers=h).status_code == 409


def test_only_the_owner_can_chat_in_a_storyline(api):
    owner, owner_h = person()
    other, other_h = person("asha@example.com")
    sl = api.client.post(f"/books/{api.book}/storylines", json={"title": "t"}, headers=owner_h).json()
    body = {"storyline_id": sl["id"], "text": "hi"}
    assert api.client.post(f"/books/{api.book}/chat", json=body, headers=other_h).status_code == 404  # private
    api.client.patch(f"/storylines/{sl['id']}", json={"visibility": "public"}, headers=owner_h)
    assert api.client.post(f"/books/{api.book}/chat", json=body, headers=other_h).status_code == 403
    assert api.client.get(f"/books/{api.book}/messages?storyline_id={sl['id']}", headers=other_h).json() == []


# --- an edit made in chat ------------------------------------------------


def edit_tools(book_id, session_id):
    """The storyline tools the agent has, bound to this chat the way the tool server binds them."""

    def wrap(fn, name):
        return StructuredTool.from_function(
            lambda **kw: json.dumps(fn(book_id, session_id, **kw), default=str), name=name, description=name,
            args_schema={"type": "object", "properties": {k: {"type": "string"} for k in
                         ("anchor_event", "premise", "ref", "action", "new_summary")}},
        )  # fmt: skip

    def start(**kw):
        return sl_tools.start_draft(book_id, session_id, kw["anchor_event"], kw["premise"],
                                    [Fact(entity="Ravi", attribute="status", value="alive")])  # fmt: skip

    return [
        StructuredTool.from_function(
            lambda **kw: json.dumps(start(**kw)), name="start_draft", description="start",
            args_schema={"type": "object", "properties": {"anchor_event": {"type": "string"}, "premise": {"type": "string"}}},
        ),
        wrap(sl_tools.resolve_item, "resolve_item"),
        StructuredTool.from_function(lambda: json.dumps(sl_tools.verify_draft(book_id, session_id)),
                                     name="verify_draft", description="verify"),
        StructuredTool.from_function(lambda: json.dumps(sl_tools.commit_draft(book_id, session_id)),
                                     name="commit_draft", description="commit"),
    ]  # fmt: skip


@pytest.fixture
def scripted(api, monkeypatch):
    """Run the real streaming agent with these scripted model replies."""

    def script(*replies):
        @asynccontextmanager
        async def open_tools(book_id, session_id, user_id=None, storyline_id=None):
            yield edit_tools(book_id, session_id)

        monkeypatch.setattr(book_qa, "open_tools", open_tools)
        monkeypatch.setattr(book_qa, "get_chat_model", lambda: FakeChat(replies=list(replies), seen=[]))
        monkeypatch.setattr(book_qa, "get_checkpointer", lambda: InMemorySaver())

    return script


EDIT = [
    call("start_draft", {"anchor_event": "e2", "premise": "Ravi survives Vik's attack"}, "t1"),
    call("resolve_item", {"ref": "e2", "action": "rewrite", "new_summary": "Vik wounds Ravi"}, "t2"),
    call("resolve_item", {"ref": "r1", "action": "hide"}, "t3"),
    call("resolve_item", {"ref": "e3", "action": "keep"}, "t4"),
    call("resolve_item", {"ref": "e4", "action": "keep"}, "t5"),
    call("verify_draft", {}, "t6"),
]


def test_an_edit_in_a_canon_chat_branches_a_storyline_with_graph_and_diff(api, scripted):
    _, h = person()
    scripted(*EDIT, AIMessage(content="Ravi now survives; verification passed."))
    res = api.client.post(f"/books/{api.book}/chat", json={"text": "What if Ravi lived?"}, headers=h)
    events = sse(res)
    labels = [d["label"] for k, d in events if k == "phase"]
    assert "Starting the alternate version…" in labels and "Updating the story…" in labels
    assert "Checking the story still holds together…" in labels
    assert events[-1][0] == "done"

    turn = events[-1][1]
    sl = turn["storyline"]
    assert sl["title"].startswith("Ravi survives") and sl["head_op_seq"] > 0 and sl["premise"].startswith("Ravi survives")
    reply = turn["assistant"]
    assert reply["route"] == "edit" and "Ravi now survives" in reply["content"]
    assert reply["diff"]["op_seq"] == sl["head_op_seq"] and reply["diff"]["divergence_pos"] == 1000.0
    assert len(reply["diff"]["added"]) == 1 and reply["diff"]["hidden"] == ["e2"]
    assert any(e["sid"] == sl["id"] for e in turn["graph"]["events"]) and "e2" in turn["graph"]["hidden"]

    # the conversation that made the edit now lives in the storyline, diff included
    history = api.client.get(f"/books/{api.book}/messages?storyline_id={sl['id']}", headers=h).json()
    assert [m["role"] for m in history] == ["user", "assistant"] and history[1]["diff"] == reply["diff"]
    assert api.client.get(f"/books/{api.book}/messages", headers=h).json() == []  # canon chat starts fresh


def test_a_question_asked_afterwards_in_the_storyline_has_no_diff_and_no_graph(api, scripted):
    _, h = person()
    scripted(*EDIT, AIMessage(content="Done."))
    first = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "What if Ravi lived?"}, headers=h))
    sl_id = first[-1][1]["storyline"]["id"]

    scripted(AIMessage(content="He survived the attack."))
    again = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "What happened to Ravi?", "storyline_id": sl_id}, headers=h))
    turn = again[-1][1]
    assert turn["storyline"]["id"] == sl_id and "diff" not in turn["assistant"] and "graph" not in turn
    assert turn["assistant"]["route"] == "local"
    history = api.client.get(f"/books/{api.book}/messages?storyline_id={sl_id}", headers=h).json()
    assert len(history) == 4  # the edit and the follow-up are one conversation


def test_a_plain_question_in_canon_branches_nothing(api, scripted):
    _, h = person()
    scripted(AIMessage(content="Ravi is a clerk."))
    turn = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "Who is Ravi?"}, headers=h))[-1][1]
    assert turn["storyline"] is None and "graph" not in turn and turn["assistant"]["route"] == "local"
    assert storylines.list_for_user(users.find_by_email("ravi@example.com").id) == []


# --- publishing a storyline ----------------------------------------------


def make_branch(api, *, commit=True):
    """A storyline on the scenario book with chapters 2 and 3 changed (by the same edit as above)."""
    u, _ = person("owner@example.com")
    sl = storylines.create_storyline(Storyline(book_id=api.book, owner_id=u.id, name="Ravi lives"))
    session = sessions.book_chat(u.id, api.book, sl.id)
    sl_tools.start_draft(api.book, session.id, "e2", "Ravi survives", [Fact(entity="Ravi", attribute="status", value="alive")])
    for ref, action, extra in (("e2", "rewrite", {"new_summary": "Vik wounds Ravi"}), ("r1", "hide", {}),
                               ("e3", "rewrite", {"new_summary": "Asha keeps vigil"}), ("e4", "keep", {})):  # fmt: skip
        sl_tools.resolve_item(api.book, session.id, ref, action, **extra)
    if commit:
        sl_tools.verify_draft(api.book, session.id)
        sl_tools.commit_draft(api.book, session.id)
    return sl, session


def test_publishing_needs_a_committed_storyline_with_every_changed_chapter_written(api):
    assert publish.storyline_text("nope") is None
    sl, session = make_branch(api, commit=False)
    with pytest.raises(StorylinesUnavailable, match="open draft"):
        storylines.get_storyline_text(sl.id)

    sl_tools.verify_draft(api.book, session.id)
    sl_tools.commit_draft(api.book, session.id)  # also writes the changed chapters
    text = storylines.get_storyline_text(sl.id)  # so it can be published straight away
    assert (text.id, text.book_id, text.title, text.diverges_at) == (sl.id, api.book, "Ravi lives", "Chapter 2")
    assert [t for t, _ in text.chapters] == ["Chapter 2", "Chapter 3", "Chapter 4"]  # to the end of the book
    assert "vigil" in text.chapters[0][1] and "vigil" in text.chapters[1][1]  # the written passages
    assert text.chapters[2][1].strip() == "Asha faced Vik."  # chapter 4 is unchanged: the original text


def test_a_changed_chapter_with_no_text_blocks_publishing_and_says_which(api, mongo):
    sl, session = make_branch(api)  # committed, chapters written
    mongo["storyline_chunks"].delete_many({"chapter": {"$in": ["Chapter 2", "Chapter 3"]}})  # a write that failed
    with pytest.raises(StorylinesUnavailable, match="Chapter 2, Chapter 3"):
        storylines.get_storyline_text(sl.id)
    sl_tools.read_chapter(api.book, "Chapter 2", session.id)  # reading writes it
    sl_tools.read_chapter(api.book, "Chapter 3", session.id)
    assert [t for t, _ in storylines.get_storyline_text(sl.id).chapters] == ["Chapter 2", "Chapter 3", "Chapter 4"]


def test_a_storyline_that_changes_nothing_publishes_as_the_original_book(api):
    u, _ = person("owner@example.com")
    sl = storylines.create_storyline(Storyline(book_id=api.book, owner_id=u.id, name="Empty"))
    text = storylines.get_storyline_text(sl.id)
    assert text.diverges_at is None and text.chapters == []


def test_the_publisher_composes_the_book_before_the_change_with_the_storyline_after_it(api):
    from app import inkitt

    sl, session = make_branch(api)  # committing wrote the chapters
    story = inkitt.storyline_story(sl.id)
    assert (story.kind, story.source_id, story.title) == ("storyline", sl.id, "Ravi lives")
    assert [t for t, _ in story.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"]
    assert "Ravi met Asha" in story.chapters[0][1] and "vigil" in story.chapters[1][1]


# --- saying "update it" ----------------------------------------------------


def test_the_turn_that_commits_shows_the_rewritten_text_in_the_chat(api, scripted):
    _, h = person()
    scripted(*EDIT, call("commit_draft", {}, "t7"), AIMessage(content="Done: Ravi survives."))
    turn = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "What if Ravi lived? Go ahead."}, headers=h))[-1][1]
    diff = turn["assistant"]["diff"]
    assert diff["passage"].startswith("Chapter 2\n\n") and "vigil" in diff["passage"]  # the new text, with its title
    assert turn["assistant"]["route"] == "edit" and "graph" in turn


def test_approving_in_a_later_turn_also_shows_the_text(api, scripted):
    """Ideate and draft in one turn, say yes in the next: the second turn writes no graph changes,
    but it writes the chapters, and the reader sees them."""
    _, h = person()
    scripted(*EDIT, AIMessage(content="Ready to save; shall I?"))
    first = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "What if Ravi lived?"}, headers=h))[-1][1]
    sl_id = first["storyline"]["id"]
    assert first["assistant"]["diff"]["passage"] == ""  # nothing is written until the reader says yes

    scripted(call("commit_draft", {}, "t8"), AIMessage(content="Saved."))
    second = sse(api.client.post(f"/books/{api.book}/chat", json={"text": "yes, update it", "storyline_id": sl_id}, headers=h))[-1][1]
    diff = second["assistant"]["diff"]
    assert diff["added"] == [] and diff["hidden"] == []  # no new graph changes in this turn
    assert diff["passage"].startswith("Chapter 2\n\n") and "vigil" in diff["passage"]
    assert "graph" in second and second["storyline"]["id"] == sl_id


def test_a_long_chapter_is_previewed_not_dumped(api):
    from app.storyline.diff import PASSAGE_PREVIEW_CHARS, _preview
    from app.repo.models import StorylineChunk

    passage = StorylineChunk(storyline_id="s", book_id="b", chapter="Chapter 9", text="word " * 2000,
                             fingerprint="f", op_seq=1, seq_start=0, seq_end=0)  # fmt: skip
    shown = _preview(passage)
    assert shown.startswith("Chapter 9\n\n") and shown.endswith("…")
    assert len(shown) <= len("Chapter 9\n\n") + PASSAGE_PREVIEW_CHARS + 1
