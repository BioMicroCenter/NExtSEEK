"""
The entity_tree lineage endpoint stops at the caller's project edge, proven on a real Neo4j.

Runs only under lane.sh (a private, throwaway Neo4j holding fixture_graph.py); elsewhere every test here skips.
The statements are the ones ``nextseek_api/services/entity_tree.py`` runs for ``POST entity_tree/lineage/``. For each
caller who is not an admin, the scoped statement runs from every sample of the whole fixture; the differential oracle
then deletes every sample and orphan the caller cannot see and every project outside the scope, runs the admin
statement from the same sample, and requires the same nodes and the same edges. A sample the caller cannot see must
return nothing at all (the endpoint answers it as a sample that does not exist), and no edge the scoped statement
returns may carry a marker the caller may not read.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md, decision 3.
"""
from __future__ import annotations

import os

import pytest

from NessieAI.tests.chat_nextseek.graph_scope import fixture_graph

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

NON_ADMIN = {name: ids for name, ids in fixture_graph.CALLERS.items() if ids is not None}
SAMPLE_IDS = sorted(sample["props"]["id"] for sample in fixture_graph.samples())


def _walk(value, nodes: list, rels: list) -> None:
    """Every node and relationship in a returned value, as ``neo4j.Result.graph`` (the endpoint's transformer)
    collects them."""
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, nodes, rels)
    elif hasattr(value, "start_node") and hasattr(value, "end_node"):
        rels.append(value)
    elif hasattr(value, "labels"):
        nodes.append(value)


def _lineage(lane, sample_id: int, caller: tuple[int, ...] | None) -> tuple[set, set, str]:
    """(node uuids, (child, parent) uuid edges, the edge text the endpoint returns) for one sample."""
    from nextseek_api.services.entity_tree import _LINEAGE_CYPHER, _LINEAGE_SCOPED_CYPHER

    if caller is None:
        rows = lane.read(_LINEAGE_CYPHER, {"id": sample_id})
    else:
        rows = lane.read(_LINEAGE_SCOPED_CYPHER, {"id": sample_id, "projects": list(caller)})
    nodes: list = []
    rels: list = []
    for row in rows:
        for value in row.values():
            _walk(value, nodes, rels)
    uuid_of = {node.element_id: node["uuid"] for node in nodes}
    edges = {(uuid_of.get(rel.start_node.element_id), uuid_of.get(rel.end_node.element_id)) for rel in rels}
    text = " ".join(f"{rel.get('internal_assay_title')} {rel.get('protocol_title')}" for rel in rels)
    return set(uuid_of.values()), edges, text


@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_lineage_equals_the_pruned_graph(lane, caller):
    lane.reload()
    scoped = {sid: _lineage(lane, sid, caller) for sid in SAMPLE_IDS}
    lane.prune(caller)
    visible = {row["id"] for row in lane.read("MATCH (s:Sample) RETURN s.id AS id")}
    failures = []
    for sid, (nodes, edges, text) in scoped.items():
        bad = fixture_graph.forbidden_markers(text, caller)
        if bad:
            failures.append(f"{sid}: the scoped lineage carries {bad}")
        if sid not in visible:
            if nodes or edges:
                failures.append(f"{sid}: not visible, yet the scoped statement returned {sorted(nodes)}")
            continue
        oracle_nodes, oracle_edges, _ = _lineage(lane, sid, None)
        if nodes != oracle_nodes:
            failures.append(f"{sid}: scoped nodes {sorted(nodes)} differ from the pruned graph's {sorted(oracle_nodes)}")
        if edges != oracle_edges:
            failures.append(f"{sid}: scoped edges {sorted(edges)} differ from the pruned graph's {sorted(oracle_edges)}")
    print(f"caller {caller}: {len(scoped)} samples, {len(visible)} visible")
    assert not failures, "\n".join(failures)


def test_the_fixture_has_lineage_that_crosses_a_project_edge(lane):
    """Without this the test above could pass on a graph where scoping changes nothing: for projects {1, 3}, the
    unscoped statement answers differently on the whole graph than on the graph pruned to what that caller sees, for
    a sample the caller can see, and it reaches a foreign sample from one."""
    lane.reload()
    caller = (1, 3)
    full = {sid: _lineage(lane, sid, None) for sid in SAMPLE_IDS}
    lane.prune(caller)
    visible = {row["id"] for row in lane.read("MATCH (s:Sample) RETURN s.id AS id")}
    pruned = {sid: _lineage(lane, sid, None) for sid in SAMPLE_IDS if sid in visible}
    assert any(full[sid][:2] != lineage[:2] for sid, lineage in pruned.items())
    assert any(fixture_graph.forbidden_markers(full[sid][2], caller) for sid in pruned)
