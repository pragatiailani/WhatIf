"""The WhatIf agent: the one agent behind every chat, a ReAct loop over all the tools.

It answers questions about the book and, when the reader wants a "what if", builds and verifies an
alternate version of it. It has the MCP tools (graph, text, session and storyline tools) and the
planning tools (todo list, reflection). The model decides which to call; the storyline tools
enforce the editing rules, so it cannot commit a draft that has not passed verification.

The conversation lives in the checkpointer (one thread per chat session, and so is the todo list);
older turns are summarised once the history passes `CHAT_HISTORY_BUDGET`. Each message and reply is
also saved to the `messages` collection for the UI, with the passages the answer was built from.
"""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Checkpointer

from app.agents.logging_mw import ToolCallLogger
from app.agents.planning import PlanningMiddleware
from app.agents.progress import ProgressReporter
from app.agents.registry import register_agent
from app.agents.tools import open_tools, tool_text
from app.agents.whatif.prompt import SYSTEM_PROMPT
from app.config import get_settings
from app.constants import HISTORY_KEEP_MESSAGES, MAX_CITATIONS, MAX_MODEL_CALLS, TITLE_CHARS
from app.llm import get_chat_model
from app.repo import sessions
from app.repo.checkpoints import get_checkpointer
from app.repo.models import Message

NAME = "whatif"
log = logging.getLogger(__name__)
# Tools whose results are passages to cite, and where the passages are in the result. A rewritten
# chapter from read_chapter is a single passage at the top level.
CITING_TOOLS = {"get_chunks": "chunks", "search_chunks": "results", "read_chapter": None}


def build_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    checkpointer: Checkpointer = None,
    on_progress: Callable[[str], None] | None = None,
):
    """The agent around `llm` and already-bound `tools`, plus the planning tools. `on_progress`
    is told what the agent is about to do, for a live status line."""
    return create_agent(
        llm,
        tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            SummarizationMiddleware(
                model=llm,
                trigger=("tokens", get_settings().chat_history_budget),
                keep=("messages", HISTORY_KEEP_MESSAGES),
            ),
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS),
            PlanningMiddleware(),
            ToolCallLogger(),
            *([ProgressReporter(on_progress)] if on_progress else []),
        ],
        checkpointer=checkpointer,
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in content)


def _citations_from(tool: str, text: str) -> list[dict]:
    """Chunk references (id, chapter, pages) from a get_chunks / search_chunks / read_chapter result."""
    try:
        data = json.loads(text)
        key = CITING_TOOLS[tool]
        items = data.get(key, []) if key else ([data] if data.get("source") == "storyline" else [])
    except (ValueError, AttributeError):
        return []
    return [
        {k: i.get(k) for k in ("chunk_id", "chapter", "page_start", "page_end", "seq")}
        for i in items
        if isinstance(i, dict) and i.get("chunk_id")
    ]


def _this_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Messages after the last human message: the model's work on the current question."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i + 1 :]
    return messages


def read_turn(messages: list[BaseMessage]) -> tuple[str, list[dict], list[dict]]:
    """(answer, citations, tool-call trace) for the question just answered."""
    turn = _this_turn(messages)
    calls = {c["id"]: c for m in turn if isinstance(m, AIMessage) for c in m.tool_calls}
    citations: dict[str, dict] = {}
    trace = []
    for m in turn:
        if not isinstance(m, ToolMessage):
            continue
        if m.name in CITING_TOOLS:
            for c in _citations_from(m.name, tool_text(m.content)):
                citations.setdefault(c["chunk_id"], c)
        args = calls.get(m.tool_call_id, {}).get("args", {})
        trace.append({"tool": m.name, "args": args, "error": m.status == "error"})
    answer = next(
        (_text(m.content).strip() for m in reversed(turn) if isinstance(m, AIMessage) and not m.tool_calls),
        "",
    )
    return answer, list(citations.values())[:MAX_CITATIONS], trace


EDIT_TOOLS = frozenset(
    {"start_draft", "resolve_item", "verify_draft", "commit_draft", "discard_draft", "generate_passage"}
)
BIG_PICTURE_TOOLS = frozenset({"search_communities", "get_chapter_summary"})


def route_of(trace: list[dict]) -> str:
    """How a turn was answered: `edit` if it used the draft tools, `global` for big-picture
    lookups, else `local`."""
    tools = {t["tool"] for t in trace}
    if tools & EDIT_TOOLS:
        return "edit"
    return "global" if tools & BIG_PICTURE_TOOLS else "local"


def read_retrieval(messages: list[BaseMessage]) -> dict[str, Any]:
    """Entity keys this turn's lookups touched: `seeds` (found by name or timeline) and `expanded`
    (reached through relations), with the number of hops walked."""
    seeds: dict[str, None] = {}
    expanded: dict[str, None] = {}
    hops = 0
    for m in _this_turn(messages):
        if not isinstance(m, ToolMessage) or m.status == "error":
            continue
        try:
            data = json.loads(tool_text(m.content))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if m.name == "find_entities":
            seeds.update({e["key"]: None for e in data.get("entities", []) if "key" in e})
            continue
        if m.name in ("get_neighbors", "get_relations", "get_timeline"):
            if isinstance(data.get("entity"), dict) and data["entity"].get("key"):
                seeds[data["entity"]["key"]] = None
        for n in data.get("neighbors", []):
            expanded[n["entity"]["key"]] = None
            hops = max(hops, n.get("hop", 1))
        for r in data.get("relations", []):
            if isinstance(r.get("other"), dict) and r["other"].get("key"):
                expanded[r["other"]["key"]] = None
                hops = max(hops, 1)
    return {"seeds": list(seeds), "expanded": [k for k in expanded if k not in seeds], "hops": hops}


@register_agent(NAME, "Answers questions about a book and builds verified 'what if' versions of it")
async def run(
    book_id: str,
    session_id: str,
    question: str,
    *,
    llm: BaseChatModel | None = None,
    checkpointer: Checkpointer = ...,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """One user message in a chat session.

    Returns {answer, citations, trace, route, retrieval, message_ids}. `on_progress(label)` is
    called as the agent starts each tool. Pass `checkpointer=None` to run without saved memory.
    """
    session = sessions.get_session(session_id)
    if session is None or session.book_id != book_id:
        raise ValueError(f"unknown session '{session_id}' for book '{book_id}'")
    if checkpointer is ...:
        checkpointer = get_checkpointer()

    log.info("message received session=%s book=%s storyline=%s chars=%d",
             session_id, book_id, session.storyline_id, len(question))
    started = time.perf_counter()
    try:
        async with open_tools(book_id, session_id) as tools:
            agent = build_agent(llm or get_chat_model(), tools, checkpointer, on_progress)
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=question)]},
                {"configurable": {"thread_id": session_id}},
            )
    except Exception:
        log.exception("agent failed session=%s after %.1fs", session_id, time.perf_counter() - started)
        raise
    answer, citations, trace = read_turn(result["messages"])
    log.info("reply ready session=%s in %.1fs tool_calls=%d citations=%d chars=%d todos=%d",
             session_id, time.perf_counter() - started, len(trace), len(citations), len(answer),
             len(result.get("todos") or []))
    if not answer:
        log.warning("agent returned no answer session=%s (call limit reached?)", session_id)

    route, retrieval = route_of(trace), read_retrieval(result["messages"])
    asked = sessions.add_message(Message(session_id=session_id, role="user", content=question))
    replied = sessions.add_message(
        Message(session_id=session_id, role="assistant", content=answer, citations=citations,
                trace=trace, route=route, retrieval=retrieval)
    )
    if session.title is None:
        sessions.set_title(session_id, question[:TITLE_CHARS])
    return {
        "answer": answer,
        "citations": citations,
        "trace": trace,
        "route": route,
        "retrieval": retrieval,
        "message_ids": {"user": asked.id, "assistant": replied.id},
    }
