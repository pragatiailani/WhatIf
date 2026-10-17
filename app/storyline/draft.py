"""The draft workflow: open a draft, work through its ripple queue, discard it or commit it.

A draft starts at an anchor event with a premise and some facts ("A is alive"). Every canon event
and relation after that point that involves an affected entity goes into the queue, and the
draft cannot be committed until each one is resolved (kept, hidden or rewritten) and the whole
storyline passes verification (`app.storyline.verify`). Resolving a change can name more affected
entities, which queues their later events too, so the ripple reaches the end of the story.

Ops go to Mongo before the graph (rule 2 of the README); hide ops need no graph write at all,
because hidden sets are read back from the op log.
"""

import logging
from collections.abc import Sequence
from typing import Any

from app.constants import MAX_QUEUE_ITEMS
from app.repo import graph, sessions, storylines
from app.repo.models import ChatSession, DraftState, GraphEvent, Storyline, StorylineItem, new_id
from app.storyline.schema import Fact

ACTIONS = ("keep", "hide", "rewrite")
log = logging.getLogger(__name__)


def _draft(sl: Storyline) -> DraftState:
    if sl.draft is None:
        raise ValueError("there is no open draft; call start_draft first")
    return sl.draft


def _fresh(storyline_id: str) -> Storyline:
    sl = storylines.get_storyline(storyline_id)
    if sl is None:
        raise ValueError(f"unknown storyline '{storyline_id}'")
    return sl


def _names(scope: graph.GraphScope, keys: Sequence[str]) -> dict[str, str]:
    return {e.key: e.name for e in graph.get_entities(scope, list(keys))}


def _set_facts(sl: Storyline, facts: Sequence[Fact], pos: float) -> None:
    for f in facts:
        storylines.append_op(
            sl.id,
            "set_fact",
            {"entity": f.entity, "attribute": f.attribute, "value": f.value, "from_pos": pos},
        )


def enqueue(sl: Storyline, keys: Sequence[str] | None = None) -> int:
    """Queue the events and relations after the divergence that involve `keys` (default: every
    affected entity). Items already queued are skipped; storyline-authored events are not queued.
    Returns how many were new."""
    draft = _draft(sl)
    keys = list(keys if keys is not None else draft.affected)
    if not keys:
        return 0
    scope = storylines.scope(sl)
    names = _names(scope, keys)
    items: list[StorylineItem] = []
    for event, involved in graph.events_involving(scope, keys, draft.divergence_pos):
        if event.sid is not None:
            continue
        who = ", ".join(names.get(k, k) for k in involved)
        items.append(
            StorylineItem(
                storyline_id=sl.id, kind="event", ref=event.key, pos=event.story_pos,
                summary=event.summary, chapter=event.chapter, reason=f"involves {who}",
            )
        )  # fmt: skip
    rels = [r for r in graph.relations_involving(scope, keys, draft.divergence_pos) if r.sid is None]
    pair = _names(scope, {k for r in rels for k in (r.src, r.dst)})
    for rel in rels:
        items.append(
            StorylineItem(
                storyline_id=sl.id, kind="relation", ref=rel.id, pos=rel.story_pos or 0.0,
                summary=f"{pair.get(rel.src, rel.src)} {rel.rel} {pair.get(rel.dst, rel.dst)}",
                reason="relation of an affected entity",
            )
        )  # fmt: skip
    items.sort(key=lambda i: i.pos)
    if len(items) > MAX_QUEUE_ITEMS:
        log.warning("ripple queue capped storyline=%s items=%d cap=%d", sl.id, len(items), MAX_QUEUE_ITEMS)
    added = storylines.add_items(items[:MAX_QUEUE_ITEMS])
    log.info("queued storyline=%s entities=%d new_items=%d", sl.id, len(keys), added)
    return added


def start_draft(
    session: ChatSession, anchor_event: str, premise: str, facts: Sequence[Fact]
) -> dict[str, Any]:
    """Open a draft on the session's storyline (created on first use). `facts` use entity keys."""
    sl = storylines.get_storyline(session.storyline_id) if session.storyline_id else None
    if sl is None:
        sl = storylines.create_storyline(
            Storyline(
                book_id=session.book_id, owner_id=session.user_id, name=premise[:60], premise=premise
            )
        )
        sessions.set_storyline(session.id, sl.id)
    elif sl.draft is not None:
        raise ValueError("a draft is already open: finish it (commit_draft) or discard_draft first")

    scope = storylines.scope(sl)
    anchor = graph.get_event(scope, anchor_event)
    if anchor is None:
        raise ValueError(f"no visible event '{anchor_event}'")
    involved = [k for k, _ in graph.event_links(scope, anchor.key)["involves"]]
    affected = list(dict.fromkeys([*(f.entity for f in facts), *involved]))

    storylines.set_draft(
        sl.id,
        DraftState(
            premise=premise, anchor_event=anchor.key, divergence_pos=anchor.story_pos, affected=affected
        ),
    )
    sl = _fresh(sl.id)
    _set_facts(sl, facts, anchor.story_pos)
    queued = enqueue(_fresh(sl.id))
    log.info("draft started storyline=%s session=%s anchor=%s pos=%s affected=%d facts=%d queued=%d",
             sl.id, session.id, anchor.key, anchor.story_pos, len(affected), len(facts), queued)
    return {
        "storyline_id": sl.id,
        "anchor": {"key": anchor.key, "summary": anchor.summary, "story_pos": anchor.story_pos},
        "affected": [{"key": k, "name": n} for k, n in _names(scope, affected).items()],
        "queued": queued,
    }


def open_items(sl: Storyline, limit: int) -> dict[str, Any]:
    """The next open items. An empty queue is topped up first, in case it was capped."""
    _draft(sl)
    items = storylines.list_items(sl.id, "open", limit)
    if not items and enqueue(sl):
        items = storylines.list_items(sl.id, "open", limit)
    return {"items": items, "open": storylines.count_items(sl.id, "open"),
            "total": storylines.count_items(sl.id)}  # fmt: skip


def resolve_item(
    sl: Storyline,
    ref: str,
    action: str,
    new_summary: str | None = None,
    participants: Sequence[str] | None = None,
    affects: Sequence[str] = (),
    facts: Sequence[Fact] = (),
) -> dict[str, Any]:
    """Decide one event or relation. `ref` is a queued item or any visible event (for example one
    the verifier flagged). A rewrite hides the event and adds a replacement at the same story
    position; `participants` (entity keys) replace its cast, `facts` hold from this position on,
    and `affects` (entity keys) pull those entities' later events into the queue."""
    _draft(sl)
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    scope = storylines.scope(sl)
    item = storylines.get_item(sl.id, ref)
    is_relation = item is not None and item.kind == "relation"
    event = None if is_relation else graph.get_event(scope, ref)
    if event is None and not is_relation:
        raise ValueError(f"'{ref}' is not a visible event or a queued relation (already hidden?)")
    if is_relation and action == "rewrite":
        raise ValueError("relations can only be kept or hidden")
    if item is None:  # an event outside the queue, e.g. flagged by verification
        storylines.add_items(
            [
                StorylineItem(
                    storyline_id=sl.id, kind="event", ref=event.key, pos=event.story_pos,
                    summary=event.summary, chapter=event.chapter, reason="reopened during editing",
                )
            ]
        )  # fmt: skip
    pos = item.pos if item else event.story_pos

    new_key = None
    if action == "hide":
        if is_relation:
            storylines.append_op(sl.id, "hide_edge", {"id": ref})
        else:
            storylines.append_op(sl.id, "hide_node", {"key": ref})
    elif action == "rewrite":
        if not (new_summary and new_summary.strip()):
            raise ValueError("rewrite needs new_summary")
        if participants is not None and len(graph.get_entities(scope, list(participants))) != len(
            set(participants)
        ):
            raise ValueError("participants must be existing entity keys")
        new_key = _rewrite(sl, scope, event, new_summary.strip(), participants)
    if facts:
        _set_facts(sl, facts, pos)

    sl = _fresh(sl.id)
    queued = 0
    if affects:
        new = storylines.add_affected(sl.id, [*affects, *(f.entity for f in facts)])
        queued = enqueue(_fresh(sl.id), new) if new else 0
    storylines.resolve_item(sl.id, ref, action)
    open_left = storylines.count_items(sl.id, "open")
    log.info("resolved storyline=%s ref=%s action=%s new_event=%s facts=%d affects=%d newly_queued=%d open=%d",
             sl.id, ref, action, new_key, len(facts), len(affects), queued, open_left)
    return {
        "resolved": ref,
        "action": action,
        "new_event": new_key,
        "newly_queued": queued,
        "open": open_left,
    }


def _rewrite(
    sl: Storyline,
    scope: graph.GraphScope,
    old: GraphEvent,
    summary: str,
    participants: Sequence[str] | None,
) -> str:
    links = graph.event_links(scope, old.key)
    roles = dict(links["involves"])
    cast = [(k, roles.get(k, "other")) for k in (participants if participants is not None else roles)]
    key = f"sl|{sl.id[:8]}|{new_id()[:10]}"
    event = {
        "key": key, "summary": summary, "story_pos": old.story_pos, "story_time": old.story_time,
        "chapter": old.chapter, "first_page": old.first_page,
    }  # fmt: skip
    storylines.append_op(sl.id, "hide_node", {"key": old.key})
    op = storylines.append_op(
        sl.id, "add_event", {"event": event, "involves": cast, "at": links["at"]}
    )
    sid = sl.id
    graph.upsert_events(
        sl.book_id, [GraphEvent(**event, sid=sid, created_by_op=op.seq)], branch=True
    )
    graph.upsert_event_links(
        sl.book_id,
        involves=[{"event": key, "entity": k, "role": r} for k, r in cast],
        at=[{"event": key, "place": p} for p in links["at"]],
        in_chapter=[{"event": key, "chapter": old.chapter}] if old.chapter else [],
    )
    return key


def discard(sl: Storyline) -> dict[str, Any]:
    """Drop everything since the last commit, in the log and in the graph."""
    _draft(sl)
    committed = storylines.drop_draft_ops(sl.id)
    graph.delete_storyline_items(sl.book_id, sl.id, committed)
    storylines.delete_items(sl.id)
    storylines.delete_passages_after(sl.id, committed)
    storylines.set_draft(sl.id, None)
    log.info("draft discarded storyline=%s rewound_to_op=%d", sl.id, committed)
    return {"discarded": True, "storyline_id": sl.id}


def commit(sl: Storyline) -> dict[str, Any]:
    """Make the draft part of the storyline. Only allowed right after a passing verification."""
    _draft(sl)
    if sl.verified_version != sl.version:
        log.warning("commit refused storyline=%s: not verified (version=%s verified=%s)",
                    sl.id, sl.version, sl.verified_version)
        raise ValueError(
            "this draft has changed since it was last verified (or never passed): "
            "run verify_draft and fix what it reports first"
        )
    if storylines.count_items(sl.id, "open"):
        log.warning("commit refused storyline=%s: items still open", sl.id)
        raise ValueError("there are still open items")
    storylines.mark_committed(sl.id)
    storylines.delete_items(sl.id)
    log.info("draft committed storyline=%s ops=%d", sl.id, _fresh(sl.id).committed_seq)
    return {"committed": True, "storyline_id": sl.id, "ops": _fresh(sl.id).committed_seq}
