"""Authenticated endpoints for inspecting and managing persistent user memories."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import current_user
from app.repo import memories
from app.repo.models import User, UserMemory

router = APIRouter(prefix="/me/memories", tags=["user memory"])


class MemoryUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)


def _out(item: UserMemory) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.type,
        "key": item.key,
        "value": item.value,
        "scope_type": item.scope_type,
        "book_id": item.book_id,
        "storyline_id": item.storyline_id,
        "source": item.source,
        "confidence": item.confidence,
        "evidence_count": item.evidence_count,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


@router.get("")
def list_user_memories(user: Annotated[User, Depends(current_user)]) -> list[dict[str, Any]]:
    """List the signed-in user's active memories."""
    return [_out(item) for item in memories.list_memories(user.id)]


@router.put("/{memory_id}")
def update_user_memory(
    memory_id: str,
    body: MemoryUpdate,
    user: Annotated[User, Depends(current_user)],
) -> dict[str, Any]:
    item = memories.set_memory(user.id, memory_id, body.key.strip(), body.value.strip())
    if item is None:
        raise HTTPException(404, "memory not found")
    return _out(item)


@router.delete("/{memory_id}", status_code=204)
def delete_user_memory(memory_id: str, user: Annotated[User, Depends(current_user)]) -> None:
    if not memories.delete_memory(user.id, memory_id):
        raise HTTPException(404, "memory not found")


@router.delete("", status_code=200)
def clear_user_memories(user: Annotated[User, Depends(current_user)]) -> dict[str, int]:
    """Soft-delete all active memories for the signed-in user."""
    return {"deleted": memories.clear_memories(user.id)}
