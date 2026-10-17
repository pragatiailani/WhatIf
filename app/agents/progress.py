"""Progress labels: tell the caller what the agent is doing, in words a reader understands.

The chat stream shows these while the agent works. The callback is called before each tool runs.
"""

import logging
from collections.abc import Callable

from langchain.agents.middleware import AgentMiddleware

log = logging.getLogger(__name__)

LABELS = {
    "find_entities": "Finding the characters…",
    "get_neighbors": "Following their connections…",
    "get_relations": "Looking at their relationships…",
    "find_path": "Tracing how they are linked…",
    "get_timeline": "Tracing their story…",
    "get_chapter_summary": "Reading the chapter overview…",
    "search_communities": "Gathering the big picture…",
    "get_chunks": "Reading the passages…",
    "search_chunks": "Searching the text…",
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
    "save_note": "Saving your note…",
}


def label_for(tool: str) -> str:
    return LABELS.get(tool, "Working on it…")


class ProgressReporter(AgentMiddleware):
    """Calls `on_progress(label)` before each tool call. A failing callback never breaks the agent."""

    def __init__(self, on_progress: Callable[[str], None]):
        self.on_progress = on_progress

    def _report(self, request) -> None:
        try:
            self.on_progress(label_for(request.tool_call["name"]))
        except Exception:
            log.exception("progress callback failed")

    def wrap_tool_call(self, request, handler):
        self._report(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._report(request)
        return await handler(request)
