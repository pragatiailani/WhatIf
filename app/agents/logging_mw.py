"""Log every tool call the agent makes: which tool, with what (clipped) arguments, and the outcome."""

import logging
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.logging_config import clip, fields

log = logging.getLogger("app.agent.tools")


def _outcome(result: Any) -> tuple[str, str]:
    """("ok" | "error", detail) for a tool result: a ToolMessage, or a Command updating state."""
    if isinstance(result, ToolMessage) and result.status == "error":
        return "error", clip(result.content, 300)
    return "ok", ""


class ToolCallLogger(AgentMiddleware):
    """Logs one line per tool call. Tool errors the model can recover from are warnings."""

    def _done(self, request: Any, started: float, result: Any = None, error: BaseException | None = None):
        call = request.tool_call
        ms = round((time.perf_counter() - started) * 1000)
        if error is not None:
            log.warning("tool=%s failed in %dms: %r %s", call["name"], ms, error, fields(call["args"]))
            return
        status, detail = _outcome(result)
        if status == "error":
            log.warning("tool=%s error in %dms: %s | %s", call["name"], ms, detail, fields(call["args"]))
        else:
            log.info("tool=%s ok in %dms %s", call["name"], ms, fields(call["args"]))

    def wrap_tool_call(self, request, handler):
        started = time.perf_counter()
        try:
            result = handler(request)
        except Exception as exc:
            self._done(request, started, error=exc)
            raise
        self._done(request, started, result)
        return result

    async def awrap_tool_call(self, request, handler):
        started = time.perf_counter()
        try:
            result = await handler(request)
        except Exception as exc:
            self._done(request, started, error=exc)
            raise
        self._done(request, started, result)
        return result
