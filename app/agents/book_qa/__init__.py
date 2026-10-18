"""The book Q&A agent: one ReAct agent over the MCP tools, with MongoDB checkpointing.

The model decides which tools to call and when it has enough to answer. The conversation lives
in the checkpointer (one thread per chat session); older turns are summarised once the history
passes `CHAT_HISTORY_BUDGET`. Each question and answer is also saved to the `messages` collection
for the UI, with the passages the answer was built from and the graph nodes it looked at.

`stream` yields progress while the agent works (a phase per tool call, a step once each tool
returns, then the answer's tokens); `run` is the same turn without the progress. A step is the
trace entry for one tool call: the tool, its args, why the model called it (the sentence it
writes first) and what came back. After the answer, `explain` adds why it holds: the chain of
graph relations it rests on.
"""

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Checkpointer

from app.agents.book_qa.explain import explain as explain_answer
from app.agents.book_qa.prompt import SYSTEM_PROMPT
from app.agents.logging_mw import ToolCallLogger
from app.agents.planning import PlanningMiddleware
from app.memory.service import build_memory_context, extract_and_update_memory
from app.agents.registry import register_agent
from app.agents.tools import CONNECT_INKITT, open_tools, tool_text
from app.config import get_settings
from app.constants import (
    CITATION_QUOTE_CHARS,
    HISTORY_KEEP_MESSAGES,
    MAX_CITATIONS,
    MAX_MODEL_CALLS,
    TITLE_CHARS,
    TRACE_FOUND_NAMES,
    TRACE_TEXT_CHARS,
)
from app.llm import get_chat_model
from app.repo import sessions, storylines
from app.repo.checkpoints import get_checkpointer
from app.repo.models import Message, utcnow
from app.storyline.diff import diff_out

NAME = "book_qa"
log = logging.getLogger(__name__)
# Tools whose results hold passages to cite, and the result field they are in. A rewritten chapter
# from read_chapter is a single passage at the top level of its result (field None).
CITING_TOOLS = {"get_chunks": "chunks", "search_chunks": "results", "retrieve": "passages",
                "read_chapter": None}  # fmt: skip
EDIT_TOOLS = frozenset(
    {"start_draft", "resolve_item", "verify_draft", "commit_draft", "discard_draft", "generate_passage"}
)
BIG_PICTURE_TOOLS = frozenset({"search_communities", "get_chapter_summary"})
# Set in a turn's `actions` when the UI should ask the user for their Inkitt token.
INKITT_TOKEN_REQUIRED = "inkitt_token_required"
# What the UI shows while a tool runs. Tools not listed show DEFAULT_PHASE.
PHASES = {
    "retrieve": "Searching the graph and the text…",
    "find_entities": "Finding the characters and places…",
    "get_neighbors": "Walking the graph…",
    "get_relations": "Walking the graph…",
    "find_path": "Tracing how they connect…",
    "search_communities": "Reading the big picture…",
    "get_chunks": "Reading the passages…",
    "search_chunks": "Searching the text…",
    "get_chapter_summary": "Reading the chapter…",
    "get_timeline": "Following the timeline…",
    "save_note": "Saving your note…",
    "connect_inkitt": "Checking your Inkitt connection…",
    "publish_to_inkitt": "Sending the story to Inkitt…",
    "read_chapter": "Reading the chapter…",
    "get_dependents": "Seeing what depends on it…",
    "get_state_at": "Checking where everyone stands…",
    "start_draft": "Starting the alternate version…",
    "get_open_items": "Checking what is left to decide…",
    "resolve_item": "Updating the story…",
    "verify_draft": "Checking the story still holds together…",
    "commit_draft": "Saving the new version…",
    "discard_draft": "Discarding the draft…",
    "generate_passage": "Writing the chapter…",
    "write_todos": "Planning the work…",
    "read_todos": "Checking the plan…",
    "think_tool": "Thinking it through…",
}
DEFAULT_PHASE = "Working…"
FIRST_PHASE = "Reading your question…"
MODEL_NODE = "model"  # create_agent's LLM node; other nodes (summarisation) never stream
# Nodes whose updates are this turn's work. Middleware nodes are skipped: summarisation re-sends
# the kept history as its update, which would put earlier calls in this turn's trace.
TURN_NODES = (MODEL_NODE, "tools")


def build_agent(
    llm: BaseChatModel, tools: list[BaseTool], checkpointer: Checkpointer = None,
    system_prompt: str = SYSTEM_PROMPT,
):
    """The ReAct agent around `llm` and already-bound `tools` and per-request system context, plus the planning tools."""
    return create_agent(
        llm,
        tools,
        system_prompt=system_prompt,
        middleware=[
            SummarizationMiddleware(
                model=llm,
                trigger=("tokens", get_settings().chat_history_budget),
                keep=("messages", HISTORY_KEEP_MESSAGES),
            ),
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS),
            PlanningMiddleware(),
            ToolCallLogger(),
        ],
        checkpointer=checkpointer,
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in content)


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _citations_from(tool: str, text: str) -> list[dict]:
    """Chunk references (id, chapter, pages, a short quote) from a passage-returning tool."""
    data, field = _json(text), CITING_TOOLS[tool]
    items = data.get(field, []) if field else ([data] if data.get("source") == "storyline" else [])
    out = []
    for i in items:
        if not isinstance(i, dict) or not i.get("chunk_id"):
            continue
        ref = {k: i.get(k) for k in ("chunk_id", "chapter", "page_start", "page_end", "seq")}
        if field is None:  # a chapter the storyline rewrote: cited as text it wrote, not as a page
            ref["storyline_chunk"] = True
        quote = " ".join(str(i.get("snippet") or i.get("text") or "").split())
        ref["quote"] = quote[:CITATION_QUOTE_CHARS] + ("…" if len(quote) > CITATION_QUOTE_CHARS else "")
        out.append(ref)
    return out


def _graph_refs_from(tool: str, text: str, refs: dict[str, list]) -> None:
    """Add the entity keys, relation ids and community ids a tool result names to `refs`."""
    data = _json(text)
    if tool in ("retrieve", "find_entities"):
        refs["seeds"] += [e["key"] for e in data.get("entities", []) if "key" in e]
    if tool == "retrieve":
        for r in data.get("relations", []):
            refs["expanded"] += [k for k in (r.get("src_key"), r.get("dst_key")) if k]
            refs["edges"] += [r["id"]] if r.get("id") else []
    if tool == "get_neighbors":
        refs["seeds"] += [data["entity"]["key"]] if "key" in data.get("entity", {}) else []
        refs["expanded"] += [n["entity"]["key"] for n in data.get("neighbors", [])]
    if tool in ("get_relations", "get_timeline"):
        refs["seeds"] += [data["entity"]["key"]] if "key" in data.get("entity", {}) else []
        refs["expanded"] += [
            r["other"]["key"] for r in data.get("relations", []) if isinstance(r.get("other"), dict)
        ]
    if tool in ("retrieve", "search_communities"):
        refs["communities"] += [c["cid"] for c in data.get("communities", []) if "cid" in c]


def _clip(text: str, limit: int = TRACE_TEXT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _name(item: Any) -> str:
    """How a trace names one item of a tool result: an entity, relation, passage, event..."""
    if not isinstance(item, dict):
        return _clip(str(item), 40)
    if item.get("rel") and item.get("src"):
        return f"{item['src']} {item['rel']} {item.get('dst', '?')}"
    for key in ("name", "title", "summary"):
        if item.get(key):
            return _clip(str(item[key]), 60)
    if item.get("chunk_id"):  # a passage
        page = item.get("page_start")
        return ", ".join(str(p) for p in (item.get("chapter"), page and f"p. {page}") if p)
    return _clip(str(item.get("rel") or item.get("key") or ""), 60)


def found_text(text: str, error: bool) -> str:
    """A one-line account of what a tool returned, e.g. "2 entities (Napoleon, Snowball); ..."."""
    if error:
        return "Failed: " + _clip(text)
    data = _json(text)
    if not data:
        return _clip(text) or "Nothing came back."
    parts = []
    for key, value in data.items():
        label = key.replace("_", " ")
        if isinstance(value, list) and value:
            names = [n for n in map(_name, value[:TRACE_FOUND_NAMES]) if n]
            more = ", …" if len(value) > TRACE_FOUND_NAMES else ""
            listed = f" ({', '.join(names)}{more})" if names else ""
            parts.append(f"{len(value)} {label}{listed}")
        elif isinstance(value, dict) and (name := _name(value)):
            parts.append(f"{label}: {name}")
        elif isinstance(value, bool) and key != "semantic":
            parts.append(f"{label}: {'yes' if value else 'no'}")
        elif isinstance(value, str) and key in ("note", "summary", "title", "message"):
            parts.append(_clip(value))
    return "; ".join(parts) or "Nothing relevant."


def trace_entry(call: dict, why: str, result: ToolMessage) -> dict[str, Any]:
    """The trace entry for one tool call: {tool, args, why, found, error}."""
    error = result.status == "error"
    return {
        "tool": result.name,
        "args": call.get("args", {}),
        "why": why,
        "found": found_text(tool_text(result.content), error),
        "error": error,
    }


def _calls(messages: list[BaseMessage]) -> dict[str, tuple[dict, str]]:
    """Tool call id -> (the call, the text the model wrote alongside it)."""
    return {
        c["id"]: (c, _clip(_text(m.content), 400))
        for m in messages if isinstance(m, AIMessage) for c in m.tool_calls
    }  # fmt: skip


def _this_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Messages after the last human message: the model's work on the current question."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i + 1 :]
    return messages


def read_turn(messages: list[BaseMessage]) -> tuple[str, list[dict], list[dict], dict[str, list]]:
    """(answer, citations, tool-call trace, graph refs) for the question just answered."""
    turn = _this_turn(messages)
    calls = _calls(turn)
    citations: dict[str, dict] = {}
    trace = []
    refs: dict[str, list] = {"seeds": [], "expanded": [], "edges": [], "communities": []}
    for m in turn:
        if not isinstance(m, ToolMessage):
            continue
        text = tool_text(m.content)
        if m.name in CITING_TOOLS:
            for c in _citations_from(m.name, text):
                citations.setdefault(c["chunk_id"], c)
        if m.status != "error":
            _graph_refs_from(m.name, text, refs)
        call, why = calls.get(m.tool_call_id, ({}, ""))
        trace.append(trace_entry(call, why, m))
    answer = next(
        (_text(m.content).strip() for m in reversed(turn) if isinstance(m, AIMessage) and not m.tool_calls),
        "",
    )
    refs = {k: list(dict.fromkeys(v)) for k, v in refs.items()}
    return answer, list(citations.values())[:MAX_CITATIONS], trace, refs


def _tool_names(chunk: Any) -> list[str]:
    """Tools a streamed model message starts calling (chunks while streaming, else full calls)."""
    calls = getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None) or []
    return [c["name"] for c in calls if c.get("name")]


async def _stream(
    book_id: str,
    session_id: str,
    question: str,
    *,
    llm: BaseChatModel | None = None,
    checkpointer: Checkpointer = ...,
) -> AsyncIterator[tuple[str, Any]]:
    """Answer one question, yielding ("phase", label), ("step", trace entry), ("token", text)
    while working, ("reasoning", chain) once the answer is written if it has one, and finally
    ("done", result). `result` is what `run` returns.

    Tokens are the model's text as it writes. A model call that goes on to call tools streams its
    reasoning first, which comes back as that call's step `why`; so a phase means the tokens so far
    were reasoning, and `result["answer"]` is the answer.
    """
    session = sessions.get_session(session_id)
    if session is None or session.book_id != book_id:
        raise ValueError(f"unknown session '{session_id}' for book '{book_id}'")
    if checkpointer is ...:
        checkpointer = get_checkpointer()
    before = storylines.get_storyline(session.storyline_id) if session.storyline_id else None
    committed_before = before.committed_seq if before else 0  # with the time, tells what this turn committed
    divergence = before.draft.divergence_pos if before and before.draft else None
    turn_started = utcnow()

    memory_context = build_memory_context(session.user_id, book_id, session.storyline_id)
    system_prompt = SYSTEM_PROMPT
    if memory_context:
        system_prompt += "\n\n" + memory_context + (
            "\nApply preferences when relevant, but the current user request takes precedence. "
            "Memories are not book evidence; verify book claims with the tools and cite passages."
        )

    yield "phase", FIRST_PHASE
    question_msg = HumanMessage(content=question)
    turn: list[BaseMessage] = [question_msg]
    model = llm or get_chat_model()
    async with open_tools(book_id, session_id, session.user_id, session.storyline_id) as tools:
        agent = build_agent(model, tools, checkpointer, system_prompt=system_prompt)
        async for mode, data in agent.astream(
            {"messages": [question_msg]},
            {"configurable": {"thread_id": session_id}},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, meta = data
                if meta.get("langgraph_node") != MODEL_NODE or not isinstance(chunk, AIMessage):
                    continue
                for name in _tool_names(chunk):
                    yield "phase", PHASES.get(name, DEFAULT_PHASE)
                if text := _text(chunk.content):
                    yield "token", text
            else:  # "updates": {node: state update}; collect this turn's messages
                for node, update in data.items():
                    if node not in TURN_NODES or not isinstance(update, dict):
                        continue
                    for m in update.get("messages") or []:
                        if isinstance(m, AIMessage | ToolMessage):
                            turn.append(m)
                        if isinstance(m, ToolMessage):
                            call, why = _calls(turn).get(m.tool_call_id, ({}, ""))
                            yield "step", trace_entry(call, why, m)

    answer, citations, trace, refs = read_turn(turn)
    results = [
        _json(tool_text(m.content))
        for m in turn
        if isinstance(m, ToolMessage) and m.status != "error"
    ]
    reasoning = await explain_answer(model, book_id, question, answer, results)
    if reasoning:
        yield "reasoning", reasoning
    actions = []
    if session.user_id and any(t["tool"] == CONNECT_INKITT for t in trace):
        actions.append(INKITT_TOKEN_REQUIRED)

    # An edit may have bound this chat to a storyline (start_draft creates one): look it up again.
    session = sessions.get_session(session_id) or session
    line = storylines.get_storyline(session.storyline_id) if session.storyline_id else None
    diff = diff_out(line, committed_before, turn_started, divergence) if line else None
    user_msg = sessions.add_message(Message(session_id=session_id, role="user", content=question))
    assistant_msg = sessions.add_message(
        Message(session_id=session_id, role="assistant", content=answer, citations=citations,
                trace=trace, graph_refs=refs, reasoning=reasoning, route=route_of(trace), diff=diff)
    )  # fmt: skip
    if session.title is None:
        sessions.set_title(session_id, question[:TITLE_CHARS])

    # Long-term memory is best-effort: an extraction failure must not fail the chat turn.
    memory_updates = await extract_and_update_memory(
        model, user_id=session.user_id, session_id=session_id, book_id=book_id,
        storyline_id=session.storyline_id, question=question, answer=answer,
    )
    yield "done", {
        "answer": answer,
        "citations": citations,
        "trace": trace,
        "graph_refs": refs,
        "reasoning": reasoning,
        "actions": actions,
        # Structured memory delta is separate from the user-facing answer.
        "memory_updates": memory_updates,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "storyline_id": line.id if line else None,
    }




def route_of(trace: list[dict]) -> str:
    """How a turn was answered: `edit` if it used the draft tools, `global` for big-picture
    lookups, else `local`."""
    tools = {t["tool"] for t in trace}
    if tools & EDIT_TOOLS:
        return "edit"
    return "global" if tools & BIG_PICTURE_TOOLS else "local"


async def stream(
    book_id: str,
    session_id: str,
    question: str,
    *,
    llm: BaseChatModel | None = None,
    checkpointer: Checkpointer = ...,
) -> AsyncIterator[tuple[str, Any]]:
    """`_stream`, with a log line when a message arrives, when the reply is ready and if it fails."""
    log.info("message received session=%s book=%s chars=%d", session_id, book_id, len(question))
    started = time.perf_counter()
    try:
        async for kind, value in _stream(book_id, session_id, question, llm=llm, checkpointer=checkpointer):
            if kind == "done":
                log.info(
                    "reply ready session=%s in %.1fs tool_calls=%d citations=%d chars=%d route=%s",
                    session_id, time.perf_counter() - started, len(value["trace"]),
                    len(value["citations"]), len(value["answer"]), value["assistant_message"].route,
                )
                if not value["answer"]:
                    log.warning("agent returned no answer session=%s (call limit reached?)", session_id)
            yield kind, value
    except Exception:
        log.exception("agent failed session=%s after %.1fs", session_id, time.perf_counter() - started)
        raise

@register_agent(NAME, "Answers questions about a book with citations, using the MCP tools")
async def run(
    book_id: str,
    session_id: str,
    question: str,
    *,
    llm: BaseChatModel | None = None,
    checkpointer: Checkpointer = ...,
) -> dict[str, Any]:
    """Answer one question in a chat session. Returns {answer, citations, trace, graph_refs,
    reasoning, actions, user_message, assistant_message}.

    Pass `checkpointer=None` to run without saved conversation memory.
    """
    result: dict[str, Any] = {}
    async for kind, value in stream(book_id, session_id, question, llm=llm, checkpointer=checkpointer):
        if kind == "done":
            result = value
    return result
