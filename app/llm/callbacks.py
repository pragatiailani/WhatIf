"""Log every chat-model call: how long it took and how many tokens it used. No prompt text."""

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

log = logging.getLogger("app.llm")


def _usage(response: LLMResult) -> tuple[int | None, int | None]:
    """(input tokens, output tokens) if the provider reported them."""
    for generations in response.generations:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                return usage.get("input_tokens"), usage.get("output_tokens")
    raw = (response.llm_output or {}).get("token_usage") or {}
    return raw.get("prompt_tokens"), raw.get("completion_tokens")


class LlmCallLogger(BaseCallbackHandler):
    def __init__(self, model: str = "?"):
        self.model = model
        self._starts: dict[UUID, float] = {}

    def _start(self, run_id: UUID) -> None:
        self._starts[run_id] = time.perf_counter()

    def _ms(self, run_id: UUID) -> int:
        started = self._starts.pop(run_id, None)
        return round((time.perf_counter() - started) * 1000) if started else -1

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **_: Any) -> None:
        self._start(run_id)

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID, **_: Any) -> None:
        self._start(run_id)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **_: Any) -> None:
        tokens_in, tokens_out = _usage(response)
        log.info("llm call model=%s in %dms tokens_in=%s tokens_out=%s",
                 self.model, self._ms(run_id), tokens_in, tokens_out)  # fmt: skip

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **_: Any) -> None:
        log.warning("llm call failed model=%s after %dms: %s", self.model, self._ms(run_id), repr(error)[:300])
