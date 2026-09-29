"""The entity_tree type-pair statements list every assay an edge carries, proven on a real Neo4j.

Runs only under lane.sh (a private, throwaway Neo4j); elsewhere every test here skips. After a reload of the fixture it
writes samples of type codes the fixture does not use, and DERIVED_FROM edges shaped as graph_sync labels them: one
shared by two assays (singular A, plural A and B), one with the singular field only, one whose singular title is null
while its plural list names B, and one whose plural list holds only the empty title. The statements are the module
constants ``nextseek_api/services/entity_tree.py`` sends for ``GET entity_tree/edges/`` and
``GET entity_tree/edge_attributes/``; the assertions read only this test's own type pairs.
"""
from __future__ import annotations

import os

import pytest

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

PARENT = "ETP"
# child type code -> the DERIVED_FROM properties of its one edge to the ETP parent
EDGES = {
    "ETA": {"internal_assay_title": "A", "internal_assay_id": 1,
            "internal_assay_titles": ["A", "B"], "internal_assay_ids": [1, 2]},
    "ETS": {"internal_assay_title": "S", "internal_assay_id": 3},
    "ETN": {"internal_assay_titles": ["", "B"], "internal_assay_ids": [5, 6]},
    "ETE": {"internal_assay_titles": [""], "internal_assay_ids": [7]},
}


def _load_edges(lane) -> None:
    lane.write(f"CREATE (:Sample:T_{PARENT} {{id: 900000, uuid: 'ETP-1', type: '{PARENT}', project_ids: [1]}})")
    for n, (child, props) in enumerate(EDGES.items(), start=1):
        lane.write(
            f"MATCH (p:Sample {{id: 900000}}) "
            f"CREATE (c:Sample:T_{child} {{id: $id, uuid: $uuid, type: $type, project_ids: [1]}}) "
            "CREATE (c)-[r:DERIVED_FROM]->(p) SET r = $props",
            {"id": 900000 + n, "uuid": f"{child}-1", "type": child, "props": props})


def _own(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["source"] == PARENT]


def test_the_type_pair_statements_list_every_assay_an_edge_carries(lane):
    from nextseek_api.services.entity_tree import _EDGE_ATTRIBUTES_CYPHER, _EDGES_CYPHER

    lane.reload()
    try:
        _load_edges(lane)
        edges = {(row["target"], row["annotation"]) for row in _own(lane.read(_EDGES_CYPHER))}
        attributes = {(row["target"], row["annotation"], row["internal_assay_id"])
                      for row in _own(lane.read(_EDGE_ATTRIBUTES_CYPHER))}
    finally:
        lane.reload()
    assert edges == {("ETA", "A"), ("ETA", "B"), ("ETS", "S"), ("ETN", "B")}
    assert attributes == {("ETA", "A", 1), ("ETA", "B", 2), ("ETS", "S", 3), ("ETN", "B", 6)}
    assert not [row for row in edges if row[1] == ""]
