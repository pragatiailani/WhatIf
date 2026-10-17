import pytest

from app.repo import graph
from app.repo.graph import GraphScope
from app.repo.models import GraphEntity


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


def test_visible_storyline_not_supported_yet():
    with pytest.raises(NotImplementedError):
        graph.visible(GraphScope(book_id="b", sid="s1"), "n")


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
