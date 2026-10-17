"""Load the MCP tools for one question, with `book_id` and `session_id` filled in for the model.

The model sees tools without those two arguments and cannot set them: each wrapper adds the real
values on every call, replacing anything the model passed.
"""

import copy
import json
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app.config import Settings, get_settings
from app.constants import AGENT_EXCLUDED_TOOLS

SERVER_NAME = "whatif"
INJECTED_ARGS = ("book_id", "session_id")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def tool_text(result: Any) -> str:
    """A tool result as text: MCP adapters return a string or a list of content blocks."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(
            b if isinstance(b, str) else str(b.get("text", "")) for b in result if b
        )
    return json.dumps(result, default=str)


def _schema(tool: BaseTool) -> dict[str, Any]:
    schema = tool.args_schema
    if isinstance(schema, dict):
        return copy.deepcopy(schema)
    return schema.model_json_schema() if schema is not None else {"type": "object", "properties": {}}


def hide_args(tool: BaseTool, values: Mapping[str, str]) -> BaseTool:
    """`tool` without the arguments in `values`; they are supplied on every call instead."""
    hidden = [name for name in values if name in _schema(tool).get("properties", {})]
    schema = _schema(tool)
    for name in hidden:
        schema["properties"].pop(name, None)
    schema["required"] = [r for r in schema.get("required", []) if r not in hidden]
    schema.pop("title", None)
    fixed = {name: values[name] for name in hidden}

    async def run(**kwargs: Any) -> str:
        return tool_text(await tool.ainvoke({**kwargs, **fixed}))

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=schema,
        coroutine=run,
        handle_tool_error=True,  # a bad argument becomes a message the model can react to
    )


def _connection(settings: Settings) -> dict[str, Any]:
    if settings.mcp_server_url:
        return {"transport": "streamable_http", "url": settings.mcp_server_url}
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "app.mcp_server"],
        "env": dict(os.environ),
        "cwd": str(PROJECT_ROOT),  # so the server finds .env
    }


@asynccontextmanager
async def open_tools(book_id: str, session_id: str) -> AsyncIterator[list[BaseTool]]:
    """The agent's tools for one question. One server connection is held while it runs."""
    client = MultiServerMCPClient({SERVER_NAME: _connection(get_settings())})
    values = {"book_id": book_id, "session_id": session_id}
    async with client.session(SERVER_NAME) as session:
        tools = await load_mcp_tools(session)
        yield [hide_args(t, values) for t in tools if t.name not in AGENT_EXCLUDED_TOOLS]
