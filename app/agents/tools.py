"""Load the MCP tools for one question, with `book_id` and `session_id` filled in for the model.

The model sees tools without those two arguments and cannot set them: each wrapper adds the real
values on every call, replacing anything the model passed.

A signed-in user with an Inkitt token also gets Inkitt's whitelisted tools and
`publish_to_inkitt`; without a token they get `connect_inkitt`, which makes the app ask for one.
"""

import copy
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession

from app import inkitt
from app.config import Settings, get_settings
from app.constants import AGENT_EXCLUDED_TOOLS, INKITT_AGENT_TOOLS
from app.repo import users
from app.repo.storylines import StorylinesUnavailable

log = logging.getLogger(__name__)

SERVER_NAME = "whatif"
INJECTED_ARGS = ("book_id", "session_id")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONNECT_INKITT = "connect_inkitt"
PUBLISH_TO_INKITT = "publish_to_inkitt"


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


def _connect_tool(message: str) -> BaseTool:
    async def connect_inkitt() -> str:
        return message

    return StructuredTool.from_function(
        coroutine=connect_inkitt,
        name=CONNECT_INKITT,
        args_schema={"type": "object", "properties": {}},
        description="Call this when the user wants anything done on Inkitt (publish this book or "
        "storyline, list or change their Inkitt stories) and no Inkitt tools are available: their Inkitt "
        "account is not connected yet. The app then asks them for their Inkitt access token.",
    )


def _publish_tool(
    session: ClientSession, book_id: str, user_id: str, storyline_id: str | None = None
) -> BaseTool:
    async def publish_to_inkitt(
        summary: str = "",
        story_type: Literal["original", "fanfiction", "public_domain"] = "original",
        age_rating: Literal["kids", "teenager", "adults"] | None = None,
        publish: bool = False,
    ) -> str:
        try:
            story = (
                inkitt.storyline_story(storyline_id) if storyline_id else inkitt.book_story(book_id)
            )
            if story.owner_id != user_id:
                return json.dumps({"error": "Only books the user uploaded and storylines they "
                                            "created can be sent to Inkitt."})
            out = await inkitt.publish_story(
                session, story, summary=summary, story_type=story_type,
                age_rating=age_rating, publish=publish,
            )
        except (inkitt.InkittError, StorylinesUnavailable, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(out)

    return StructuredTool.from_function(
        coroutine=publish_to_inkitt,
        name=PUBLISH_TO_INKITT,
        description="Send the story of this chat to the user's Inkitt account as a new Inkitt "
        "story: the book, or in a storyline chat the rewritten book (the original chapters "
        "before the storyline diverges, then the storyline's own). One Inkitt chapter per "
        "chapter, copied from the stored text. Chapters are drafts unless `publish` is true; set "
        "it only after the user has explicitly confirmed going live. Write `summary` yourself "
        "(a blurb of a few sentences, from search_communities). `story_type`: original for the "
        "user's own book, public_domain for classics, fanfiction for a storyline built on "
        "someone else's book. Each book or storyline is sent once; afterwards use the Inkitt "
        "tools on the returned story_id.",
    )


async def _inkitt_tools(
    stack: AsyncExitStack, book_id: str, user_id: str | None, storyline_id: str | None = None
) -> list[BaseTool]:
    if user_id is None:
        return [_connect_tool("The user is not signed in. Ask them to sign in to use Inkitt.")]
    pat = users.get_inkitt_pat(user_id)
    if pat is None:
        return [_connect_tool("Inkitt is not connected. The app is now asking the user for "
                              "their Inkitt access token; tell them to ask again once it is saved.")]
    try:
        session = await stack.enter_async_context(inkitt.open_session(pat))
        tools = await load_mcp_tools(session)
    except Exception:  # revoked token or Inkitt down: answer the question without Inkitt
        log.warning("could not connect to Inkitt for user %s", user_id, exc_info=True)
        return [_connect_tool("Inkitt rejected the saved token or could not be reached. The app "
                              "is asking the user for a new Inkitt access token.")]
    allowed = [hide_args(t, {}) for t in tools if t.name in INKITT_AGENT_TOOLS]
    return [*allowed, _publish_tool(session, book_id, user_id, storyline_id)]


@asynccontextmanager
async def open_tools(
    book_id: str, session_id: str, user_id: str | None = None, storyline_id: str | None = None
) -> AsyncIterator[list[BaseTool]]:
    """The agent's tools for one question. Server connections are held while it runs."""
    client = MultiServerMCPClient({SERVER_NAME: _connection(get_settings())})
    values = {"book_id": book_id, "session_id": session_id}
    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(client.session(SERVER_NAME))
        tools = [
            hide_args(t, values)
            for t in await load_mcp_tools(session)
            if t.name not in AGENT_EXCLUDED_TOOLS
        ]
        yield tools + await _inkitt_tools(stack, book_id, user_id, storyline_id)
