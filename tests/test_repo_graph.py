import pytest

from app.repo import graph
from app.repo.graph import GraphScope
from app.repo.models import GraphEntity, GraphEvent, GraphRelation


class FakeGraph:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls: list[tuple[str, str, dict]] = []

    def ro_query(self, cypher, params):
        self.calls.append(("ro", cypher, params))
        return type("Result", (), {"result_set": self.rows})()

    def query(self, cypher, params):
        self.calls.append(("rw", cypher, params))


@pytest.fixture
def fake(monkeypatch):
    g = FakeGraph()
    monkeypatch.setattr(graph, "select_graph", lambda name: g)
    return g


def test_graph_name():
    assert graph.graph_name("abc") == "book_abc"


def test_visible_canon():
    scope = GraphScope.canon("b")
    assert graph.visible(scope, "n") == "n.sid IS NULL"
    assert graph.visible(scope, "n", "r") == "n.sid IS NULL AND r.sid IS NULL"


def test_visible_storyline_scope():
    cypher = graph.visible(GraphScope(book_id="b", sid="s1"), "n")
    assert "$sid" in cypher and "$hidden_nodes" in cypher


def test_reads_apply_filter_and_drop_embedding(fake):
    fake.rows = [[{"key": "ravi", "name": "Ravi", "type": "person", "embedding": [0.1]}]]
    entity = graph.get_entity(GraphScope.canon("b"), "ravi")

    assert entity.key == "ravi" and entity.embedding is None
    kind, cypher, params = fake.calls[0]
    assert kind == "ro" and "n.sid IS NULL" in cypher and params == {"key": "ravi"}


def test_relations_filter_every_alias(fake):
    graph.entity_relations(GraphScope.canon("b"), "ravi")
    cypher = fake.calls[0][1]
    for alias in ("n", "r", "m"):
        assert f"{alias}.sid IS NULL" in cypher


def test_canon_write_rejects_sid(fake):
    with pytest.raises(ValueError):
        graph.upsert_entities("b", [GraphEntity(key="k", name="K", type="person", sid="s1")])
    assert fake.calls == []


# --- storyline scopes ----------------------------------------------------

STORY = GraphScope(book_id="b", sid="s1", max_op=4, hidden_nodes=frozenset({"e2"}),
                   hidden_edges=frozenset({"r1"}))  # fmt: skip
STORY_PARAMS = {"sid": "s1", "max_op": 4, "hidden_nodes": ["e2"], "hidden_edges": ["r1"]}


def test_storyline_reads_carry_the_scope_parameters(fake):
    fake.rows = []
    graph.get_event(STORY, "e1")
    _, cypher, params = fake.calls[0]
    assert params == {**STORY_PARAMS, "key": "e1"} and "created_by_op <= $max_op" in cypher


def test_entity_relations_can_be_limited_to_types(fake):
    graph.entity_relations(GraphScope.canon("b"), "ravi", rel_types=["KILLS"])
    _, cypher, params = fake.calls[0]
    assert "r.rel IN $rel_types" in cypher and params["rel_types"] == ["KILLS"]
    graph.entity_relations(GraphScope.canon("b"), "ravi")
    assert "r.rel IN" not in fake.calls[1][1]


def test_events_involving_filters_event_and_entity_and_groups_participants(fake):
    fake.rows = [[{"key": "e2", "summary": "Vik kills Ravi", "story_pos": 1000.0, "embedding": [1]},
                  ["ravi", "vik"]]]  # fmt: skip
    out = graph.events_involving(STORY, ["ravi", "vik"], 1000.0)
    _, cypher, params = fake.calls[0]
    assert [(e.key, e.embedding, keys) for e, keys in out] == [("e2", None, ["ravi", "vik"])]
    assert params == {**STORY_PARAMS, "keys": ["ravi", "vik"], "pos_from": 1000.0}
    assert "INVOLVES" in cypher and "(e.sid IS NULL" in cypher and "(n.sid IS NULL" in cypher
    assert "ORDER BY e.story_pos" in cypher


def test_relations_involving_filters_both_ends_and_the_edge(fake):
    fake.rows = [[{"id": "r1", "rel": "KILLS", "story_pos": 1000.0}, "vik", "ravi"]]
    [rel] = graph.relations_involving(GraphScope.canon("b"), ["ravi"], 500.0)
    cypher = fake.calls[0][1]
    assert (rel.id, rel.src, rel.dst, rel.rel) == ("r1", "vik", "ravi", "KILLS")
    for alias in ("a", "r", "b"):
        assert f"{alias}.sid IS NULL" in cypher
    assert "r.story_pos >= $pos_from" in cypher


def test_event_links_reads_participants_and_places(fake):
    results = iter([[["ravi", "target"], ["vik", "actor"]], [["delhi"]]])
    fake.ro_query = lambda cypher, params: type("R", (), {"result_set": next(results)})()
    assert graph.event_links(GraphScope.canon("b"), "e2") == {
        "involves": [("ravi", "target"), ("vik", "actor")],
        "at": ["delhi"],
    }


def test_branch_writes_need_a_sid_and_keep_it(fake):
    event = GraphEvent(key="sl|1", summary="x", story_pos=1.0, sid="s1", created_by_op=3)
    graph.upsert_events("b", [event], branch=True)
    kind, cypher, params = fake.calls[0]
    assert kind == "rw" and params["rows"][0]["sid"] == "s1" and params["rows"][0]["created_by_op"] == 3
    with pytest.raises(ValueError, match="storyline writes need a sid"):
        graph.upsert_events("b", [GraphEvent(key="k", summary="x", story_pos=1.0)], branch=True)
    with pytest.raises(ValueError, match="canon writes"):
        graph.upsert_events("b", [event])
    assert len(fake.calls) == 1


def test_branch_relations_store_their_sid_and_op(fake):
    rel = GraphRelation(id="r9", src="a", dst="b", rel="HELPS", sid="s1", created_by_op=2)
    graph.upsert_relations("b", [rel], branch=True)
    cypher, row = fake.calls[0][1], fake.calls[0][2]["rows"][0]
    assert "r.sid = row.sid" in cypher and "r.created_by_op = row.created_by_op" in cypher
    assert (row["sid"], row["created_by_op"]) == ("s1", 2)


def test_deleting_a_storyline_rewind_removes_edges_before_nodes(fake):
    graph.delete_storyline_items("b", "s1", 5)
    (k1, c1, p1), (k2, c2, p2) = fake.calls
    assert k1 == k2 == "rw" and p1 == p2 == {"sid": "s1", "op": 5}
    assert "DELETE r" in c1 and "DETACH DELETE n" in c2 and "created_by_op > $op" in c2


# --- logging -------------------------------------------------------------


def test_slow_graph_queries_are_warnings_and_fast_ones_are_debug(fake, caplog, monkeypatch):
    import logging

    caplog.set_level(logging.DEBUG, logger="app.repo.graph")
    graph.get_event(GraphScope.canon("b"), "e1")
    fast = [r for r in caplog.records if r.name == "app.repo.graph"]
    assert fast and fast[0].levelno == logging.DEBUG and "graph read book=b" in fast[0].getMessage()

    caplog.clear()
    monkeypatch.setattr(graph, "SLOW_QUERY_SECONDS", 0.0)
    graph.get_event(GraphScope.canon("b"), "e1")
    [slow] = [r for r in caplog.records if r.name == "app.repo.graph"]
    assert slow.levelno == logging.WARNING and "SLOW" in slow.getMessage() and "MATCH" in slow.getMessage()


def test_a_failing_query_is_logged_as_an_error_and_still_raises(fake, caplog):
    import logging

    def boom(cypher, params):
        raise ConnectionError("falkor is down")

    fake.query = boom
    with pytest.raises(ConnectionError):
        graph.delete_storyline_items("b", "s1", 0)
    [record] = [r for r in caplog.records if r.name == "app.repo.graph"]
    assert record.levelno == logging.ERROR and "graph write failed book=b" in record.getMessage()
    assert "DELETE" in record.getMessage()
