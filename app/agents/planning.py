"""Planning tools for agents: a todo list kept in agent state, and a reflection step.

These are LangChain tools attached to the agent through `PlanningMiddleware`, not MCP tools: they
act on the agent's own state, not on the book. The todo list lives in the agent state, so it is
checkpointed with the conversation (it survives turns and history summarisation).
"""

import logging
from typing import Annotated, Literal

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import OmitFromInput
from langchain.tools import InjectedState, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict

log = logging.getLogger(__name__)


WRITE_TODOS_DESCRIPTION = """Create and manage structured task lists for tracking progress through complex workflows.

## When to Use
- Multi-step or non-trivial tasks requiring coordination
- When user provides multiple tasks or explicitly requests todo list
- Avoid for single, trivial actions unless directed otherwise

## Structure
- Maintain one list containing multiple todo objects (content, status)
- Use clear, actionable content descriptions
- Status must be: pending, in_progress, or completed

## Best Practices
- Only one in_progress task at a time
- Mark completed immediately when task is fully done
- Always send the full updated list when making changes
- Prune irrelevant items to keep list focused

## Progress Updates
- Call write_todos again to change task status or edit content
- Reflect real-time progress; don't batch completions
- If blocked, keep in_progress and add new task describing blocker

## Parameters
- todos: List of TODO items with content and status fields

## Returns
Updates agent state with new todo list."""

TODO_USAGE_INSTRUCTIONS = """# TODO MANAGEMENT

Use TODO lists to plan and track progress through multi-step or complex tasks.

## When to Create TODOs
- Answering the request requires extensive thinking, reasoning and research.
- The request involves **multiple steps** or **several distinct sub-tasks**.
- The task is complex enough that you need to keep track of progress.
- The user explicitly asks for a plan or checklist.

## When NOT to Create TODOs
- Simple, single-step requests (for example "who is this character?").
- Quick lookups or one-off questions that can be answered immediately.

## Workflow
1. **Plan**: At the start of a complex task, use `write_todos` to create a clear list of steps. Batch related work into a single TODO item to keep the list focused.
2. **Execute**: Work through items one at a time. Mark the current task as `in_progress`.
3. **Track**: After completing a step, use `read_todos` to review remaining work, then use `write_todos` to mark it as `completed` and move to the next item.
4. **Reflect**: Use `think_tool` periodically to assess progress, evaluate results, and decide if the plan needs adjustment.
5. **Repeat** until all TODOs are completed.

## Thinking + Planning Coupling
- Treat `think_tool` and TODO planning as connected: after key execution steps, reflect with `think_tool` and then adjust TODOs as needed.
- If execution results differ from expectations, add corrective TODO items immediately and continue iteratively.
- When blocked, keep the active TODO `in_progress`, record the blocker in a new TODO item, and reflect before choosing the next action.

## Best Practices
- Only one `in_progress` task at a time.
- Keep TODO descriptions short and actionable.
- Update the list immediately when a task completes - don't batch updates.
- If you get blocked, keep the task `in_progress` and add a new TODO describing the blocker.
"""


class Todo(TypedDict):
    """A single TODO item with content and status."""

    content: str
    status: Literal["pending", "in_progress", "completed"]


class PlanningState(AgentState):
    """Agent state with the todo list. Hidden from the model's input; tools read and write it."""

    todos: Annotated[NotRequired[list[Todo]], OmitFromInput]


class WriteTodosSchema(BaseModel):
    todos: list[Todo] = Field(
        ...,
        description="List of TODO items with content and status.",
    )
    description: str = Field(..., description="Short description on why you are writing down todos")


class ReadTodosSchema(BaseModel):
    description: str = Field(..., description="Short description on why you are reading todos")


class ThinkToolSchema(BaseModel):
    reflection: str = Field(
        ...,
        description=(
            "Detailed reflection on current findings, missing gaps, quality, and "
            "the next decision."
        ),
    )


@tool(args_schema=WriteTodosSchema, description=WRITE_TODOS_DESCRIPTION)
async def write_todos(
    todos: list[Todo],
    description: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Create or update the agent's TODO list for task planning and tracking."""
    log.info("write_todos: %d todo(s) (%s)", len(todos), description)
    return Command(
        update={
            "todos": todos,
            "messages": [ToolMessage(f"Updated todo list to {todos}", tool_call_id=tool_call_id)],
        }
    )


@tool(args_schema=ReadTodosSchema)
async def read_todos(
    description: str,
    state: Annotated[dict, InjectedState],
) -> str:
    """Read the current TODO list from the agent state.

    Use it to review the list, stay focused on the remaining tasks, and track progress through
    complex workflows, for example after a long tool loop or when the conversation was summarised.
    """
    todos = state.get("todos", [])
    log.debug("read_todos: %d todo(s) (%s)", len(todos), description)
    if not todos:
        return "No todos currently in the list."
    lines = [f"{i}. {t['content']} ({t['status']})" for i, t in enumerate(todos, 1)]
    return "Current TODO List:\n" + "\n".join(lines)


@tool(args_schema=ThinkToolSchema)
async def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on progress and decision-making.

    Use this tool after receiving results to analyze them and plan next steps systematically.
    This creates a deliberate pause in the workflow for quality decision-making.

    When to use:
    - After receiving results: What key information did I find?
    - Before deciding next steps: Do I have enough to proceed or answer?
    - When assessing gaps: What specific information am I still missing?
    - Before concluding: Can I give a complete answer or finish this step now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence for a good result?
    4. Strategic decision - Should I continue working or provide my answer?

    Args:
        reflection: Your detailed reflection on progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    log.debug("think_tool: %s", reflection)
    return f"Reflection recorded: {reflection}"


PLANNING_TOOLS = [write_todos, read_todos, think_tool]


class PlanningMiddleware(AgentMiddleware):
    """Gives an agent the planning tools and the state field the todo list is stored in."""

    state_schema = PlanningState
    tools = PLANNING_TOOLS
