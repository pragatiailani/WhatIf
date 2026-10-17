import json
from contextlib import asynccontextmanager

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

import app.agents as agents
from app.agents import book_qa
from app.agents.tools import hide_args
from app.config import get_settings
from app.repo import sessions
from app.repo.models import ChatSession


class FakeChat(BaseChatModel):
    """Replies with queued messages and remembers what it was shown."""

    replies: list
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **_):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **_):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def fake(*replies):
    return FakeChat(replies=list(replies), seen=[])


def call(name, args, id_):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])


seen_book_ids = []


async def _get_chunks(book_id: str, chunk_ids: list[str]) -> str:
    seen_book_ids.append(book_id)
    return json.dumps({
        "chunks": [{"chunk_id": "c1", "seq": 0, "chapter": "Chapter 1", "page_start": 3,
                    "page_end": 3, "text": "Ravi left Delhi because of the debt."}],
        "missing": [],
    })


def make_tools():
    base = StructuredTool.from_function(
        coroutine=_get_chunks, name="get_chunks", description="passages")
    return [hide_args(base, {"book_id": "book1", "session_id": "s"})]


@pytest.fixture
def env(mongo, monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    monkeypatch.setenv("FALKOR_PASSWORD", "x")
    get_settings.cache_clear()
    seen_book_ids.clear()

    @asynccontextmanager
    async def fake_open_tools(book_id, session_id, user_id=None, storyline_id=None):
        yield make_tools()

    monkeypatch.setattr(book_qa, "open_tools", fake_open_tools)
    yield
    get_settings.cache_clear()


def new_session():
    return sessions.create_session(ChatSession(book_id="book1"))


async def ask(session, question, llm, saver=None):
    run = agents.get_agent("book_qa").run
    return await run("book1", session.id, question, llm=llm, checkpointer=saver)


async def test_answers_with_citations_and_saves_messages(env):
    s = new_session()
    llm = fake(
        call("get_chunks", {"chunk_ids": ["c1"], "book_id": "evil"}, "t1"),
        AIMessage(content="He left because of a debt (Chapter 1, p. 3)."),
    )
    out = await ask(s, "Why did Ravi leave?", llm)

    assert out["answer"].startswith("He left because of a debt")
    assert out["citations"] == [{"chunk_id": "c1", "chapter": "Chapter 1", "page_start": 3,
                                 "page_end": 3, "seq": 0,
                                 "quote": "Ravi left Delhi because of the debt."}]
    assert seen_book_ids == ["book1"]  # the model's book_id was replaced
    stored = sessions.last_messages(s.id, 10)
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[1].citations == out["citations"] and stored[1].trace[0]["tool"] == "get_chunks"
    assert sessions.get_session(s.id).title == "Why did Ravi leave?"
    # the model saw the tool result and the system prompt
    assert "because of the debt" in str(llm.seen[1][-1].content)
    assert "only from what the tools return" in llm.seen[0][0].content


async def test_conversation_memory_is_per_session_thread(env):
    saver = InMemorySaver()
    a, b = new_session(), new_session()
    llm = fake(AIMessage(content="First."), AIMessage(content="Second."), AIMessage(content="Other."))

    await ask(a, "Who is Ravi?", llm, saver)
    await ask(a, "And his friend?", llm, saver)
    await ask(b, "Hello?", llm, saver)

    second_turn = [str(m.content) for m in llm.seen[1]]
    assert "Who is Ravi?" in second_turn and "First." in second_turn
    assert not any("Who is Ravi?" in str(m.content) for m in llm.seen[2])  # other chat: no leak
    assert {c.config["configurable"]["thread_id"] for c in saver.list(None)} == {a.id, b.id}


async def test_citations_only_from_this_question(env):
    saver = InMemorySaver()
    s = new_session()
    llm = fake(
        call("get_chunks", {"chunk_ids": ["c1"]}, "t1"), AIMessage(content="Answer one."),
        AIMessage(content="Answer two, from memory of the chat."),
    )
    first = await ask(s, "Q1", llm, saver)
    second = await ask(s, "Q2", llm, saver)
    assert len(first["citations"]) == 1 and second["citations"] == []


async def test_unknown_session_or_wrong_book(env):
    with pytest.raises(ValueError, match="unknown session"):
        await agents.get_agent("book_qa").run("book1", "nope", "hi", llm=fake())
    s = new_session()
    with pytest.raises(ValueError, match="unknown session"):
        await agents.get_agent("book_qa").run("other-book", s.id, "hi", llm=fake())


def test_registry():
    assert [a.name for a in agents.list_agents()] == ["book_qa"]
    assert agents.get_agent("book_qa").run is book_qa.run
    with pytest.raises(KeyError, match="available: book_qa"):
        agents.get_agent("nope")
    with pytest.raises(ValueError, match="already registered"):
        agents.register_agent("book_qa", "again")(lambda: None)


def test_hide_args_removes_injected_fields_from_the_schema():
    tool = make_tools()[0]
    assert set(tool.args_schema["properties"]) == {"chunk_ids"}
    assert tool.args_schema["required"] == ["chunk_ids"]


async def test_open_tools_over_stdio_hides_ids(env):
    from app.agents.tools import open_tools

    async with open_tools("b1", "s1") as tools:
        by_name = {t.name: t for t in tools}
    assert "get_history" not in by_name and "find_entities" in by_name
    for tool in tools:
        assert not {"book_id", "session_id"} & set(tool.args_schema["properties"])


async def test_stream_yields_phases_tokens_and_done(env):
    s = new_session()
    llm = fake(
        call("get_chunks", {"chunk_ids": ["c1"]}, "t1"),
        AIMessage(content="Because of a debt (Chapter 1, p. 3)."),
    )
    events = [e async for e in book_qa.stream("book1", s.id, "Why?", llm=llm, checkpointer=None)]

    kinds = [k for k, _ in events]
    assert kinds[0] == "phase" and kinds[-1] == "done"
    assert ("phase", "Reading the passages…") in events  # the tool call, labelled
    assert "".join(v for k, v in events if k == "token") == "Because of a debt (Chapter 1, p. 3)."
    done = events[-1][1]
    assert done["answer"] == "Because of a debt (Chapter 1, p. 3)."
    assert done["assistant_message"].citations == done["citations"]
    assert done["user_message"].content == "Why?"


def test_graph_refs_from_tool_results():
    from langchain_core.messages import ToolMessage

    retrieved = json.dumps({
        "entities": [{"key": "ravi"}],
        "relations": [{"id": "r1", "src_key": "ravi", "dst_key": "asha"}],
        "communities": [{"cid": 2}], "passages": [],
    })
    neighbors = json.dumps({"entity": {"key": "asha"}, "neighbors": [{"entity": {"key": "meera"}}]})
    turn = [
        call("retrieve", {"query": "q"}, "t1"),
        ToolMessage(content=retrieved, name="retrieve", tool_call_id="t1"),
        call("get_neighbors", {"entity": "asha"}, "t2"),
        ToolMessage(content=neighbors, name="get_neighbors", tool_call_id="t2"),
        ToolMessage(content="boom", name="find_entities", tool_call_id="t3", status="error"),
        AIMessage(content="done"),
    ]
    _, _, trace, refs = book_qa.read_turn(turn)
    assert refs == {"seeds": ["ravi", "asha"], "expanded": ["ravi", "asha", "meera"],
                    "edges": ["r1"], "communities": [2]}
    assert trace[-1]["error"] is True
