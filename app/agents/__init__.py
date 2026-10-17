"""LangGraph agents. Importing this package registers them; use `get_agent(name).run(...)`."""

from app.agents import whatif  # noqa: F401  (registers "whatif")
from app.agents.registry import AgentSpec, get_agent, list_agents, register_agent

__all__ = ["AgentSpec", "get_agent", "list_agents", "register_agent"]
