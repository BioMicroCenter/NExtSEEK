"""The ``aggregate`` op holds every statement to the caller's projects (spec 2026-09-18-graph-cypher-scope, 7.1).

The op never widens a scope: it hands the config the view built to every call, takes no Cypher and no project list
from the caller, and runs every statement, a retry's too, through the real ``tool_neo4j_query``. Here that tool
runs for real over a fake ``neo4j`` module that records every statement; the agents and the REST call are faked.
The non-superuser is the shape of a real test account: projects 2 and 13.
"""
from __future__ import annotations

import inspect
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import neo4j as _real_neo4j
import pytest
from pydantic import ValidationError

from chat_nextseek.graph_scope import SCOPE_PARAM, GraphScope, with_scope
from chat_nextseek.schemas import GraphAgentPlan, ParserPlan
from NessieAI.ns import aggregate, granular
from NessieAI.ns.granular import run_op
from nextseek_api.assistant.models_api import AggregateOpRequest

MEMBER = GraphScope.for_projects((2, 13), source="test")
ADMIN = GraphScope.admin("test")
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"
BREAKDOWN = "MATCH (s:T_NHP) RETURN s.Species AS species, count(DISTINCT s) AS n ORDER BY n DESC"
CALL = "CALL { MATCH (s:T_NHP) RETURN s } RETURN s.Species AS species, count(s) AS n"
ROWS = [{"species": "Macaca mulatta", "n": 3}, {"species": None, "n": 1}]


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return SimpleNamespace(counters=None)


class _Session:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements: list[tuple[str, dict]] = []

    def execute_read(self, fn, *args, **kwargs):
        session = self

        class _Tx:
            def run(self, cypher, parameters=None, **kw):
                session.statements.append((cypher, dict(parameters or {})))
                return _Result(session.rows)

        return fn(_Tx(), *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def driver(monkeypatch):
    """A fake ``neo4j`` module: ``.session`` records every statement, ``.opened`` every driver opened."""
    handle = SimpleNamespace(opened=[], session=_Session(ROWS))

    def factory(*args, **kwargs):
        handle.opened.append((args, kwargs))
        return SimpleNamespace(session=lambda **k: handle.session, close=lambda: None)

    module = types.ModuleType("neo4j")
    module.GraphDatabase = SimpleNamespace(driver=factory)
    module.unit_of_work = _real_neo4j.unit_of_work
    monkeypatch.setitem(sys.modules, "neo4j", module)
    return handle


def _config(scope):
    base = SimpleNamespace(NEO4J_PASSWORD="p", NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="u")
    return base if scope is None else with_scope(base, scope)


def _run(config, cyphers, *, parts=None, query="Break the NHP samples down by species."):
    """``cyphers`` maps a part to the statements its graph agent writes, in order."""
    calls: dict[str, int] = {}

    def graph(cfg, q, entity_out, parser_plan, retry_context=None, refine_context=None):
        calls[q] = calls.get(q, 0) + 1
        script = cyphers[q]
        return GraphAgentPlan(cypher=script[min(calls[q], len(script)) - 1], parameters={})

    build = MagicMock(return_value=SimpleNamespace(
        endpoint=GRAPH_SEARCH, method="POST", requestBody={"sample_type": "T_NHP"}, queryParameters=None,
        model_dump=lambda: {"endpoint": GRAPH_SEARCH}))
    rest = MagicMock(return_value={"ok": True, "status_code": 200, "data": {"total": 4, "rows": []}})
    args = {"query": query, "parts": json.dumps(parts) if parts else ""}
    with patch("chat_nextseek.portable.entity_agent", return_value=SimpleNamespace(model_dump=lambda: {})), \
         patch("chat_nextseek.portable.parser_agent", return_value=ParserPlan(mode="graph_query")), \
         patch("chat_nextseek.portable.graph_agent", side_effect=graph), \
         patch("chat_nextseek.portable.api_agent_build_request", build), \
         patch("chat_nextseek.helpers.tool_nextseek_api_request", rest):
        out = run_op("aggregate", args, config=config, session=SimpleNamespace(), write_gate=MagicMock())
    return out, calls, rest


def test_a_members_breakdown_runs_with_their_projects_bound(driver):
    out, _, _ = _run(_config(MEMBER), {"Break the NHP samples down by species.": [BREAKDOWN]})

    ((cypher, params),) = driver.session.statements
    assert f"${SCOPE_PARAM}" in cypher
    assert params[SCOPE_PARAM] == [2, 13]
    assert cypher.rstrip().endswith("LIMIT 1001")
    part = out["parts"][0]
    assert part["status"] == "ok"
    assert part["scope"]["decision"] == "proven"
    assert part["scope"]["project_ids"] == [2, 13]
    assert (part["sum_of_group_counts"], part["null_group"]) == (4, 1)


def test_a_project_named_in_the_question_narrows_and_never_widens(driver):
    narrowed = "MATCH (s:T_NHP) WHERE 99 IN s.project_ids RETURN count(DISTINCT s) AS n"
    driver.session.rows = [{"n": 0}]
    _run(_config(MEMBER), {"Break the NHP samples down by species.": [narrowed, narrowed]})

    for cypher, params in driver.session.statements:
        assert "99 IN s.project_ids" in cypher
        assert f"${SCOPE_PARAM}" in cypher
        assert params[SCOPE_PARAM] == [2, 13]


def test_a_call_subquery_never_reaches_the_driver(driver):
    q = "Break the NHP samples down by species."
    out, calls, rest = _run(_config(MEMBER), {q: [CALL, CALL]})

    assert driver.opened == []
    assert driver.session.statements == []
    assert calls[q] == 2  # the one shape repair, refused again
    part = out["parts"][0]
    assert part["status"] == "fallback"
    assert "call_subquery" in part["scope"]["codes"]
    assert part["sum_of_group_counts"] == 4 and part["groups"] == []
    rest.assert_called_once()


def test_with_no_scope_every_part_is_refused_and_nothing_runs(driver):
    parts = ["How many NHP samples?", "How many TIS samples?"]
    out, _, _ = _run(_config(None), {parts[0]: [BREAKDOWN], parts[1]: [BREAKDOWN]}, parts=parts)

    assert driver.opened == []
    for part in out["parts"]:
        assert part["scope"]["decision"] == "refused"
        assert part["scope"]["codes"] == ["no_scope"]
        assert part["status"] in ("fallback", "refused")


def test_an_admins_statement_runs_unchanged(driver):
    out, _, _ = _run(_config(ADMIN), {"Break the NHP samples down by species.": [BREAKDOWN]})

    ((cypher, params),) = driver.session.statements
    assert cypher == BREAKDOWN + "\nLIMIT 1001"
    assert SCOPE_PARAM not in params
    assert out["parts"][0]["scope"]["decision"] == "admin"


@pytest.mark.parametrize("field, value", [
    ("projects", [1, 2, 3]), ("scope", {"is_admin": True}), ("is_admin", True),
    ("cypher", "MATCH (s:Sample) RETURN count(s) AS n"),
])
def test_the_request_carries_no_scope_and_no_cypher(field, value):
    with pytest.raises(ValidationError):
        AggregateOpRequest.model_validate({"query": "How many samples?", field: value})


def test_the_op_never_builds_or_widens_a_scope():
    source = Path(aggregate.__file__).read_text(encoding="utf-8")
    chain = inspect.getsource(granular.run_graph_question)
    for name in ("with_scope", "GraphScope.admin", "operator_scope_from_env"):
        assert name not in source, f"aggregate.py names {name}"
        assert name not in chain, f"run_graph_question names {name}"
