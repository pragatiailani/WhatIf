"""The book Q&A agent: one ReAct agent over the MCP tools, with MongoDB checkpointing.

The model decides which tools to call and when it has enough to answer. The conversation lives
in the checkpointer (one thread per chat session); older turns are summarised once the history
passes `CHAT_HISTORY_BUDGET`. Each question and answer is also saved to the `messages` collection
for the UI, with the passages the answer was built from.
"""

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Checkpointer

from app.agents.book_qa.prompt import SYSTEM_PROMPT
from app.agents.registry import register_agent
from app.agents.tools import CONNECT_INKITT, open_tools, tool_text
from app.config import get_settings
from app.constants import HISTORY_KEEP_MESSAGES, MAX_CITATIONS, MAX_MODEL_CALLS, TITLE_CHARS
from app.llm import get_chat_model
from app.repo import sessions
from app.repo.checkpoints import get_checkpointer
from app.repo.models import Message

NAME = "book_qa"
CITING_TOOLS = {"get_chunks": "chunks", "search_chunks": "results"}
# Set in a turn's `actions` when the UI should ask the user for their Inkitt token.
INKITT_TOKEN_REQUIRED = "inkitt_token_required"


def build_agent(llm: BaseChatModel, tools: list[BaseTool], checkpointer: Checkpointer = None):
    """The ReAct agent around `llm` and already-bound `tools`."""
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
        ],
        checkpointer=checkpointer,
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in content)


def _citations_from(tool: str, text: str) -> list[dict]:
    """Chunk references (id, chapter, pages) from a get_chunks / search_chunks result."""
    try:
        items = json.loads(text).get(CITING_TOOLS[tool], [])
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


@register_agent(NAME, "Answers questions about a book with citations, using the MCP tools")
async def run(
    book_id: str,
    session_id: str,
    question: str,
    *,
    llm: BaseChatModel | None = None,
    checkpointer: Checkpointer = ...,
) -> dict[str, Any]:
    """Answer one question in a chat session. Returns {answer, citations, trace, actions}.

    Pass `checkpointer=None` to run without saved conversation memory.
    """
    session = sessions.get_session(session_id)
    if session is None or session.book_id != book_id:
        raise ValueError(f"unknown session '{session_id}' for book '{book_id}'")
    if checkpointer is ...:
        checkpointer = get_checkpointer()

    async with open_tools(book_id, session_id, session.user_id, session.storyline_id) as tools:
        agent = build_agent(llm or get_chat_model(), tools, checkpointer)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            {"configurable": {"thread_id": session_id}},
        )
    answer, citations, trace = read_turn(result["messages"])
    actions = []
    if session.user_id and any(t["tool"] == CONNECT_INKITT for t in trace):
        actions.append(INKITT_TOKEN_REQUIRED)

    sessions.add_message(Message(session_id=session_id, role="user", content=question))
    sessions.add_message(
        Message(session_id=session_id, role="assistant", content=answer, citations=citations, trace=trace)
    )
    if session.title is None:
        sessions.set_title(session_id, question[:TITLE_CHARS])
    return {"answer": answer, "citations": citations, "trace": trace, "actions": actions}
