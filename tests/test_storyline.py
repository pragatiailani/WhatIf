"""The storyline editing workflow, end to end over the tool functions.

FalkorDB is replaced by `MemGraph`, which applies the same visibility rules as `graph.visible()`
(sid, created_by_op, hidden sets); Mongo is mongomock.
"""

import pytest
from langchain_core.messages import AIMessage

from app.mcp_server import storyline_tools as tools
from app.repo import books, chunks, graph, sessions, storylines
from app.repo.graph import GraphScope
from app.repo.models import Book, ChatSession, Chunk, GraphEntity, GraphEvent, GraphRelation, PageSpan
from app.storyline import draft, ledger
from app.storyline import prose as prose_module
from app.storyline.schema import Continuity, ContinuityIssue, Fact

RAVI, ASHA, VIK = "ravi|character", "asha|character", "vik|character"


def ent(key, name):
    return GraphEntity(key=key, name=name, type="Character", mention_count=1)


def ev(key, summary, pos, chapter):
    return GraphEvent(key=key, summary=summary, story_pos=pos, chapter=chapter, first_page=int(pos // 1000) + 1)


class MemGraph:
    """Just enough of the graph repo for the storyline code, with its visibility rules."""

    def __init__(self):
        self.entities = {e.key: e for e in (ent(RAVI, "Ravi"), ent(ASHA, "Asha"), ent(VIK, "Vik"))}
        self.events = [
            ev("e1", "Ravi meets Asha", 0, "Chapter 1"),
            ev("e2", "Vik kills Ravi", 1000, "Chapter 2"),
            ev("e3", "Asha mourns Ravi at his funeral", 2000, "Chapter 3"),
            ev("e4", "Asha confronts Vik", 3000, "Chapter 4"),
        ]
        self.involves = {
            "e1": [(RAVI, "actor"), (ASHA, "actor")],
            "e2": [(VIK, "actor"), (RAVI, "target")],
            "e3": [(ASHA, "actor"), (RAVI, "other")],
            "e4": [(ASHA, "actor"), (VIK, "target")],
        }
        self.at = {}
        self.relations = [
            GraphRelation(id="r0", src=RAVI, dst=ASHA, rel="FRIEND_OF", story_pos=0),
            GraphRelation(id="r1", src=VIK, dst=RAVI, rel="KILLS", story_pos=1000),
        ]

    def ok(self, scope, item, key=None, id_=None):
        if scope.sid is None:
            return item.sid is None
        mine = item.sid is None or (item.sid == scope.sid and item.created_by_op <= scope.max_op)
        return mine and (key or "") not in scope.hidden_nodes and (id_ or "") not in scope.hidden_edges

    def events_in(self, scope):
        return sorted((e for e in self.events if self.ok(scope, e, key=e.key)), key=lambda e: e.story_pos)

    # reads
    def get_event(self, scope, key):
        return next((e for e in self.events_in(scope) if e.key == key), None)

    def get_entities(self, scope, keys):
        return [self.entities[k] for k in keys if k in self.entities]

    def get_entity(self, scope, key):
        return self.entities.get(key)

    def entity_index(self, scope):
        return [{"key": e.key, "name": e.name, "type": e.type, "aliases": [], "mention_count": 1}
                for e in self.entities.values()]  # fmt: skip

    def event_links(self, scope, key):
        return {"involves": list(self.involves.get(key, [])), "at": list(self.at.get(key, []))}

    def events_involving(self, scope, keys, pos_from):
        out = []
        for e in self.events_in(scope):
            hit = [k for k, _ in self.involves.get(e.key, []) if k in keys]
            if hit and e.story_pos >= pos_from:
                out.append((e, hit))
        return out

    def relations_in(self, scope):
        return [r for r in self.relations if self.ok(scope, r, id_=r.id)]

    def relations_involving(self, scope, keys, pos_from):
        return [r for r in self.relations_in(scope)
                if (r.src in keys or r.dst in keys) and (r.story_pos or 0) >= pos_from]  # fmt: skip

    def entity_events(self, scope, key):
        return [(e, dict(self.involves[e.key])[key]) for e in self.events_in(scope)
                if key in dict(self.involves.get(e.key, []))]  # fmt: skip

    def entity_relations(self, scope, key, limit=50, rel_types=None):
        return [(r, self.entities[r.dst if r.src == key else r.src]) for r in self.relations_in(scope)
                if key in (r.src, r.dst)][:limit]  # fmt: skip

    def book_graph(self, scope):
        events = [(e, [k for k, _ in self.involves.get(e.key, [])]) for e in self.events_in(scope)]
        return list(self.entities.values()), events, self.relations_in(scope)

    def chapter_events(self, scope, chapter):
        return [(e, [k for k, _ in self.involves.get(e.key, [])])
                for e in self.events_in(scope) if e.chapter == chapter]  # fmt: skip

    # writes
    def upsert_events(self, book_id, events, branch=False):
        assert branch
        self.events += list(events)

    def upsert_event_links(self, book_id, involves=(), at=(), before=(), in_chapter=()):
        for row in involves:
            self.involves.setdefault(row["event"], []).append((row["entity"], row["role"]))
        for row in at:
            self.at.setdefault(row["event"], []).append(row["place"])

    def delete_storyline_items(self, book_id, sid, after_op):
        keep = lambda x: not (x.sid == sid and x.created_by_op > after_op)  # noqa: E731
        self.events = [e for e in self.events if keep(e)]
        self.relations = [r for r in self.relations if keep(r)]


class FakeJudge:
    """Stands in for the chat model. The system prompt says which job it is asked to do: judge a
    chapter's events, rewrite a piece of text, or check a rewritten chapter."""

    def __init__(self):
        self.issues_by_chapter: dict[str, list[ContinuityIssue]] = {}
        self.asked: list[str] = []  # chapters judged for continuity
        self.rewrites: list[str] = []  # chapters rewritten, one entry per call
        self.prose_issues: list[list[str]] = []  # queued results of the rewritten-text check

    def with_structured_output(self, schema, **_):
        return self

    def invoke(self, messages):
        system, text = messages[0].content, messages[-1].content
        chapter = next(line[9:] for line in text.splitlines() if line.startswith("Chapter: ")) \
            if "Chapter: " in text else None  # fmt: skip
        if system.startswith("You rewrite part of a novel"):
            self.rewrites.append(chapter)
            return AIMessage(content=f"Rewritten {chapter}: Asha kept vigil beside the wounded Ravi.")
        if system.startswith("You check a rewritten chapter"):
            found = self.prose_issues.pop(0) if self.prose_issues else []
            return Continuity(issues=[ContinuityIssue(problem=p) for p in found])
        self.asked.append(chapter)
        return Continuity(issues=self.issues_by_chapter.get(chapter, []))


@pytest.fixture
def world(mongo, monkeypatch):
    mem = MemGraph()
    for name in ("get_event", "get_entities", "get_entity", "entity_index", "event_links",
                 "events_involving", "relations_involving", "entity_events", "entity_relations",
                 "chapter_events", "upsert_events", "upsert_event_links", "delete_storyline_items",
                 "book_graph"):  # fmt: skip
        monkeypatch.setattr(graph, name, getattr(mem, name))
    judge = FakeJudge()
    monkeypatch.setattr(tools, "get_chat_model", lambda: judge)
    monkeypatch.setattr(prose_module, "PROSE_WORKERS", 1)  # a predictable order for the fake model
    book = books.create_book(Book(title="Kim", content_hash="h"))
    session = sessions.create_session(ChatSession(book_id=book.id))
    texts = ["Ravi met Asha.", "Vik killed Ravi.", "Asha wept at the funeral of Ravi.", "Asha faced Vik."]
    chunks.insert_chunks([
        Chunk(book_id=book.id, seq=i, chapter=f"Chapter {i + 1}", text=t,
              page_spans=[PageSpan(page=i + 1, char_start=0, char_end=len(t))])
        for i, t in enumerate(texts)
    ])
    chunk_ids = chunks.ids_by_seq(book.id, range(4))

    class W:
        pass

    w = W()
    w.mem, w.judge, w.book, w.session = mem, judge, book.id, session.id
    w.chunk_ids = chunk_ids
    w.storyline = lambda: storylines.get_storyline(sessions.get_session(session.id).storyline_id)
    w.start = lambda **kw: tools.start_draft(
        book.id, session.id, "e2", "Ravi survives Vik's attack",
        kw.get("facts", [Fact(entity="Ravi", attribute="status", value="alive")]),
    )
    w.resolve = lambda ref, action, **kw: tools.resolve_item(book.id, session.id, ref, action, **kw)
    return w


def finish_all(w):
    """Resolve the whole queue the way the scenario in the docs does."""
    w.resolve("e2", "rewrite", new_summary="Vik wounds Ravi, who survives", affects=["Asha"])
    w.resolve("r1", "hide")
    w.resolve("e3", "rewrite", new_summary="Asha keeps vigil by the wounded Ravi")
    w.resolve("e4", "keep")


# --- scope and repo ------------------------------------------------------


def test_storyline_scope_filter_and_params():
    scope = GraphScope(book_id="b", sid="s1", max_op=3, hidden_nodes=frozenset({"e2"}),
                       hidden_edges=frozenset({"r1"}))  # fmt: skip
    cypher = graph.visible(scope, "n", "r")
    assert cypher.count("$sid") == 2 and "created_by_op <= $max_op" in cypher
    assert "coalesce(n.key, '') IN $hidden_nodes" in cypher and "coalesce(r.id, '') IN $hidden_edges" in cypher
    assert graph.scope_params(scope) == {
        "sid": "s1", "max_op": 3, "hidden_nodes": ["e2"], "hidden_edges": ["r1"],
    }  # fmt: skip
    assert graph.scope_params(GraphScope.canon("b")) == {}


def test_scope_follows_the_op_log(world):
    world.start()
    sl = world.storyline()
    assert sl.draft.affected == [RAVI, VIK] and sl.head_seq == 1
    world.resolve("r1", "hide")
    scope = storylines.scope(world.storyline())
    assert scope.hidden_edges == {"r1"} and scope.max_op == 2
    assert storylines.scope(world.storyline(), committed_only=True).hidden_edges == frozenset()


# --- drafting ------------------------------------------------------------


def test_start_draft_queues_everything_after_the_divergence(world):
    out = world.start()
    assert out["queued"] == 4  # e2, e3, e4 and the KILLS relation; e1 is before the change
    items = tools.get_open_items(world.book, world.session, 10)
    assert [i["ref"] for i in items["items"]] == ["e2", "r1", "e3", "e4"]  # story order, ties by ref
    assert items["open"] == items["total"] == 4
    with pytest.raises(ValueError, match="already open"):
        world.start()


def test_get_open_items_returns_only_the_next_few(world):
    world.start()
    got = tools.get_open_items(world.book, world.session, 2)
    assert [i["ref"] for i in got["items"]] == ["e2", "r1"] and got["open"] == 4
    assert got["items"][0]["chunk_id"] == world.chunk_ids[1]  # e2 is at story_pos 1000, chunk seq 1


def test_affects_queues_the_later_events_of_newly_affected_entities(world):
    world.mem.entities["meera|character"] = ent("meera|character", "Meera")
    world.mem.events.append(ev("e5", "Meera tells the police", 4000, "Chapter 5"))
    world.mem.involves["e5"] = [("meera|character", "actor")]
    world.start()
    assert world.resolve("e3", "keep")["newly_queued"] == 0
    out = world.resolve("e4", "keep", affects=["Meera"])
    assert out["newly_queued"] == 1 and out["open"] == 3
    assert "e5" in [i.ref for i in storylines.list_items(world.storyline().id, "open")]
    assert "meera|character" in world.storyline().draft.affected


def test_resolve_rejects_bad_input(world):
    world.start()
    with pytest.raises(ValueError, match="action"):
        world.resolve("e3", "delete")
    with pytest.raises(ValueError, match="new_summary"):
        world.resolve("e3", "rewrite")
    with pytest.raises(ValueError, match="only be kept or hidden"):
        world.resolve("r1", "rewrite", new_summary="x")
    with pytest.raises(ValueError, match="not a visible event"):
        world.resolve("nope", "keep")
    world.resolve("e3", "hide")
    with pytest.raises(ValueError, match="already hidden"):
        world.resolve("e3", "hide")


def test_tools_need_a_draft(world):
    with pytest.raises(ValueError, match="start_draft"):
        tools.get_open_items(world.book, world.session)


# --- the gate ------------------------------------------------------------


def test_cannot_commit_without_a_passing_verification(world):
    world.start()
    with pytest.raises(ValueError, match="verified"):
        tools.commit_draft(world.book, world.session)
    finish_all(world)
    with pytest.raises(ValueError, match="verified"):
        tools.commit_draft(world.book, world.session)


def test_verify_fails_while_items_are_open(world):
    world.start()
    result = tools.verify_draft(world.book, world.session)
    assert not result["passed"] and result["issues"][0]["kind"] == "open_items"


def test_full_flow_ends_in_a_committed_storyline(world):
    world.start()
    finish_all(world)
    result = tools.verify_draft(world.book, world.session)
    assert result["passed"], result
    assert world.judge.asked == ["Chapter 2", "Chapter 3", "Chapter 4"]
    assert tools.commit_draft(world.book, world.session)["committed"]

    sl = world.storyline()
    assert sl.draft is None and sl.committed_seq == sl.head_seq
    committed = storylines.scope(sl, committed_only=True)
    assert {"e2", "e3"} <= committed.hidden_nodes and committed.hidden_edges == {"r1"}
    summaries = [e.summary for e in world.mem.events_in(committed)]
    assert "Vik kills Ravi" not in summaries and "Asha keeps vigil by the wounded Ravi" in summaries


def test_a_change_after_verification_invalidates_it(world):
    world.start()
    finish_all(world)
    assert tools.verify_draft(world.book, world.session)["passed"]
    world.resolve("e4", "rewrite", new_summary="Asha confronts Vik in the market")
    with pytest.raises(ValueError, match="changed since"):
        tools.commit_draft(world.book, world.session)
    assert tools.verify_draft(world.book, world.session)["passed"]
    tools.commit_draft(world.book, world.session)


def test_judge_findings_fail_verification_and_can_be_fixed(world):
    world.start()
    finish_all(world)
    world.judge.issues_by_chapter["Chapter 4"] = [
        ContinuityIssue(event_key="e4", problem="Asha confronts Vik as if Ravi were dead.",
                        suggestion="Make it a warning."),
    ]  # fmt: skip
    result = tools.verify_draft(world.book, world.session)
    assert not result["passed"]
    assert result["issues"][0]["kind"] == "continuity" and result["issues"][0]["ref"] == "e4"
    with pytest.raises(ValueError, match="verified"):
        tools.commit_draft(world.book, world.session)

    world.resolve("e4", "rewrite", new_summary="Asha warns Vik away from the wounded Ravi")  # ref reopened
    world.judge.issues_by_chapter.clear()
    assert tools.verify_draft(world.book, world.session)["passed"]


def test_a_chapter_the_judge_cannot_read_is_a_failure(world, monkeypatch):
    world.start()
    finish_all(world)

    def boom(messages):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(world.judge, "invoke", boom)
    result = tools.verify_draft(world.book, world.session)
    assert not result["passed"] and {i["kind"] for i in result["issues"]} == {"judge_error"}


def test_dead_entity_acting_after_death_is_caught_in_code(world):
    world.start(facts=[Fact(entity="Ravi", attribute="status", value="dead")])
    world.mem.involves["e3"] = [(ASHA, "actor"), (RAVI, "actor")]
    finish_all(world)  # e4/e3 resolved, but the rewritten e3 copies Ravi as an actor
    kinds = [i["kind"] for i in tools.verify_draft(world.book, world.session)["issues"]]
    assert "dead_acts" in kinds


def test_alive_entity_with_a_visible_kills_relation_is_caught(world):
    world.start()
    world.resolve("e2", "rewrite", new_summary="Vik wounds Ravi", affects=["Asha"])
    world.resolve("e3", "keep")
    world.resolve("e4", "keep")
    world.resolve("r1", "keep")
    issues = tools.verify_draft(world.book, world.session)["issues"]
    assert any(i["kind"] == "still_killed" and i["ref"] == "r1" for i in issues)


# --- discard and the ledger ----------------------------------------------


def test_discard_removes_the_draft_from_log_and_graph(world):
    world.start()
    finish_all(world)
    tools.discard_draft(world.book, world.session)
    sl = world.storyline()
    assert sl.draft is None and sl.head_seq == 0
    assert storylines.list_ops(sl.id) == [] and storylines.count_items(sl.id) == 0
    assert not [e for e in world.mem.events if e.sid]
    scope = storylines.scope(sl)
    assert world.mem.get_event(scope, "e2") is not None
    tools.get_state_at(world.book, 1500.0, ["Ravi"], world.session)  # tools still work afterwards
    world.start()  # a new draft can begin


def test_state_at_merges_branch_facts_with_canon_hints(world):
    world.start()
    out = tools.get_state_at(world.book, 1500.0, None, world.session)
    states = {s["entity"]["key"]: s for s in out["states"]}
    assert states[RAVI]["branch_facts"] == {"status": "alive"}
    assert any("KILLS" in h for h in states[RAVI]["canon_hints"])
    assert ledger.facts_at(world.storyline(), 500.0) == {}  # the fact starts at the anchor


def test_get_dependents_lists_what_follows(world):
    out = tools.get_dependents(world.book, "Ravi", from_event="e2", session_id=world.session)
    assert [e["key"] for e in out["events"]] == ["e3"] and out["events_per_chapter"] == {"Chapter 3": 1}
    with pytest.raises(ValueError, match="no visible event"):
        tools.get_dependents(world.book, "Ravi", from_event="zzz")


# --- prose ---------------------------------------------------------------


def test_canon_chat_and_unchanged_chapters_read_as_the_original(world):
    out = tools.read_chapter(world.book, "Chapter 3")  # no session: canon
    assert out["source"] == "original" and out["chunk_ids"] == [world.chunk_ids[2]]
    world.start()
    finish_all(world)
    unchanged = tools.read_chapter(world.book, "Chapter 1", world.session)
    assert unchanged["source"] == "original" and "text" not in unchanged


def test_a_changed_chapter_is_not_written_while_the_draft_is_open(world):
    world.start()
    finish_all(world)
    out = tools.read_chapter(world.book, "Chapter 3", world.session)
    assert out["source"] == "original" and out["stale"] and "generate_passage" in out["note"]
    assert world.judge.rewrites == []  # no LLM work for a draft that is still moving


def test_generate_passage_writes_checks_and_stores_a_chapter(world):
    world.start()
    finish_all(world)
    out = tools.generate_passage(world.book, world.session, "Chapter 3")
    assert out["issues"] == [] and out["preview"].startswith("Rewritten Chapter 3")
    assert world.judge.rewrites == ["Chapter 3"]
    stored = storylines.get_passage(world.storyline().id, "Chapter 3")
    assert stored.id == out["chunk_id"] and stored.page_spans[0].page == 3

    got = tools.text_tools.get_chunks(world.book, [out["chunk_id"], world.chunk_ids[2], world.chunk_ids[0]],
                                      world.session)  # fmt: skip
    by_id = {c["chunk_id"]: c for c in got["chunks"]}
    assert "vigil" in by_id[out["chunk_id"]]["text"] and "superseded" not in by_id[out["chunk_id"]]
    assert "superseded" in by_id[world.chunk_ids[2]]  # the original of a rewritten chapter
    assert "superseded" not in by_id[world.chunk_ids[0]]  # an unchanged chapter
    with pytest.raises(ValueError, match="unchanged"):
        tools.generate_passage(world.book, world.session, "Chapter 1")


def test_a_failed_check_triggers_one_retry_with_the_problems(world):
    world.start()
    finish_all(world)
    world.judge.prose_issues = [['mentions the "funeral"'], []]
    out = tools.generate_passage(world.book, world.session, "Chapter 3")
    assert out["issues"] == [] and world.judge.rewrites == ["Chapter 3", "Chapter 3"]


def test_problems_that_survive_the_retry_are_kept_with_the_passage(world):
    world.start()
    finish_all(world)
    world.judge.prose_issues = [["still mourns"], ["still mourns"]]
    out = tools.generate_passage(world.book, world.session, "Chapter 3")
    assert out["issues"] == ["still mourns"] and len(world.judge.rewrites) == 2


def test_committing_writes_the_changed_chapters_and_reading_reuses_them(world):
    world.start()
    finish_all(world)
    tools.verify_draft(world.book, world.session)
    out = tools.commit_draft(world.book, world.session)
    assert out["committed"] and out["chapters_written"] == ["Chapter 2", "Chapter 3"]
    assert out["chapters_failed"] == {} and out["chapters_with_problems"] == {}
    assert world.judge.rewrites == ["Chapter 2", "Chapter 3"]  # chapters 1 and 4 did not change

    first = tools.read_chapter(world.book, "Chapter 3", world.session)
    assert first["source"] == "storyline" and "vigil" in first["text"] and first["issues"] == []
    again = tools.read_chapter(world.book, "Chapter 3", world.session)
    assert again["chunk_id"] == first["chunk_id"] and len(world.judge.rewrites) == 2  # nothing rewritten
    assert tools.read_chapter(world.book, "Chapter 4", world.session)["source"] == "original"


def test_one_chapter_failing_does_not_undo_the_commit_and_is_written_when_read(world, monkeypatch):
    world.start()
    finish_all(world)
    tools.verify_draft(world.book, world.session)
    real = world.judge.invoke

    def flaky(messages):
        if "Chapter: Chapter 3" in messages[-1].content and messages[0].content.startswith("You rewrite"):
            raise RuntimeError("model overloaded")
        return real(messages)

    monkeypatch.setattr(world.judge, "invoke", flaky)
    out = tools.commit_draft(world.book, world.session)
    assert out["committed"] and out["chapters_written"] == ["Chapter 2"]
    assert "overloaded" in out["chapters_failed"]["Chapter 3"]
    assert world.storyline().draft is None  # the verified change stands

    monkeypatch.setattr(world.judge, "invoke", real)
    assert tools.read_chapter(world.book, "Chapter 3", world.session)["source"] == "storyline"  # written now


def test_no_model_at_commit_time_still_commits(world, monkeypatch):
    world.start()
    finish_all(world)
    tools.verify_draft(world.book, world.session)
    monkeypatch.setattr(tools, "get_chat_model", lambda: (_ for _ in ()).throw(RuntimeError("no LLM key")))
    out = tools.commit_draft(world.book, world.session)
    assert out["committed"] and out["chapters_written"] == [] and "no LLM key" in out["chapters_failed"]["all"]
    assert world.storyline().draft is None


def test_problems_the_check_still_finds_are_reported_by_the_commit(world):
    world.start()
    finish_all(world)
    tools.verify_draft(world.book, world.session)
    world.judge.prose_issues = [["still mourns"], ["still mourns"]]  # chapter 2: both tries
    out = tools.commit_draft(world.book, world.session)
    assert out["chapters_with_problems"] == {"Chapter 2": ["still mourns"]}
    assert out["chapters_written"] == ["Chapter 2", "Chapter 3"]


def test_chapters_are_written_in_parallel_without_losing_any(world, monkeypatch):
    world.start()
    finish_all(world)
    tools.verify_draft(world.book, world.session)
    storylines.mark_committed(world.storyline().id)  # commit the graph only, as draft.commit does
    storylines.set_draft(world.storyline().id, None)
    result = prose_module.write_changed_chapters(world.storyline(), world.judge, workers=4)
    assert sorted(result["written"]) == ["Chapter 2", "Chapter 3"] and result["failed"] == {}
    assert prose_module.changed_chapters(world.storyline()) == []  # nothing left to write
    assert prose_module.write_changed_chapters(world.storyline(), world.judge) == {
        "written": [], "failed": {}, "unresolved": {}}  # a second run has nothing to do


def test_editing_a_chapter_again_makes_its_passage_stale(world):
    world.start()
    finish_all(world)
    first = tools.generate_passage(world.book, world.session, "Chapter 3")
    sl = world.storyline()
    new_key = next(e.key for e in world.mem.events if e.sid and e.chapter == "Chapter 3")
    world.resolve(new_key, "rewrite", new_summary="Asha sits with Ravi through the night")
    out = tools.read_chapter(world.book, "Chapter 3", world.session)
    assert out["stale"] and out["source"] == "original"
    second = tools.generate_passage(world.book, world.session, "Chapter 3")
    assert second["chunk_id"] != first["chunk_id"]
    assert [p.chapter for p in storylines.list_passages(sl.id)] == ["Chapter 3"]  # replaced, not added


def test_search_finds_rewritten_text_instead_of_the_original(world):
    world.start()
    finish_all(world)
    tools.generate_passage(world.book, world.session, "Chapter 3")
    hit = tools.text_tools.search_chunks(world.book, "vigil", session_id=world.session)
    assert [r["chapter"] for r in hit["results"]] == ["Chapter 3"]
    assert tools.text_tools.search_chunks(world.book, "funeral", session_id=world.session)["results"] == []
    assert tools.text_tools.search_chunks(world.book, "funeral")["results"][0]["chapter"] == "Chapter 3"  # canon
    stale = tools.text_tools.search_chunks(world.book, "Vik killed", session_id=world.session)
    assert "superseded" in stale["results"][0]  # Chapter 2 changed but has no passage yet


def test_discard_drops_passages_written_in_the_draft(world):
    world.start()
    finish_all(world)
    tools.generate_passage(world.book, world.session, "Chapter 3")
    tools.discard_draft(world.book, world.session)
    assert storylines.list_passages(world.storyline().id) == []


def test_long_chapters_are_rewritten_in_pieces(world, mongo, monkeypatch):
    from app.storyline import prose

    monkeypatch.setattr(prose, "PROSE_SEGMENT_CHARS", 40)
    mongo["chunks"].update_one({"seq": 3}, {"$set": {"chapter": "Chapter 3"}})  # chapter 3 = seq 2-3
    world.start()
    finish_all(world)
    out = tools.generate_passage(world.book, world.session, "Chapter 3")
    assert world.judge.rewrites == ["Chapter 3", "Chapter 3"]
    assert out["characters"] == 2 * len("Rewritten Chapter 3: Asha kept vigil beside the wounded Ravi.") + 2


# --- through the MCP server ----------------------------------------------


def call_tool(name, args):
    """Call a tool the way the agent does: through the server, which is where arguments are checked."""
    import asyncio
    import json

    from app.mcp_server.server import mcp

    content, _ = asyncio.run(mcp.call_tool(name, args))
    return json.loads(content[0].text)


def test_the_whole_flow_runs_through_the_server_wrappers(world):
    ids = {"book_id": world.book, "session_id": world.session}
    assert call_tool("get_dependents", {**ids, "entity": "Ravi", "from_event": "e2"})["total_events"] == 1
    started = call_tool("start_draft", {**ids, "anchor_event": "e2", "premise": "Ravi survives",
                                        "facts": [{"entity": "Ravi", "attribute": "status", "value": "alive"}]})  # fmt: skip
    assert started["queued"] == 4
    assert call_tool("get_state_at", {**ids, "pos": 1500})["states"][0]["branch_facts"] == {"status": "alive"}
    assert call_tool("get_open_items", {**ids, "limit": 2})["open"] == 4
    for ref, action, extra in (("e2", "rewrite", {"new_summary": "Vik wounds Ravi", "affects": ["Asha"]}),
                               ("r1", "hide", {}), ("e3", "keep", {}), ("e4", "keep", {})):  # fmt: skip
        call_tool("resolve_item", {**ids, "ref": ref, "action": action, **extra})
    assert call_tool("verify_draft", ids)["passed"]
    assert call_tool("generate_passage", {**ids, "chapter": "Chapter 2"})["issues"] == []
    assert call_tool("commit_draft", ids)["committed"]
    assert call_tool("read_chapter", {**ids, "chapter": "Chapter 2"})["source"] == "storyline"
    assert call_tool("get_chunks", {**ids, "chunk_ids": [world.chunk_ids[0]]})["chunks"][0]["text"]
    assert call_tool("search_chunks", {**ids, "query": "Asha"})["total_matches"] >= 1


def test_discard_through_the_server(world):
    ids = {"book_id": world.book, "session_id": world.session}
    call_tool("start_draft", {**ids, "anchor_event": "e2", "premise": "x"})
    assert call_tool("discard_draft", ids)["discarded"]


# --- logging -------------------------------------------------------------


def test_the_editing_flow_leaves_an_audit_trail(world, caplog):
    import logging

    caplog.set_level(logging.INFO)
    world.start()
    with pytest.raises(ValueError):
        tools.commit_draft(world.book, world.session)  # never verified
    finish_all(world)
    world.judge.issues_by_chapter["Chapter 4"] = [ContinuityIssue(event_key="e4", problem="x")]
    assert not tools.verify_draft(world.book, world.session)["passed"]
    world.judge.issues_by_chapter.clear()
    assert tools.verify_draft(world.book, world.session)["passed"]
    tools.commit_draft(world.book, world.session)

    records = [(r.levelno, r.getMessage()) for r in caplog.records if r.name.startswith("app.storyline")]
    text = "\n".join(m for _, m in records)
    assert "draft started" in text and "anchor=e2" in text and "queued=4" in text
    assert "commit refused" in text and "not verified" in text
    assert text.count("resolved storyline=") == 4 and "action=rewrite" in text
    assert any(lvl == logging.WARNING and "verification FAILED" in m and "continuity" in m for lvl, m in records)
    assert any(lvl == logging.INFO and "verification passed" in m for lvl, m in records)
    assert "draft committed" in text


def test_prose_generation_and_discard_are_logged(world, caplog):
    import logging

    caplog.set_level(logging.INFO)
    world.start()
    finish_all(world)
    world.judge.prose_issues = [["still mourns"], []]
    tools.generate_passage(world.book, world.session, "Chapter 3")
    tools.discard_draft(world.book, world.session)
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app.storyline"))
    assert "writing chapter" in text and "chapter check found 1 problem(s)" in text
    assert "chapter written" in text and "unresolved_problems=0" in text
    assert "draft discarded" in text


def test_tool_refusals_are_logged_by_the_server_without_a_traceback(world, caplog):
    import logging

    caplog.set_level(logging.INFO)
    with pytest.raises(Exception, match="no visible event"):
        call_tool("get_dependents", {"book_id": world.book, "session_id": world.session,
                                     "entity": "Ravi", "from_event": "zzz"})  # fmt: skip
    [record] = [r for r in caplog.records if r.name == "app.mcp"]
    assert record.levelno == logging.WARNING and "tool=get_dependents refused" in record.getMessage()
    assert "no visible event" in record.getMessage() and record.exc_info is None


def test_a_crashing_tool_is_logged_with_its_traceback(world, caplog, monkeypatch):
    import logging

    def explode(*a, **k):
        raise RuntimeError("graph connection lost")

    monkeypatch.setattr(tools, "get_dependents", explode)
    with pytest.raises(Exception, match="graph connection lost"):
        call_tool("get_dependents", {"book_id": world.book, "entity": "Ravi"})
    [record] = [r for r in caplog.records if r.name == "app.mcp"]
    assert record.levelno == logging.ERROR and "crashed" in record.getMessage()
    assert record.exc_info and "graph connection lost" in str(record.exc_info[1])
