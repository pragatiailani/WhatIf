import re

import numpy as np
import pytest

from app.merge.candidates import find_candidates, name_sim
from app.merge.combine import Resolution, cap_descriptions, combine, from_leaf
from app.merge.prompt import build_merge_messages
from app.merge.runner import children, merge_page_graphs, tree_height
from app.merge.schema import EntityVerdict, FinalDescriptions, MergeVerdicts
from app.repo import books, chunks, graph_levels
from app.repo.models import Book, Chunk, GraphLevel, GraphPayload, PageSpan


def _entity(name, type_="Character", aliases=(), page=1, description=None):
    from app.extraction.schema import entity_key

    return {
        "key": entity_key(name, type_),
        "name": name,
        "type": type_,
        "aliases": list(aliases),
        "description": description if description is not None else f"{name} on p.{page}.",
        "first_page": page,
        "last_page": page,
        "mention_count": 1,
    }


def _event(seq, order, summary, participants, page):
    return {
        "key": f"event|{seq}|{order}",
        "summary": summary,
        "story_pos": float(seq * 1000 + order),
        "story_time": None,
        "chapter": None,
        "first_page": page,
        "participants": [{"key": k, "role": "actor"} for k in participants],
        "place": None,
    }


def _relation(src, dst, rel, seq, page):
    return {
        "src": src, "dst": dst, "rel": rel, "description": "", "weight": 1.0,
        "story_pos": float(seq * 1000), "page": page,
    }  # fmt: skip


def _leaf(seq, entities=(), events=(), relations=(), book="b1") -> GraphLevel:
    page = seq + 1
    return GraphLevel(
        book_id=book, level=0, index=seq, seq_start=seq, seq_end=seq,
        page_start=page, page_end=page, chunk_ids=[f"c{seq}"],
        graph=GraphPayload(entities=list(entities), events=list(events), relations=list(relations)),
    )  # fmt: skip


RAVI, MEERA = "ravi|character", "meera|character"


# --- tree ----------------------------------------------------------------------------------


def test_tree_shape():
    assert [tree_height(n) for n in (1, 2, 3, 4, 5, 8, 9)] == [1, 1, 2, 2, 3, 3, 4]
    assert children(1, 0, 2) == ((0, 0), (0, 1))
    assert children(1, 2, 5) == ((0, 4), None)  # odd tail: a copy of its one child
    assert children(3, 0, 5) == ((2, 0), (2, 1))
    assert children(1, 0, 1) == ((0, 0), None)


# --- combine -------------------------------------------------------------------------------


def test_entities_union_and_sum():
    left = from_leaf(_leaf(0, [_entity("Ravi", aliases=["the clerk"], page=1)]))
    right = from_leaf(_leaf(1, [_entity("Ravi", aliases=["Ravi bhai"], page=2)]))
    merged = combine(left, right, Resolution(entities={RAVI: RAVI}))
    (ravi,) = merged.entities
    assert ravi["aliases"] == ["the clerk", "Ravi bhai"]
    assert ravi["pages"] == [1, 2] and ravi["chunk_ids"] == ["c0", "c1"]
    assert (ravi["first_page"], ravi["last_page"], ravi["mention_count"]) == (1, 2, 2)
    assert ravi["descriptions"] == [
        {"page": 1, "text": "Ravi on p.1."},
        {"page": 2, "text": "Ravi on p.2."},
    ]
    assert ravi["sources"] == [[0, RAVI], [1, RAVI]]


def test_canonical_name_keeps_key_and_old_name_as_alias():
    left = from_leaf(_leaf(0, [_entity("Ravi")]))
    right = from_leaf(_leaf(1, [_entity("Ravi Sharma")]))
    resolution = Resolution(entities={"ravi_sharma|character": RAVI}, names={RAVI: "Ravi Sharma"})
    merged = combine(left, right, resolution)
    (ravi,) = merged.entities
    assert (ravi["key"], ravi["name"], ravi["aliases"]) == (RAVI, "Ravi Sharma", ["Ravi"])


def test_descriptions_capped_but_spread_over_the_story():
    notes = [{"page": p, "text": f"note {p}"} for p in range(1, 11)]
    notes.append({"page": 3, "text": "Note 3"})  # a repeat, ignoring case
    kept = cap_descriptions(notes, limit=5)
    assert len(kept) == 5
    assert kept[0]["page"] == 1 and kept[-1]["page"] == 10  # late story still represented


def test_unmerged_same_key_gets_a_new_key_and_edges_follow():
    left = from_leaf(_leaf(0, [_entity("Ravi"), _entity("Meera")], relations=[
        _relation(RAVI, MEERA, "FRIEND_OF", 0, 1)]))  # fmt: skip
    right = from_leaf(_leaf(4, [_entity("Ravi"), _entity("Meera")], relations=[
        _relation(RAVI, MEERA, "TALKS_TO", 4, 5)]))  # fmt: skip
    merged = combine(left, right, Resolution(entities={MEERA: MEERA}))  # two different Ravis
    assert {e["key"] for e in merged.entities} == {RAVI, MEERA, "ravi|character@4"}
    assert {(r["src"], r["rel"]) for r in merged.relations} == {
        (RAVI, "FRIEND_OF"), ("ravi|character@4", "TALKS_TO"),
    }  # fmt: skip


def test_edges_grouped_but_changing_relations_kept_apart():
    ents = [_entity("Ravi"), _entity("Meera")]
    left = from_leaf(_leaf(0, ents, relations=[_relation(RAVI, MEERA, "FRIEND_OF", 0, 1)]))
    right = from_leaf(_leaf(1, ents, relations=[
        _relation(RAVI, MEERA, "FRIEND_OF", 1, 2), _relation(RAVI, MEERA, "ENEMY_OF", 1, 2),
        _relation(MEERA, MEERA, "TALKS_TO", 1, 2)]))  # fmt: skip
    merged = combine(left, right, Resolution(entities={RAVI: RAVI, MEERA: MEERA}))
    by_rel = {r["rel"]: r for r in merged.relations}
    assert set(by_rel) == {"FRIEND_OF", "ENEMY_OF"}  # self-loop dropped
    friend = by_rel["FRIEND_OF"]
    assert (friend["weight"], friend["pos_start"], friend["pages"]) == (2.0, 0.0, [1, 2])
    assert friend["chunk_ids"] == ["c0", "c1"]
    assert by_rel["ENEMY_OF"]["pos_start"] == 1000.0  # the story changing, in order


def test_right_events_are_remapped_and_merged():
    left = from_leaf(_leaf(0, [_entity("Ravi")], events=[_event(0, 0, "Ravi runs", [RAVI], 1)]))
    right = from_leaf(_leaf(1, [_entity("Ravi Sharma")], events=[
        _event(1, 0, "Ravi keeps running", ["ravi_sharma|character"], 2),
        _event(1, 1, "Ravi stops", ["ravi_sharma|character"], 2)]))  # fmt: skip
    merged = combine(
        left,
        right,
        Resolution(entities={"ravi_sharma|character": RAVI}, events={"event|1|0": "event|0|0"}),
    )
    assert [e["key"] for e in merged.events] == ["event|0|0", "event|1|1"]
    assert merged.events[0]["pages"] == [1, 2]
    assert merged.events[1]["participants"] == [{"key": RAVI, "role": "actor"}]


# --- candidates ----------------------------------------------------------------------------


def _payloads(left_entities, right_entities, left_events=(), right_events=(), right_seq=1):
    return (
        from_leaf(_leaf(0, left_entities, left_events)),
        from_leaf(_leaf(right_seq, right_entities, right_events)),
    )


def test_same_name_is_decided_in_code_and_types_never_mix():
    left, right = _payloads(
        [_entity("Ravi"), _entity("Delhi", "Place")], [_entity("Ravi"), _entity("Delhi", "Group")]
    )
    found = find_candidates(left, right)
    assert found.entities == {RAVI: RAVI}
    assert not found.ambiguous_entities


def test_partial_names_and_aliases_go_to_the_judge():
    left, right = _payloads(
        [_entity("Ravi"), _entity("Inspector Rao", aliases=["the Inspector"])],
        [_entity("Ravi Sharma"), _entity("The Inspector")],
    )
    found = find_candidates(left, right)
    assert not found.entities
    pairs = {(c.left["name"], c.right["name"]) for c in found.ambiguous_entities}
    assert pairs == {("Ravi", "Ravi Sharma"), ("Inspector Rao", "The Inspector")}
    assert name_sim(left.entities[0], right.entities[0]) == pytest.approx(0.85)


def test_two_possible_partners_are_not_decided_in_code():
    left = from_leaf(_leaf(0, [_entity("Ravi")]))
    left.entities.append({**left.entities[0], "key": "ravi|character@3"})  # a second Ravi
    right = from_leaf(_leaf(5, [_entity("Ravi")]))
    found = find_candidates(left, right)
    assert not found.entities
    assert len(found.ambiguous_entities) == 2


def test_embeddings_veto_same_name_and_find_epithets():
    left, right = _payloads(
        [_entity("Ravi"), _entity("Kabir Singh")], [_entity("Ravi"), _entity("The Old Man")]
    )
    lv = {RAVI: np.array([1.0, 0.0]), "kabir_singh|character": np.array([0.0, 1.0])}
    rv = {RAVI: np.array([0.0, 1.0]), "the_old_man|character": np.array([0.0, 1.0])}
    found = find_candidates(left, right, lv, rv)
    assert not found.entities  # same name, but the descriptions disagree
    pairs = {(c.left["name"], c.right["name"], c.embed_sim) for c in found.ambiguous_entities}
    assert ("Ravi", "Ravi", 0.0) in pairs
    assert ("Kabir Singh", "The Old Man", 1.0) in pairs
    assert ("Kabir Singh", "Ravi", 1.0) in pairs


def test_events_compared_only_near_the_boundary_with_shared_people():
    ravi = [_entity("Ravi")]
    near = _payloads(ravi, ravi, [_event(0, 0, "Ravi fights the guard", [RAVI], 1)],
                     [_event(1, 0, "Ravi fights the guards", [RAVI], 2),
                      _event(1, 1, "Ravi fights the guard", [RAVI], 2)])  # fmt: skip
    found = find_candidates(*near)
    assert found.events == {"event|1|1": "event|0|0"}  # identical summary: decided in code
    assert not found.ambiguous_events  # the other one lost its partner to the identical one

    far = _payloads(ravi, ravi, [_event(0, 0, "Ravi fights the guard", [RAVI], 1)],
                    [_event(9, 0, "Ravi fights the guard", [RAVI], 10)], right_seq=9)  # fmt: skip
    assert not find_candidates(*far).events


def test_merge_prompt_has_sections_signals_and_relations():
    left = from_leaf(_leaf(0, [_entity("Ravi"), _entity("Meera")],
                           relations=[_relation(RAVI, MEERA, "FRIEND_OF", 0, 1)]))  # fmt: skip
    right = from_leaf(_leaf(1, [_entity("Ravi Sharma")]))
    found = find_candidates(left, right)
    _, human = build_merge_messages(
        Book(title="Train", author="A. Writer", content_hash="h"),
        _leaf(0), _leaf(1), left, right,
        [("E1", found.ambiguous_entities[0])], [],
    )  # fmt: skip
    text = human.content
    assert "Book: Train by A. Writer" in text
    assert "LEFT section: pages 1-1    RIGHT section: pages 2-2" in text
    assert "relations: FRIEND_OF → Meera | pages: 1" in text
    assert "signals: name_sim=0.85, embed_sim=n/a" in text


def test_verdict_values_are_normalized():
    v = EntityVerdict(id="E1", decision="Same", confidence=1.4)
    assert (v.decision, v.confidence) == ("same", 1.0)


# --- runner --------------------------------------------------------------------------------


class FakeLLM:
    """Judge answers `verdict` for every candidate; describer joins the notes."""

    def __init__(self, verdict=("same", 0.9), fail_on=None):
        self.verdict = verdict
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    def with_structured_output(self, schema, **kwargs):
        return _Bound(self, schema)


class _Bound:
    def __init__(self, llm, schema):
        self.llm, self.schema = llm, schema

    async def ainvoke(self, messages):
        text = messages[1].content
        self.llm.calls.append((self.schema.__name__, text))
        if self.llm.fail_on and self.llm.fail_on in text:
            raise RuntimeError("llm down")
        if self.schema is FinalDescriptions:
            keys = re.findall(r"^(\S+) \| ", text, flags=re.M)
            return FinalDescriptions(descriptions=[{"key": k, "description": "final"} for k in keys])
        decision, confidence = self.llm.verdict
        ids = re.findall(r"^\[([EV]\d+)\]$", text, flags=re.M)
        return MergeVerdicts(
            entities=[
                {"id": i, "decision": decision, "confidence": confidence, "canonical_name": "Ravi Sharma"}
                for i in ids if i.startswith("E")
            ],
            events=[{"id": i, "decision": decision, "confidence": confidence} for i in ids if i.startswith("V")],
        )  # fmt: skip


def _store_book(pages: list[list[dict]], book="b1"):
    books.create_book(Book(id=book, title="Train", content_hash="h"))
    for seq, entities in enumerate(pages):
        chunk = Chunk(
            id=f"c{seq}", book_id=book, seq=seq, text="x",
            page_spans=[PageSpan(page=seq + 1, char_start=0, char_end=1)],
        )  # fmt: skip
        chunks.insert_chunks([chunk])
        graph_levels.save_level_graph(_leaf(seq, entities, book=book))


def _run(**kwargs):
    return merge_page_graphs("b1", embeddings=False, concurrency=4, **kwargs)


def test_clear_book_merges_with_no_llm_calls_except_descriptions(mongo):
    _store_book([[_entity("Ravi", page=p + 1), _entity("Meera", page=p + 1)] for p in range(5)])
    llm = FakeLLM()
    report = _run(llm=llm)

    assert report.root == (3, 0) and not report.failed
    assert [schema for schema, _ in llm.calls] == ["FinalDescriptions"]
    assert report.same_by_code == 8 and report.same_by_llm == 0
    assert graph_levels.count_level("b1", 1) == 3 and graph_levels.count_level("b1", 3) == 1
    root = graph_levels.get_level_graph("b1", 3, 0)
    assert (root.seq_start, root.seq_end, root.page_start, root.page_end) == (0, 4, 1, 5)
    assert root.chunk_ids == ["c0", "c1", "c2", "c3", "c4"]
    ravi = next(e for e in root.graph.entities if e["key"] == RAVI)
    assert ravi["mention_count"] == 5 and ravi["pages"] == [1, 2, 3, 4, 5]
    assert ravi["description"] == "final" and report.described == 2


def test_ambiguous_pairs_go_to_one_call_and_low_confidence_is_ignored(mongo):
    _store_book([[_entity("Ravi")], [_entity("Ravi Sharma")]])
    report = _run(llm=FakeLLM(verdict=("same", 0.6)), describe=False)
    root = graph_levels.get_level_graph("b1", 1, 0)
    assert report.llm_calls == 1 and report.same_by_llm == 0
    assert len(root.graph.entities) == 2

    graph_levels.delete_levels("b1", from_level=1)
    report = _run(llm=FakeLLM(verdict=("same", 0.9)), describe=False)
    (ravi,) = graph_levels.get_level_graph("b1", 1, 0).graph.entities
    assert report.same_by_llm == 1
    assert (ravi["key"], ravi["name"], ravi["aliases"]) == (RAVI, "Ravi Sharma", ["Ravi"])


def test_large_merges_are_split_into_calls_of_40(mongo):
    left = [_entity(f"Person {i}") for i in range(45)]
    right = [_entity(f"Person {i} Jr") for i in range(45)]
    _store_book([left, right])
    llm = FakeLLM(verdict=("different", 0.9))
    report = _run(llm=llm, describe=False)
    assert report.llm_calls == len(llm.calls) >= 2
    for _, text in llm.calls:
        assert len(re.findall(r"^\[E\d+\]$", text, flags=re.M)) <= 40


def test_failed_merge_is_reported_and_resume_skips_finished_nodes(mongo):
    _store_book([[_entity("Ravi")], [_entity("Ravi")], [_entity("Meera")], [_entity("Meera Devi")]])
    report = _run(llm=FakeLLM(fail_on="pages 3-3"), describe=False)
    assert report.root is None
    assert list(report.failed) == [(1, 1)] and "llm down" in report.failed[(1, 1)]
    assert graph_levels.get_level_graph("b1", 1, 0) is not None  # the other subtree finished

    again = _run(llm=FakeLLM(), describe=False)
    assert again.root == (2, 0)
    assert again.skipped == [(1, 0)] and again.merged == [(1, 1), (2, 0)]


def test_single_page_book_and_missing_pages(mongo):
    _store_book([[_entity("Ravi")]])
    report = _run(llm=FakeLLM())
    assert report.root == (1, 0) and report.llm_calls == 0  # one note: used as is
    (ravi,) = graph_levels.get_level_graph("b1", 1, 0).graph.entities
    assert ravi["description"] == "Ravi on p.1."

    chunks.insert_chunks([Chunk(book_id="b1", seq=1, text="y")])
    with pytest.raises(ValueError, match="no level-0 graph"):
        _run(llm=FakeLLM())


# --- publish -------------------------------------------------------------------------------


class RecordingGraph:
    def __init__(self):
        self.writes: list[tuple[str, dict]] = []
        self.deleted = False

    def query(self, cypher, params):
        self.writes.append((" ".join(cypher.split()), params))

    def delete(self):
        self.deleted = True
        raise RuntimeError("ERR Invalid graph operation on empty key")


def test_publish_writes_root_with_type_labels_and_event_links(mongo, monkeypatch):
    from app.merge.publish import publish_book_graph
    from app.repo import graph

    ents = [_entity("Ravi"), _entity("Delhi", "Place")]
    _store_book([ents, ents])
    level = graph_levels.get_level_graph("b1", 0, 1)
    level.graph.events = [_event(1, 0, "Ravi reaches Delhi", [RAVI], 2)]
    level.graph.events[0]["place"] = "delhi|place"
    level.graph.relations = [_relation(RAVI, "delhi|place", "LIVES_IN", 1, 2)]
    graph_levels.save_level_graph(level)
    graph_levels.save_level_graph(_leaf(0, ents, [_event(0, 0, "Ravi waits", [RAVI], 1)]))
    _run(llm=FakeLLM())

    fake = RecordingGraph()
    monkeypatch.setattr(graph, "select_graph", lambda name: fake)
    counts = publish_book_graph("b1")

    assert fake.deleted  # the missing graph is ignored
    assert counts == {"entities": 2, "events": 2, "relations": 1, "event_links": 4}
    cyphers = [c for c, _ in fake.writes]
    assert any("MERGE (n:Entity:Character {key: row.key})" in c for c in cyphers)
    assert any("MERGE (n:Entity:Place {key: row.key})" in c for c in cyphers)
    before = next(p for c, p in fake.writes if "BEFORE" in c)
    assert before["rows"] == [{"src": "event|0|0", "dst": "event|1|0"}]


def test_publish_needs_a_finished_merge(mongo):
    from app.merge.publish import publish_book_graph

    _store_book([[_entity("Ravi")], [_entity("Ravi")]])
    with pytest.raises(ValueError, match="no finished merge"):
        publish_book_graph("b1")
