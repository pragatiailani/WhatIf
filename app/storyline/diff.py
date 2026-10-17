"""What an editing turn changed in a storyline, in the terms the reading page shows."""

from typing import Any

from app.repo import storylines
from app.repo.models import Storyline


def diff_out(sl: Storyline, before: int) -> dict[str, Any] | None:
    """What the ops written after op `before` changed. None if there were none."""
    ops = storylines.list_ops(sl.id, after_seq=before, upto_seq=sl.head_seq)
    if not ops:
        return None
    added = [o.payload["event"] for o in ops if o.kind == "add_event"]
    positions = [e["story_pos"] for e in added]
    divergence = sl.draft.divergence_pos if sl.draft else (min(positions) if positions else 0)
    return {
        "divergence_pos": divergence,
        "added": [e["key"] for e in added],
        "hidden": [o.payload["key"] for o in ops if o.kind == "hide_node"],
        "modified": [],
        "added_edges": [],
        "passage": "",
        "op_seq": sl.head_seq,
    }
