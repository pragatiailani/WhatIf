"""What an editing turn changed in a storyline, in the terms the reading page shows.

The reader sees a change in two steps: while the draft waits for their approval it is a proposal
(`proposal_diff`), and once committed it is what the turn changed (`diff_out`)."""

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


def _diff(sl: Storyline, after: int, upto: int, passage: str, divergence: float | None) -> dict[str, Any]:
    ops = storylines.list_ops(sl.id, after_seq=after, upto_seq=upto)
    added = [o.payload["event"] for o in ops if o.kind == "add_event"]
    if divergence is None:
        divergence = min((e["story_pos"] for e in added), default=0)
    return {
        "divergence_pos": divergence,
        "added": [e["key"] for e in added],
        "hidden": [o.payload["key"] for o in ops if o.kind == "hide_node"],
        "modified": [],
        "added_edges": [],
        "passage": passage,
        "op_seq": upto,
    }


def diff_out(
    sl: Storyline, committed_before: int, since: datetime | None = None, divergence: float | None = None
) -> dict[str, Any] | None:
    """What this turn committed: the ops after op `committed_before`, and the chapters written
    since `since` (committing writes them). None if the turn committed nothing. `passage` is the
    start of the first chapter written, so the reader sees how the story now reads. `divergence` is
    the committed draft's, which the storyline no longer holds."""
    if sl.committed_seq == committed_before:
        return None
    written = [p for p in storylines.list_passages(sl.id) if since and p.created_at >= since]
    passage = _preview(written[0]) if written else ""
    return _diff(sl, committed_before, sl.committed_seq, passage, divergence)


def proposal_diff(sl: Storyline) -> dict[str, Any]:
    """What the open draft would change if the reader accepts it."""
    divergence = sl.draft.divergence_pos if sl.draft else None
    return _diff(sl, sl.committed_seq, sl.head_seq, "", divergence)
