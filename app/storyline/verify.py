"""The verification gate. A draft can be committed only if this passes on its current version.

Two layers: deterministic checks over the storyline's graph view (nothing left open, nobody acts
after being recorded dead, nobody recorded alive is still killed), then an LLM judge that reads
each chapter the draft touches against the premise and the facts. Any failure to run a check
counts as a failure: the gate never passes by default.
"""

import logging
import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.constants import DEAD_STATUSES, MAX_VERIFY_ISSUES, TOOL_MAX_TIMELINE_EVENTS
from app.repo import graph, storylines
from app.repo.graph import GraphScope
from app.repo.models import Storyline
from app.storyline import ledger
from app.storyline.prompt import JUDGE_SYSTEM, judge_user
from app.storyline.schema import Continuity

log = logging.getLogger(__name__)


def _issue(kind: str, detail: str, ref: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "detail": detail, "ref": ref}


def _latest_status(sl: Storyline) -> dict[str, tuple[str, float]]:
    """{entity key: (status, story position it was set at)} from the branch facts."""
    latest: dict[str, tuple[str, float]] = {}
    for op in ledger.fact_ops(sl):
        if op.payload["attribute"] == "status":
            latest[op.payload["entity"]] = (op.payload["value"].strip().lower(), op.payload["from_pos"])
    return latest


def check_invariants(sl: Storyline, scope: GraphScope) -> list[dict[str, Any]]:
    issues = []
    open_n = storylines.count_items(sl.id, "open")
    if open_n:
        issues.append(_issue("open_items", f"{open_n} queue item(s) are not resolved yet"))
    for key, (status, pos) in _latest_status(sl).items():
        if status in DEAD_STATUSES:
            for event, role in graph.entity_events(scope, key):
                if event.story_pos > pos and role == "actor":
                    issues.append(
                        _issue("dead_acts", f"{key} is dead from pos {pos} but acts in: {event.summary}", event.key)
                    )
        else:
            for rel in graph.relations_involving(scope, [key], pos):
                if rel.rel == "KILLS" and rel.dst == key:
                    issues.append(
                        _issue("still_killed", f"{key} is '{status}' from pos {pos} but a KILLS relation "
                               f"at pos {rel.story_pos} is still visible", rel.id)
                    )  # fmt: skip
    return issues


def _chapters(sl: Storyline, scope: GraphScope) -> list[str]:
    """Chapters the draft touches: those of every queued item and every event added to it."""
    found: dict[str, float] = {}
    for item in storylines.list_items(sl.id):
        if item.chapter:
            found.setdefault(item.chapter, item.pos)
    for op in storylines.list_ops(sl.id, upto_seq=sl.head_seq):
        if op.kind == "add_event" and op.payload["event"].get("chapter"):
            event = op.payload["event"]
            found.setdefault(event["chapter"], event["story_pos"])
    return sorted(found, key=found.get)


def judge_chapter(
    sl: Storyline, scope: GraphScope, chapter: str, judge: Any
) -> list[dict[str, Any]]:
    rows = graph.chapter_events(scope, chapter)[:TOOL_MAX_TIMELINE_EVENTS]
    keys = {k for _, ks in rows for k in ks} | set(ledger.facts_at(sl))
    names = {e.key: e.name for e in graph.get_entities(scope, sorted(keys))}
    facts = "\n".join(
        f"- {names.get(k, k)}: " + ", ".join(f"{a} = {v}" for a, v in attrs.items())
        for k, attrs in ledger.facts_at(sl).items()
    )
    events = [
        f"- {'[NEW] ' if e.sid else ''}{e.key} (pos {e.story_pos}): {e.summary}"
        + (f" [{', '.join(names.get(k, k) for k in ks)}]" if ks else "")
        for e, ks in rows
    ]
    result: Continuity = judge.invoke(
        [SystemMessage(JUDGE_SYSTEM), HumanMessage(judge_user(sl.draft.premise, facts, chapter, events))]
    )
    return [
        _issue("continuity", f"{i.problem} Suggestion: {i.suggestion}".strip(), i.event_key)
        | {"chapter": i.chapter or chapter}
        for i in result.issues
    ]


def verify(sl: Storyline, llm: BaseChatModel) -> dict[str, Any]:
    """Run both layers, record the outcome against the draft's version and return it."""
    if sl.draft is None:
        raise ValueError("there is no open draft; call start_draft first")
    started = time.perf_counter()
    scope = storylines.scope(sl)
    issues = check_invariants(sl, scope)
    chapters = _chapters(sl, scope)
    log.info("verifying storyline=%s version=%s chapters=%d code_issues=%d",
             sl.id, sl.version, len(chapters), len(issues))
    judge = llm.with_structured_output(Continuity, method="function_calling")
    for chapter in chapters:
        try:
            issues += judge_chapter(sl, scope, chapter, judge)
        except Exception as exc:  # a chapter that could not be judged is not a passing chapter
            log.warning("continuity check failed storyline=%s chapter=%s: %r", sl.id, chapter, exc)
            issues.append(_issue("judge_error", f"could not check '{chapter}': {exc!r}"))
    result = {
        "passed": not issues,
        "version": sl.version,
        "chapters_checked": chapters,
        "issues": issues[:MAX_VERIFY_ISSUES],
        "total_issues": len(issues),
    }
    storylines.record_verification(sl.id, sl.version, result)
    kinds = {}
    for i in issues:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    log.log(logging.INFO if result["passed"] else logging.WARNING,
            "verification %s storyline=%s version=%s issues=%s in %.1fs",
            "passed" if result["passed"] else "FAILED", sl.id, sl.version, kinds or 0,
            time.perf_counter() - started)
    return result
