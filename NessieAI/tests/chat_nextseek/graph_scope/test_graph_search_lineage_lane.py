"""
graph_search's lineage condition stops at the caller's project edge, proven on a real Neo4j.

Runs only under lane.sh (a private, throwaway Neo4j holding fixture_graph.py); elsewhere every test here skips.
For each caller who is not an admin, every request below is built by graph_search's own query builder
(``nextseek_api/graph_search/query.py``) with that caller's scope and run on the whole fixture; the differential
oracle then deletes every sample and orphan the caller cannot see and every project outside the scope, builds the
same request as admin, and requires the same ids. A lineage path through a sample of another project must not count,
so the fixture's cross-project chains (a visible child of a foreign parent, a foreign sample between two visible
ones) are what these requests turn on.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 7.3 and 11.2.
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
TYPE_IDS = {code: k for k, code in enumerate(TYPE_CODES, start=1)}
HOPS = (1, 2, 4)


def _catalog():
    from nextseek_api.graph_search.query import Catalog

    titles: dict[str, set[str]] = {code: {"UID"} for code in TYPE_CODES}
    for sample in fixture_graph.samples():
        code = sample["props"]["type"]
        titles[code].update(k for k in sample["props"] if k[:1].isupper())
    return Catalog(
        type_title_by_id={k: code for code, k in TYPE_IDS.items()},
        label_by_title={code: f"T_{code}" for code in TYPE_CODES},
        titles_by_type={code: frozenset(names) for code, names in titles.items()},
        value_type={},
    )


def requests() -> list[tuple[str, dict, dict]]:
    """(id, filters, extensions): every type against every lineage target, both ways, at three depths, plus a few
    fulltext-sourced ones."""
    out = []
    for start, target, direction, hops in itertools.product(TYPE_CODES, TYPE_CODES, ("ancestor", "descendant"),
                                                             HOPS):
        out.append((f"{start}-{direction}-{target}-{hops}",
                    {"sampletype_ids": [str(TYPE_IDS[start])], "filter_searchText": ""},
                    {"lineage": {"direction": direction, "sample_type": target, "max_hops": hops}}))
    for term, direction, target in (("sample", "ancestor", "CHM"), ("sample", "ancestor", "MUS"),
                                    ("alpha", "descendant", "SLD"), ("sample", "descendant", "TIS")):
        out.append((f"text-{term}-{direction}-{target}", {"filter_searchText": term},
                    {"lineage": {"direction": direction, "sample_type": target, "max_hops": 4}}))
    return out


def _ids(lane, built) -> list[int]:
    return sorted(row["id"] for row in lane.read(built.ids_cypher, built.params))


def _run(caller: tuple[int, ...] | None):
    from nextseek_api.graph_search.query import build
    from nextseek_api.graph_search.scope import Scope

    catalog = _catalog()
    scope = Scope(True, None, ()) if caller is None else Scope(False, 1, tuple(caller))
    return {rid: build(filters, extensions, scope, catalog, 1, 1000) for rid, filters, extensions in requests()}


@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_graph_search_lineage_equals_the_pruned_graph(lane, caller):
    lane.reload()
    scoped = {rid: _ids(lane, built) for rid, built in _run(caller).items()}
    lane.prune(caller)
    admin = _run(None)
    failures = []
    for rid, ids in scoped.items():
        oracle = _ids(lane, admin[rid])
        if ids != oracle:
            failures.append(f"{rid}: scoped ids {ids} differ from the pruned graph's {oracle}")
    compared = sum(1 for ids in scoped.values() if ids)
    print(f"caller {caller}: {len(scoped)} lineage requests, {compared} with rows")
    assert not failures, "\n".join(failures[:40])


def test_graph_search_lineage_battery_crosses_a_project_edge(lane):
    """The battery must include a request whose answer changes when lineage stops at the edge: for projects {1, 3},
    a project-1 tissue whose only path to a project-1 chemical runs through a project-2 mouse."""
    lane.reload()
    full = {rid: _ids(lane, built) for rid, built in _run(None).items()}
    lane.prune((1, 3))
    pruned = {rid: _ids(lane, built) for rid, built in _run(None).items()}
    visible = {row["id"] for row in lane.read("MATCH (s:Sample) RETURN s.id AS id")}
    assert any(set(full[rid]) & visible != set(pruned[rid]) for rid in full)
