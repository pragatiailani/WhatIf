"""The planning tools, run inside a real create_agent graph with a scripted model."""

import logging

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool, ToolException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from app.agents import whatif
from app.agents.whatif import build_agent
from app.agents.planning import PLANNING_TOOLS, read_todos, think_tool, write_todos

TODOS = [
    {"content": "Decide chapter 2 items", "status": "in_progress"},
    {"content": "Verify the draft", "status": "pending"},
]


class Scripted(BaseChatModel):
    replies: list

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **_):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **_):
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def call(name, args, id_="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    monkeypatch.setenv("AUTH_SECRET", "x")


def agent(*replies, saver=None):
    return build_agent(Scripted(replies=list(replies)), [], saver)


def test_the_three_tools_are_langchain_tools_with_model_facing_schemas():
    assert [t.name for t in PLANNING_TOOLS] == ["write_todos", "read_todos", "think_tool"]
    for t in PLANNING_TOOLS:
        hidden = {"state", "tool_call_id"} & set(t.tool_call_schema.model_json_schema()["properties"])
        assert not hidden, f"{t.name} shows injected arguments to the model"
    assert set(write_todos.tool_call_schema.model_json_schema()["required"]) == {"todos", "description"}
    assert "reflection" in think_tool.tool_call_schema.model_json_schema()["properties"]


async def test_write_then_read_updates_agent_state():
    graph = agent(
        call("write_todos", {"todos": TODOS, "description": "plan the draft"}),
        call("read_todos", {"description": "check the plan"}, "c2"),
        AIMessage(content="done"),
    )
    result = await graph.ainvoke({"messages": [HumanMessage(content="plan it")]})
    assert result["todos"] == TODOS
    replies = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert replies[0].startswith("Updated todo list")
    assert replies[1] == (
        "Current TODO List:\n1. Decide chapter 2 items (in_progress)\n2. Verify the draft (pending)"
    )


async def test_read_with_no_todos_and_think_change_nothing():
    graph = agent(
        call("read_todos", {"description": "look"}),
        call("think_tool", {"reflection": "verify found one problem in ch 4"}, "c2"),
        AIMessage(content="ok"),
    )
    result = await graph.ainvoke({"messages": [HumanMessage(content="hi")]})
    tool_replies = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_replies == [
        "No todos currently in the list.",
        "Reflection recorded: verify found one problem in ch 4",
    ]
    assert not result.get("todos")


async def test_the_todo_list_persists_across_turns_of_a_chat():
    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "chat-1"}}
    first = agent(
        call("write_todos", {"todos": TODOS, "description": "plan"}), AIMessage(content="planned"), saver=saver
    )
    await first.ainvoke({"messages": [HumanMessage(content="start")]}, cfg)

    second = agent(call("read_todos", {"description": "resume"}), AIMessage(content="resumed"), saver=saver)
    result = await second.ainvoke({"messages": [HumanMessage(content="continue")]}, cfg)
    reply = next(m.content for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "read_todos")
    assert "1. Decide chapter 2 items (in_progress)" in reply


def test_the_prompt_covers_every_tool_the_agent_has():
    import asyncio

    from app.agents.whatif.prompt import SYSTEM_PROMPT
    from app.constants import AGENT_EXCLUDED_TOOLS
    from app.mcp_server.server import mcp

    tools = {t.name for t in asyncio.run(mcp.list_tools())} - AGENT_EXCLUDED_TOOLS
    tools |= {t.name for t in PLANNING_TOOLS}
    missing = sorted(name for name in tools if name not in SYSTEM_PROMPT)
    assert not missing, f"the prompt never mentions: {missing}"


# --- tool call logging ---------------------------------------------------


async def test_every_tool_call_is_logged_with_its_outcome(caplog):
    caplog.set_level(logging.INFO, logger="app.agent.tools")
    graph = agent(
        call("write_todos", {"todos": TODOS, "description": "plan the draft"}),
        call("read_todos", {"description": "check"}, "c2"),
        AIMessage(content="done"),
    )
    await graph.ainvoke({"messages": [HumanMessage(content="plan it")]})
    lines = [r.getMessage() for r in caplog.records if r.name == "app.agent.tools"]
    assert len(lines) == 2
    assert lines[0].startswith("tool=write_todos ok in ") and "description=plan the draft" in lines[0]
    assert lines[1].startswith("tool=read_todos ok in ")


async def test_a_tool_error_the_model_can_recover_from_is_a_warning(caplog):
    def broken(value: str) -> str:
        raise ToolException("no such entity")

    tool = StructuredTool.from_function(broken, name="find_thing", description="d", handle_tool_error=True)
    caplog.set_level(logging.INFO, logger="app.agent.tools")
    graph = build_agent(
        Scripted(replies=[call("find_thing", {"value": "Gandalf " * 80}), AIMessage(content="sorry")]), [tool]
    )
    await graph.ainvoke({"messages": [HumanMessage(content="who?")]})
    [record] = [r for r in caplog.records if r.name == "app.agent.tools"]
    assert record.levelno == logging.WARNING and "tool=find_thing error" in record.getMessage()
    assert "no such entity" in record.getMessage()
    assert len(record.getMessage()) < 600  # a huge argument is clipped, not dumped


async def test_a_tool_that_raises_is_logged_and_still_raises(caplog):
    def crash(value: str) -> str:
        raise RuntimeError("db is down")

    tool = StructuredTool.from_function(crash, name="crash", description="d", handle_tool_error=False)
    caplog.set_level(logging.INFO, logger="app.agent.tools")
    graph = build_agent(Scripted(replies=[call("crash", {"value": "x"})]), [tool])
    with pytest.raises(Exception, match="db is down"):
        await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    assert any("tool=crash failed" in r.getMessage() for r in caplog.records)


# --- progress ------------------------------------------------------------


async def test_the_agent_reports_what_it_is_about_to_do():
    from app.agents.progress import LABELS, label_for

    seen = []
    graph = build_agent(
        Scripted(replies=[
            call("write_todos", {"todos": TODOS, "description": "plan"}),
            call("think_tool", {"reflection": "ok"}, "c2"),
            AIMessage(content="done"),
        ]),
        [],
        None,
        on_progress=seen.append,
    )  # fmt: skip
    await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    assert seen == [LABELS["write_todos"], LABELS["think_tool"]]
    assert label_for("some_new_tool") == "Working on it…"


async def test_a_broken_progress_callback_does_not_break_the_agent(caplog):
    def broken(label):
        raise RuntimeError("client went away")

    graph = build_agent(
        Scripted(replies=[call("read_todos", {"description": "x"}), AIMessage(content="fine")]),
        [],
        None,
        on_progress=broken,
    )
    result = await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    assert result["messages"][-1].content == "fine"
    assert any("progress callback failed" in r.getMessage() for r in caplog.records)


def test_every_tool_has_a_progress_label():
    import asyncio

    from app.agents.progress import LABELS
    from app.constants import AGENT_EXCLUDED_TOOLS
    from app.mcp_server.server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())} - AGENT_EXCLUDED_TOOLS
    names |= {t.name for t in PLANNING_TOOLS}
    assert not names - set(LABELS), f"no progress label for: {sorted(names - set(LABELS))}"
