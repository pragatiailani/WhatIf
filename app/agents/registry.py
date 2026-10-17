"""Registry of the LangGraph agents. Callers look agents up by name instead of importing them.

An agent is an async function `run(book_id, session_id, question, *, llm=None, checkpointer=None)`
returning a dict with at least `answer`. Register one with `@register_agent(name, description)`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

AgentRun = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    run: AgentRun


_AGENTS: dict[str, AgentSpec] = {}


def register_agent(name: str, description: str) -> Callable[[AgentRun], AgentRun]:
    def decorate(run: AgentRun) -> AgentRun:
        if name in _AGENTS:
            raise ValueError(f"agent '{name}' is already registered")
        _AGENTS[name] = AgentSpec(name=name, description=description, run=run)
        return run

    return decorate


def get_agent(name: str) -> AgentSpec:
    try:
        return _AGENTS[name]
    except KeyError:
        raise KeyError(f"unknown agent '{name}'; available: {', '.join(sorted(_AGENTS))}") from None


def list_agents() -> list[AgentSpec]:
    return sorted(_AGENTS.values(), key=lambda a: a.name)
