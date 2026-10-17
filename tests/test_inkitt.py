import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from app import auth, crypto, inkitt
from app.agents import tools as agent_tools
from app.api import chat
from app.api.auth import router as auth_router
from app.repo import books, chunks, inkitt_stories, sessions, storylines, users
from app.repo.models import Book, ChatSession, Chunk, User
from app.repo.storylines import StorylineText

SETTINGS = SimpleNamespace(auth_secret="test-secret", secrets_key="")


@pytest.fixture
def env(mongo, monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(crypto, "get_settings", lambda: SETTINGS)
    users.ensure_indexes()


def make_user(email="ravi@example.com") -> User:
    return users.create_user(User(name="Ravi", email=email, password_hash="x"))


def make_book(owner_id=None, pages=None) -> Book:
    book = books.create_book(Book(title="Kim", content_hash="h", owner_id=owner_id, status="ready"))
    pages = pages or [
        (None, "KIM\nA novel"),
        ("Chapter 1", "Chapter 1\nRavi left Delhi.\nIt rained."),
        ("Chapter 1", "He came back.\n\nChapter 2\nThe debt was paid."),
        ("Chapter 2", "The end."),
    ]
    chunks.insert_chunks(
        [Chunk(book_id=book.id, seq=i, chapter=ch, text=t) for i, (ch, t) in enumerate(pages)]
    )
    return book


class FakeInkitt:
    """Stands in for an Inkitt MCP ClientSession; records calls, answers like the real server."""

    def __init__(self, fail: set[str] = frozenset()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail
        self.next_chapter = 100

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name in self.fail:
            return self._result({"error": "Story needs a cover image"}, error=True)
        if name == "create_story":
            return self._result({"story": {"id": 7, "title": args["title"]}})
        if name == "list_chapters":
            return self._result({"chapters": [{"id": 99, "name": "Untitled"}]})
        if name == "create_chapter":
            self.next_chapter += 1
            return self._result({"chapter": {"id": self.next_chapter}})
        return self._result({"ok": True})

    @staticmethod
    def _result(data, error=False):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(data))],
            isError=error,
            structuredContent=None,
        )


# --- storage -------------------------------------------------------------


def test_token_is_stored_encrypted(env, mongo):
    user = make_user()
    users.set_inkitt_pat(user.id, "ink_secret")
    stored = mongo["users"].find_one({"_id": user.id})["inkitt_pat"]
    assert stored and "ink_secret" not in stored
    assert users.get_inkitt_pat(user.id) == "ink_secret"
    assert users.get_user(user.id).inkitt_pat == stored

    users.set_inkitt_pat(user.id, None)
    assert users.get_inkitt_pat(user.id) is None


def test_token_unreadable_under_another_key(env, monkeypatch):
    user = make_user()
    users.set_inkitt_pat(user.id, "ink_secret")
    monkeypatch.setattr(crypto, "get_settings", lambda: SimpleNamespace(auth_secret="other", secrets_key=""))
    assert users.get_inkitt_pat(user.id) is None


# --- chapters and publishing ---------------------------------------------


def test_book_chapters_split_at_headings(env):
    book = make_book()
    assert inkitt.book_chapters(book.id) == [
        ("Chapter 1", "Ravi left Delhi.\nIt rained.\nHe came back."),
        ("Chapter 2", "The debt was paid.\nThe end."),
    ]


def test_book_without_chapters_is_one_chapter(env):
    book = make_book(pages=[(None, "Once."), (None, "Twice.")])
    assert inkitt.book_chapters(book.id) == [("Kim", "Once.\nTwice.")]


async def test_publish_book_creates_drafts_once(env):
    book = make_book()
    fake = FakeInkitt()
    out = await inkitt.publish_story(fake, inkitt.book_story(book.id), summary="A boy and a debt.")

    assert out["status"] == "drafts_created" and out["story_id"] == 7
    assert out["chapter_ids"] == [99, 101]
    names = [n for n, _ in fake.calls]
    assert names == ["create_story", "list_chapters", "update_chapter", "create_chapter"]
    assert fake.calls[0][1]["summary"] == "A boy and a debt."
    assert fake.calls[2][1] == {"story_id": 7, "chapter_id": 99, "name": "Chapter 1",
                                "text": "Ravi left Delhi.\nIt rained.\nHe came back."}
    assert "publish_all_chapters" not in names
    assert inkitt_stories.get_story_id("book", book.id) == 7

    again = await inkitt.publish_story(fake, inkitt.book_story(book.id))
    assert again["status"] == "already_on_inkitt" and len(fake.calls) == 4


async def test_publish_reports_inkitt_refusal(env):
    book = make_book()
    fake = FakeInkitt(fail={"publish_all_chapters"})
    out = await inkitt.publish_story(fake, inkitt.book_story(book.id), publish=True)
    assert out["status"] == "drafts_created" and "cover" in out["publish_error"]


# --- agent tools ---------------------------------------------------------


def _inkitt_server_tools():
    async def noop(**_):
        return "{}"

    names = ["list_stories", "create_story", "delete_story", "delete_chapter", "publish_all_chapters",
             "cs_shadowban_user", "cs_update_user_email"]
    return [StructuredTool.from_function(coroutine=noop, name=n, description=n,
                                         args_schema={"type": "object", "properties": {}})
            for n in names]


async def _tools_for(book_id, user_id):
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        return await agent_tools._inkitt_tools(stack, book_id, user_id)


async def test_without_token_only_connect_tool(env):
    user = make_user()
    tools = await _tools_for("b", user.id)
    assert [t.name for t in tools] == ["connect_inkitt"]
    assert "asking the user" in await tools[0].ainvoke({})
    assert [t.name for t in await _tools_for("b", None)] == ["connect_inkitt"]


async def test_with_token_loads_only_whitelisted_tools(env, monkeypatch):
    user = make_user()
    users.set_inkitt_pat(user.id, "ink_secret")
    seen = []

    @asynccontextmanager
    async def fake_open(pat):
        seen.append(pat)
        yield FakeInkitt()

    async def fake_load(session):
        return _inkitt_server_tools()

    monkeypatch.setattr(inkitt, "open_session", fake_open)
    monkeypatch.setattr(agent_tools, "load_mcp_tools", fake_load)
    names = {t.name for t in await _tools_for("b", user.id)}
    assert seen == ["ink_secret"]
    assert names == {"list_stories", "publish_all_chapters", "publish_to_inkitt"}


async def test_rejected_token_falls_back_to_connect(env, monkeypatch):
    user = make_user()
    users.set_inkitt_pat(user.id, "revoked")

    @asynccontextmanager
    async def failing_open(pat):
        raise RuntimeError("401 Unauthorized")
        yield

    monkeypatch.setattr(inkitt, "open_session", failing_open)
    tools = await _tools_for("b", user.id)
    assert [t.name for t in tools] == ["connect_inkitt"]
    assert "rejected" in await tools[0].ainvoke({})


async def test_publish_tool_only_for_owner(env):
    owner, other = make_user(), make_user("x@example.com")
    book = make_book(owner_id=owner.id)
    fake = FakeInkitt()

    refused = json.loads(await agent_tools._publish_tool(fake, book.id, other.id).ainvoke({}))
    assert "error" in refused and fake.calls == []

    out = json.loads(await agent_tools._publish_tool(fake, book.id, owner.id).ainvoke({}))
    assert out["status"] == "drafts_created"


# --- storylines -----------------------------------------------------------


def _storyline(owner_id, book_id, diverges_at="Chapter 2"):
    return StorylineText(
        id="sl1", book_id=book_id, owner_id=owner_id, title="Kim stays in Delhi",
        diverges_at=diverges_at,
        chapters=[("Chapter 2: Delhi", "Ravi stayed."), ("Chapter 3", "The debt grew.")],
    )


def test_storyline_story_is_the_rewritten_book(env, monkeypatch):
    book = make_book()
    monkeypatch.setattr(storylines, "get_storyline_text", lambda sid: _storyline("u1", book.id))
    story = inkitt.storyline_story("sl1")
    assert (story.kind, story.source_id, story.owner_id, story.title) == (
        "storyline", "sl1", "u1", "Kim stays in Delhi")
    assert [t for t, _ in story.chapters] == ["Chapter 1", "Chapter 2: Delhi", "Chapter 3"]

    # diverging after the last chapter keeps the whole book
    monkeypatch.setattr(storylines, "get_storyline_text",
                        lambda sid: _storyline("u1", book.id, diverges_at=None))
    assert [t for t, _ in inkitt.storyline_story("sl1").chapters] == [
        "Chapter 1", "Chapter 2", "Chapter 2: Delhi", "Chapter 3"]


async def test_storyline_of_catalog_book_publishable_by_its_creator(env, monkeypatch):
    creator, other = make_user(), make_user("x@example.com")
    book = make_book(owner_id=None)  # a catalog book nobody uploaded
    monkeypatch.setattr(storylines, "get_storyline_text", lambda sid: _storyline(creator.id, book.id))
    fake = FakeInkitt()

    refused = json.loads(await agent_tools._publish_tool(fake, book.id, other.id, "sl1").ainvoke({}))
    assert "error" in refused and fake.calls == []

    args = {"story_type": "fanfiction"}
    out = json.loads(await agent_tools._publish_tool(fake, book.id, creator.id, "sl1").ainvoke(args))
    assert out["status"] == "drafts_created" and fake.calls[0][1]["story_type"] == "fanfiction"
    assert out["chapter_titles"] == ["Chapter 1", "Chapter 2: Delhi", "Chapter 3"]
    assert inkitt_stories.get_story_id("storyline", "sl1") == 7
    assert inkitt_stories.get_story_id("book", book.id) is None  # the book itself was not sent

    # the book chat (no storyline) still refuses: the catalog book is not the user's
    book_chat = json.loads(await agent_tools._publish_tool(fake, book.id, creator.id).ainvoke({}))
    assert "error" in book_chat


async def test_unknown_storyline_is_reported(env):
    user = make_user()
    book = make_book(owner_id=user.id)
    out = json.loads(await agent_tools._publish_tool(FakeInkitt(), book.id, user.id, "sl1").ainvoke({}))
    assert "unknown storyline" in out["error"]


async def test_agent_reports_token_required(env, monkeypatch):
    from langchain_core.messages import AIMessage

    from app.agents import book_qa
    from tests.test_agents import call, fake

    user = make_user()
    s = sessions.create_session(ChatSession(book_id="book1", user_id=user.id))

    @asynccontextmanager
    async def fake_open_tools(book_id, session_id, user_id=None, storyline_id=None):
        assert user_id == user.id
        yield [agent_tools._connect_tool("Inkitt is not connected.")]

    monkeypatch.setattr(book_qa, "open_tools", fake_open_tools)
    llm = fake(call("connect_inkitt", {}, "t1"), AIMessage(content="Please connect Inkitt."))
    out = await book_qa.run("book1", s.id, "Publish this to Inkitt", llm=llm, checkpointer=None)
    assert out["actions"] == ["inkitt_token_required"]


# --- API -----------------------------------------------------------------


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat.router)
    return TestClient(app)


def _headers(user: User) -> dict:
    return {"Authorization": f"Bearer {auth.issue_token(user.id)}"}


def test_connect_and_disconnect_inkitt(client, monkeypatch):
    user = make_user()

    async def verify(pat):
        return pat == "ink_good"

    monkeypatch.setattr(inkitt, "verify", verify)
    assert client.put("/auth/me/inkitt", json={"token": "ink_good"}).status_code == 401

    bad = client.put("/auth/me/inkitt", json={"token": "ink_bad"}, headers=_headers(user))
    assert bad.status_code == 400 and users.get_inkitt_pat(user.id) is None

    ok = client.put("/auth/me/inkitt", json={"token": " ink_good "}, headers=_headers(user))
    assert ok.json()["inkitt_connected"] is True and "inkitt_pat" not in ok.json()
    assert users.get_inkitt_pat(user.id) == "ink_good"
    assert client.get("/auth/me", headers=_headers(user)).json()["inkitt_connected"] is True

    off = client.delete("/auth/me/inkitt", headers=_headers(user))
    assert off.json()["inkitt_connected"] is False and users.get_inkitt_pat(user.id) is None


def test_signed_in_session_is_private(client, monkeypatch):
    owner, other = make_user(), make_user("x@example.com")
    book = make_book(owner_id=owner.id)

    async def run(book_id, session_id, question, **_):
        return {"answer": "ok", "citations": [], "trace": [], "actions": []}

    monkeypatch.setattr(chat, "get_agent", lambda name: SimpleNamespace(run=run))
    sid = client.post("/sessions", json={"book_id": book.id}, headers=_headers(owner)).json()["session_id"]
    assert sessions.get_session(sid).user_id == owner.id

    ask = {"question": "Publish to Inkitt"}
    assert client.post(f"/sessions/{sid}/chat", json=ask).status_code == 403
    assert client.post(f"/sessions/{sid}/chat", json=ask, headers=_headers(other)).status_code == 403
    assert client.post(f"/sessions/{sid}/chat", json=ask, headers=_headers(owner)).status_code == 200
