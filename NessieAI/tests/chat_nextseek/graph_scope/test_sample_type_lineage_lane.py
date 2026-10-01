"""
The two sample type lineage endpoints stop at the caller's project edge, proven on a real Neo4j.

Runs only under lane.sh (a private, throwaway Neo4j holding fixture_graph.py); elsewhere every test here skips.
The statements are the ones ``nextseek_api/services/sample_types.py`` runs for ``sampletypes/{uid}/child_types/`` and
``sample_types/get_parents/parents_by_child_types/``. For each caller who is not an admin, the scoped statement runs on
the whole fixture; the differential oracle then deletes every sample and orphan the caller cannot see and every
project outside the scope, runs the admin statement with the same inputs, and requires the same answer. For
child_types, a sample the caller cannot see must return no row at all (the endpoint answers 404).

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md, decision 3.
"""
from __future__ import annotations

import itertools
import os

import pytest

from NessieAI.tests.chat_nextseek.graph_scope import fixture_graph

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

NON_ADMIN = {name: ids for name, ids in fixture_graph.CALLERS.items() if ids is not None}
TYPE_CODES = ("MUS", "TIS", "SLD", "CHM")
SAMPLE_IDS = sorted(sample["props"]["id"] for sample in fixture_graph.samples())


def _parent_requests() -> list[tuple[str, list[str], list[str] | None]]:
    out = []
    for size in (1, 2):
        for children in itertools.combinations(TYPE_CODES, size):
            for parent in (None, *TYPE_CODES):
                out.append((f"{'+'.join(children)}<-{parent or 'any'}", list(children),
                            None if parent is None else [parent]))
    return out


def _child_types(lane, sample_id: int, caller: tuple[int, ...] | None) -> list[dict]:
    from nextseek_api.services.sample_types import _CHILD_TYPES_CYPHER, _CHILD_TYPES_SCOPED_CYPHER

    if caller is None:
        return lane.read(_CHILD_TYPES_CYPHER, {"id": sample_id})
    return lane.read(_CHILD_TYPES_SCOPED_CYPHER, {"id": sample_id, "projects": list(caller)})


def _parents(lane, children: list[str], parents: list[str] | None, caller: tuple[int, ...] | None) -> list[int]:
    from nextseek_api.services.sample_types import _parents_cypher

    params: dict = {"types": children}
    if parents:
        params["parent_types"] = parents
    if caller is not None:
        params["projects"] = list(caller)
    statement = _parents_cypher(with_parent_filter=bool(parents), scoped=caller is not None)
    return sorted(row["id"] for row in lane.read(statement, params))


def _types(rows: list[dict]) -> list[str]:
    return sorted({row["type"] for row in rows if row["type"] is not None})


@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_child_types_equal_the_pruned_graph(lane, caller):
    lane.reload()
    scoped = {sid: _child_types(lane, sid, caller) for sid in SAMPLE_IDS}
    lane.prune(caller)
    visible = {row["id"] for row in lane.read("MATCH (s:Sample) RETURN s.id AS id")}
    failures = []
    for sid, rows in scoped.items():
        if sid not in visible:
            if rows:
                failures.append(f"{sid}: not visible, yet the scoped statement returned {rows}")
            continue
        if not rows:
            failures.append(f"{sid}: visible, yet the scoped statement returned no row")
        oracle = _types(_child_types(lane, sid, None))
        if _types(rows) != oracle:
            failures.append(f"{sid}: scoped types {_types(rows)} differ from the pruned graph's {oracle}")
    print(f"caller {caller}: {len(scoped)} samples, {len(visible)} visible")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_parents_by_child_types_equal_the_pruned_graph(lane, caller):
    lane.reload()
    requests = _parent_requests()
    scoped = {rid: _parents(lane, children, parents, caller) for rid, children, parents in requests}
    lane.prune(caller)
    failures = []
    for rid, children, parents in requests:
        oracle = _parents(lane, children, parents, None)
        if scoped[rid] != oracle:
            failures.append(f"{rid}: scoped ids {scoped[rid]} differ from the pruned graph's {oracle}")
    print(f"caller {caller}: {len(requests)} requests, {sum(1 for ids in scoped.values() if ids)} with rows")
    assert not failures, "\n".join(failures)


def test_the_fixture_has_lineage_that_crosses_a_project_edge(lane):
    """Without this the two tests above could pass on a graph where scoping changes nothing: for projects {1, 3}, the
    unscoped statements answer differently on the whole graph than on the graph pruned to what that caller sees."""
    lane.reload()
    caller = (1, 3)
    full_children = {sid: _types(_child_types(lane, sid, None)) for sid in SAMPLE_IDS}
    full_parents = {rid: _parents(lane, c, p, None) for rid, c, p in _parent_requests()}
    lane.prune(caller)
    visible = {row["id"] for row in lane.read("MATCH (s:Sample) RETURN s.id AS id")}
    pruned_children = {sid: _types(_child_types(lane, sid, None)) for sid in SAMPLE_IDS if sid in visible}
    pruned_parents = {rid: _parents(lane, c, p, None) for rid, c, p in _parent_requests()}
    assert any(full_children[sid] != types for sid, types in pruned_children.items())
    assert any(set(full_parents[rid]) & visible != set(pruned_parents[rid]) for rid in full_parents)
