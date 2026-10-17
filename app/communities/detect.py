"""Find groups of closely connected entities in a book's merged graph. Pure, no I/O.

The graph has one node per entity. Story relations add their weight to the edge between the two
entities, and every event adds COMMUNITY_EVENT_WEIGHT between each pair of its participants, so
characters who keep appearing together end up in one group even without a named relation.
"""

from itertools import combinations

import networkx as nx

from app.constants import (
    COMMUNITY_EVENT_WEIGHT,
    COMMUNITY_MAX,
    COMMUNITY_MIN_SIZE,
    COMMUNITY_SEED,
)
from app.repo.models import GraphPayload


def _add(g: nx.Graph, a: str, b: str, weight: float) -> None:
    if a == b or a not in g or b not in g:
        return
    if g.has_edge(a, b):
        g[a][b]["weight"] += weight
    else:
        g.add_edge(a, b, weight=weight)


def entity_graph(payload: GraphPayload) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(e["key"] for e in payload.entities)
    for r in payload.relations:
        _add(g, r["src"], r["dst"], float(r.get("weight") or 1))
    for event in payload.events:
        keys = sorted({p["key"] for p in event.get("participants", [])})
        for a, b in combinations(keys, 2):
            _add(g, a, b, COMMUNITY_EVENT_WEIGHT)
    return g


def detect(g: nx.Graph) -> list[list[str]]:
    """Groups of entity keys, the most connected group first; each group ordered by how
    connected its members are. Groups under COMMUNITY_MIN_SIZE are dropped."""
    if g.number_of_edges() == 0:
        return []
    # Leiden walks sets of nodes, and string hashes change per process; on integer labels (in
    # sorted key order) the same graph always gives the same groups.
    keys = sorted(g.nodes)
    numbered = nx.relabel_nodes(g, {k: i for i, k in enumerate(keys)})
    parts = nx.community.leiden_communities(
        numbered, weight="weight", metric="modularity", seed=COMMUNITY_SEED
    )
    strength = dict(g.degree(weight="weight"))
    groups = [
        sorted((keys[i] for i in part), key=lambda k: (-strength[k], k))
        for part in parts
        if len(part) >= COMMUNITY_MIN_SIZE
    ]
    groups.sort(key=lambda grp: (-sum(strength[k] for k in grp), grp[0]))
    return groups[:COMMUNITY_MAX]
