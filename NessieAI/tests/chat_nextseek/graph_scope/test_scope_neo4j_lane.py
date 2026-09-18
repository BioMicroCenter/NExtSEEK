"""
The project scope, proven on a real Neo4j: a caller who is not an admin reads nothing outside their projects.

Runs only under lane.sh (a private, throwaway Neo4j holding fixture_graph.py); elsewhere every test here skips.
Callers: projects {1, 3}, project {2}, no projects, and admin. Statements: the taught corpus, the accepted extras, the
report runners' statements, the refusal table and its hidden variants, and about 300 seeded generator statements.

Two arms. The prover arm runs ``scope_cypher`` and then the lane's own READ transaction (plus ``strip_hidden`` for a
non-admin, as the tool does); the tool arm runs ``tool_neo4j_query`` with a config carrying the ``GraphScope`` and
reads the result's ``scope`` field, which the plumbing unit adds (until it lands, run the prover arm with
``-k prover``).

For every caller who is not an admin:

- differential oracle: every accepted statement's rows equal the rows the original statement returns, as admin, on
  the graph with every sample and orphan the caller cannot see and every project outside the scope deleted
  (fulltext score columns excluded, list order ignored);
- marker: no accepted statement's rows carry a marker the caller may not read;
- refusals: taught and accepted shapes are accepted, refusal-table statements refuse with exactly their codes
  (generator statements may refuse freely);
- an injected statement that fails on Neo4j while its original runs is a failure.

For admin, every statement runs exactly as submitted. Every write is refused and the graph is unchanged afterwards.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 11.2.
"""
from __future__ import annotations

import functools
import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from chat_nextseek.cypher_scope import Refused, Scoped, scope_cypher, strip_hidden
from chat_nextseek.cypher_text import write_clause
from chat_nextseek.graph_scope import SCOPE_PARAM, GraphScope, with_scope

from NessieAI.tests.chat_nextseek.graph_scope import battery, fixture_graph, generator

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

NON_ADMIN = {name: ids for name, ids in fixture_graph.CALLERS.items() if ids is not None}
MIN_ACCEPTED_GENERATED = 120


class Stmt(NamedTuple):
    id: str
    cypher: str
    params: dict
    expect: Any  # "accept", a tuple of refusal codes, or None (free)


@functools.lru_cache(maxsize=1)
def statements() -> tuple[Stmt, ...]:
    out = [Stmt(c.id, c.cypher, c.params, "accept") for c in battery.TAUGHT + battery.ACCEPTED]
    with tempfile.TemporaryDirectory() as tmp:
        out += [Stmt(f"report{k}", cypher, params, "accept")
                for k, (cypher, params) in enumerate(battery.report_statements(tmp))]
    out += [Stmt(r.id, r.cypher, r.params, r.codes) for r in battery.TAUGHT_REFUSED + battery.REFUSALS]
    out += [Stmt(v[0], v[1], {}, v[2] or "accept") for v in battery.hidden_variants()]
    out += [Stmt(g.id, g.cypher, g.params, None) for g in generator.generate()]
    return tuple(out)


def _reserved(params: dict) -> bool:
    return any(isinstance(k, str) and k.lower().startswith("__scope") for k in params)


# --------------------------------------------------------------------------- #
# Row comparison
# --------------------------------------------------------------------------- #

def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "nodes") and hasattr(value, "relationships") and not hasattr(value, "items"):
        return {"nodes": [_plain(n) for n in value.nodes], "relationships": [_plain(r) for r in value.relationships]}
    if hasattr(value, "element_id") and callable(getattr(value, "items", None)):
        props = {str(k): _plain(v) for k, v in value.items()}
        if hasattr(value, "labels"):
            return {"labels": sorted(value.labels), "props": props}
        return {"type": getattr(value, "type", None), "props": props}
    return str(value)


def _sorted(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sorted(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((_sorted(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def _canon(rows: list[dict]) -> list[str]:
    out = []
    for row in rows:
        kept = {k: v for k, v in dict(row).items() if k != "score"}
        out.append(json.dumps(_sorted(_plain(kept)), sort_keys=True))
    return sorted(out)


# --------------------------------------------------------------------------- #
# The two arms
# --------------------------------------------------------------------------- #

def _prover_arm(lane, stmt: Stmt, scope: GraphScope) -> tuple[str, Any, Any]:
    out = scope_cypher(stmt.cypher, stmt.params, scope)
    if isinstance(out, Refused):
        return "refused", out.codes, out
    assert isinstance(out, Scoped)
    try:
        rows = lane.read(out.cypher, out.parameters)
    except Exception as err:
        return "error", repr(err), out
    return "rows", (rows if scope.is_admin else strip_hidden(rows)), out


def _tool_config(scope: GraphScope | None):
    base = SimpleNamespace(NEO4J_URI=URI, NEO4J_USER="neo4j", NEO4J_PASSWORD=PASSWORD, NEO4J_DATABASE="neo4j")
    return with_scope(base, scope)


def _tool_arm(lane, stmt: Stmt, scope: GraphScope) -> tuple[str, Any, Any]:
    from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

    result = tool_neo4j_query(_tool_config(scope), stmt.cypher, stmt.params)
    decision = result["scope"]["decision"]
    assert result["submitted_cypher"] == stmt.cypher
    if decision == "not_checked":  # write_clause refused it first: a write or a procedure call
        assert result["ok"] is False and result["scope"]["codes"] == ["write"]
        assert write_clause(stmt.cypher) is not None
        return "refused", ("write",), result
    if decision == "refused":
        assert result["ok"] is False
        return "refused", tuple(result["scope"]["codes"]), result
    if not result["ok"]:
        return "error", result.get("error"), result
    if scope.is_admin:
        assert decision == "admin" and result["cypher"] == stmt.cypher
    else:
        assert decision == "proven"
        assert result["parameters"][SCOPE_PARAM] == list(scope.project_ids)
        assert result["scope"]["project_ids"] == list(scope.project_ids)
    return "rows", result["data"], result


def _differential(lane, caller: tuple[int, ...], arm) -> dict:
    """Run every statement for one caller through one arm; return the findings and the counts."""
    scope = GraphScope.for_projects(caller, source="test")
    lane.reload()
    failures: list[str] = []
    recorded: dict[str, list[str]] = {}
    invalid: list[str] = []
    accepted_generated = 0
    for stmt in statements():
        kind, payload, _ = arm(lane, stmt, scope)
        if stmt.expect == "accept" and kind == "refused":
            failures.append(f"{stmt.id}: refused {payload}")
            continue
        if isinstance(stmt.expect, tuple) and (kind != "refused" or tuple(payload) not in (stmt.expect, ("write",))):
            shown = "" if kind == "rows" else payload
            failures.append(f"{stmt.id}: expected refusal {stmt.expect}, got {kind} {shown}")
            continue
        if kind == "refused":
            continue
        try:
            lane.read(stmt.cypher, stmt.params)
        except Exception:
            invalid.append(stmt.id)  # the original itself does not run: nothing to compare
            continue
        if kind == "error":
            failures.append(f"{stmt.id}: the scoped statement failed while its original runs: {payload}")
            continue
        text = json.dumps(_plain(payload), sort_keys=True)
        leaked = fixture_graph.forbidden_markers(text, caller)
        if leaked:
            failures.append(f"{stmt.id}: rows carry {sorted(set(leaked))}")
        recorded[stmt.id] = _canon(payload)
        if stmt.expect is None:
            accepted_generated += 1
    lane.prune(caller)
    for stmt in statements():
        if stmt.id not in recorded:
            continue
        oracle = _canon(strip_hidden(lane.read(stmt.cypher, stmt.params)))
        if oracle != recorded[stmt.id]:
            failures.append(f"{stmt.id}: scoped rows {recorded[stmt.id][:3]} differ from the pruned graph's "
                            f"{oracle[:3]}")
    return {"failures": failures, "compared": len(recorded), "invalid": invalid,
            "accepted_generated": accepted_generated}


def _assert_differential(report: dict, caller) -> None:
    print(f"caller {caller}: compared {report['compared']}, generator accepted {report['accepted_generated']} "
          f"of {generator.COUNT}, originals that do not run {len(report['invalid'])}: {report['invalid'][:10]}")
    assert not report["failures"], "\n".join(report["failures"][:40])
    assert report["accepted_generated"] >= MIN_ACCEPTED_GENERATED


def _run_writes(lane, run_one) -> None:
    lane.reload()
    before = lane.counts()
    for text in battery.WRITES:
        assert write_clause(text) is not None, text
        run_one(text)
    assert lane.counts() == before


# --------------------------------------------------------------------------- #
# The prover arm
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_prover_scoped_rows_equal_the_pruned_graph(lane, caller):
    _assert_differential(_differential(lane, caller, _prover_arm), caller)


def test_prover_admin_runs_the_submitted_text(lane):
    lane.reload()
    admin = GraphScope.admin("test")
    for stmt in statements():
        out = scope_cypher(stmt.cypher, stmt.params, admin)
        if _reserved(stmt.params):
            assert isinstance(out, Refused) and out.codes == ("reserved_parameter",)
            continue
        assert isinstance(out, Scoped) and out.decision == "admin", stmt.id
        assert out.cypher == stmt.cypher and out.parameters == stmt.params
        try:
            direct = _canon(lane.read(stmt.cypher, stmt.params))
        except Exception:
            continue
        if direct != _canon(lane.read(stmt.cypher, stmt.params)):
            continue  # not deterministic (randomUUID()): two direct runs already differ
        assert _canon(lane.read(out.cypher, out.parameters)) == direct, stmt.id


def test_prover_refuses_writes_and_the_graph_is_unchanged(lane):
    def run_one(text):
        for ids in NON_ADMIN.values():
            assert isinstance(scope_cypher(text, {}, GraphScope.for_projects(ids)), Refused), text
        with pytest.raises(Exception):
            lane.read(text)  # the READ transaction refuses whatever a text check might miss

    _run_writes(lane, run_one)


# --------------------------------------------------------------------------- #
# The tool arm (needs the plumbing unit: tool_neo4j_query's result["scope"])
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_tool_scoped_rows_equal_the_pruned_graph(lane, caller):
    _assert_differential(_differential(lane, caller, _tool_arm), caller)


def test_tool_admin_runs_the_submitted_text(lane):
    from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

    lane.reload()
    config = _tool_config(GraphScope.admin("test"))
    for stmt in statements():
        if write_clause(stmt.cypher) is not None or _reserved(stmt.params):
            continue
        result = tool_neo4j_query(config, stmt.cypher, stmt.params)
        assert result["submitted_cypher"] == stmt.cypher
        assert result["scope"]["decision"] == "admin", stmt.id
        assert result["cypher"] == stmt.cypher
        if not result["ok"]:
            continue
        direct = _canon(lane.read(stmt.cypher, stmt.params))
        if direct != _canon(lane.read(stmt.cypher, stmt.params)) or result.get("truncated"):
            continue
        assert _canon(result["data"]) == direct, stmt.id


def test_tool_refuses_writes_for_every_caller_and_the_graph_is_unchanged(lane):
    from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

    scopes = [GraphScope.for_projects(ids, source="test") for ids in NON_ADMIN.values()] + [GraphScope.admin("test")]

    def run_one(text):
        for scope in scopes:
            result = tool_neo4j_query(_tool_config(scope), text, {})
            assert result["ok"] is False, text
            assert result["scope"]["decision"] == "not_checked" and result["scope"]["codes"] == ["write"], text

    _run_writes(lane, run_one)


def test_tool_without_a_scope_refuses_before_any_query(lane):
    from chat_nextseek.helpers.tools.neo4j import is_scope_refusal, tool_neo4j_query

    lane.reload()
    result = tool_neo4j_query(_tool_config(None), "MATCH (s:Sample) RETURN count(*) AS n", {})
    assert result["ok"] is False
    assert is_scope_refusal(result)
    assert "no_scope" in result["scope"]["codes"]
