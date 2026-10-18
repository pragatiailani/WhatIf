"""Keeps tool calls and their results paired in what the model is sent.

A turn that crashes mid-tool-call (Inkitt dropping the connection, a server restart) leaves the saved
conversation with an AI message whose tool calls were never answered. Later questions are saved
after it, and the model API then rejects every call in that chat ("an assistant message with
'tool_calls' must be followed by tool messages..."). This middleware fills such gaps, and drops
results whose call is gone, in the messages of each model call. The saved history is left as is.
"""

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

log = logging.getLogger(__name__)

INTERRUPTED = "This tool call was interrupted before it finished; nothing came back."


def close_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """`messages` with an "interrupted" result after every unanswered tool call, and without
    tool results that answer no open call. Unchanged when every call is paired."""
    out: list[BaseMessage] = []
    open_calls: dict[str, str] = {}  # id -> tool name, for the latest AI message's calls

    def close() -> None:
        out.extend(
            ToolMessage(content=INTERRUPTED, tool_call_id=id_, name=name, status="error")
            for id_, name in open_calls.items()
        )
        open_calls.clear()

    for m in messages:
        if isinstance(m, ToolMessage):
            if open_calls.pop(m.tool_call_id, None) is not None:
                out.append(m)
            continue  # a result for no open call: the API rejects it too
        close()
        out.append(m)
        if isinstance(m, AIMessage):
            open_calls.update({c["id"]: c["name"] for c in m.tool_calls})
    close()
    return out


class ToolGapRepair(AgentMiddleware):
    """Sends the model `close_tool_calls(messages)` whenever a crash left them unpaired."""

    def _repair(self, request):
        fixed = close_tool_calls(request.messages)
        if len(fixed) == len(request.messages) and all(
            a is b for a, b in zip(fixed, request.messages, strict=True)
        ):
            return request
        log.warning("repaired unpaired tool calls in the model's messages (%d -> %d)",
                    len(request.messages), len(fixed))
        return request.override(messages=fixed)

    def wrap_model_call(self, request, handler):
        return handler(self._repair(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._repair(request))
