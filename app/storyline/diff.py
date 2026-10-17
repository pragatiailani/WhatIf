"""What an editing turn changed in a storyline, in the terms the reading page shows."""

from datetime import datetime
from typing import Any

from app.constants import PASSAGE_PREVIEW_CHARS
from app.repo import storylines
from app.repo.models import Storyline, StorylineChunk


def _preview(passage: StorylineChunk) -> str:
    """The start of a rewritten chapter, with its title, for the reading page."""
    text = passage.text.strip()
    cut = text[:PASSAGE_PREVIEW_CHARS].rstrip()
    return f"{passage.chapter}\n\n{cut}" + ("…" if len(text) > len(cut) else "")


def diff_out(sl: Storyline, before: int, since: datetime | None = None) -> dict[str, Any] | None:
    """What this turn changed: the ops written after op `before`, and the chapters written since
    `since` (committing writes them). None if the turn changed nothing. `passage` is the start of
    the first chapter written, so the reader sees how the story now reads."""
    ops = storylines.list_ops(sl.id, after_seq=before, upto_seq=sl.head_seq)
    written = [p for p in storylines.list_passages(sl.id) if since and p.created_at >= since]
    if not ops and not written:
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
        "passage": _preview(written[0]) if written else "",
        "op_seq": sl.head_seq,
    }
