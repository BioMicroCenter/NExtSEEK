"""The CC ``graph`` and ``aggregate`` ops never hand a caller who is not an admin a value from Neo4j's error text.

A statement the prover accepts can still read a node an earlier pattern scoped in a later clause, and Neo4j may
evaluate that clause on a foreign node first; its message for a type error or an unparsable date quotes the value. The
Neo4j tool keeps only the error's codes for such a caller (``RUNTIME_ERROR_WITHHELD``), so neither op's result, nor
the aggregate op's notes, can carry it. An admin keeps the full text.

Both ops run the real ``tool_neo4j_query`` over a fake ``neo4j`` module whose every statement raises a real driver
error, and whose EXPLAIN plans cleanly: the failure came from the data. The agents and the REST call are faked.
"""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import neo4j as _real_neo4j
import pytest
from neo4j.exceptions import Neo4jError

from chat_nextseek.graph_scope import GraphScope, with_scope
from chat_nextseek.schemas import GraphAgentPlan, ParserPlan
from NessieAI.ns.granular import run_op

MEMBER = GraphScope.for_projects((2, 13), source="test")
ADMIN = GraphScope.admin("test")
MARKER = "Ada Lovelace ZQF4"
QUESTION = "How many of these samples have no parseable collection date?"
PROBE = "MATCH (s:Sample {uuid: 'MUS-230101AAA-1'}) WITH s WHERE date(s.Scientist) IS NULL RETURN count(s) AS n"
CODE = "Neo.ClientError.Statement.SyntaxError"


def _date_error() -> Neo4jError:
    """What Neo4j sends for a date it cannot parse: a syntax-coded error whose message quotes the stored text."""
    message = f'Text cannot be parsed to a Date\n"{MARKER}"\n ^'
    return Neo4jError._hydrate_gql(
        gql_status="22007", description=f"error: data exception - invalid date. {message}", message=message,
        neo4j_code=CODE, diagnostic_record={"_classification": "CLIENT_ERROR", "OPERATION": "", "CURRENT_SCHEMA": "/"},
    )


class _Session:
    def __init__(self):
        self.statements: list[str] = []

    def execute_read(self, fn, *args, **kwargs):
        session = self

        class _Tx:
            def run(self, cypher, parameters=None, **kw):
                session.statements.append(cypher)
                if cypher.startswith("EXPLAIN "):
                    return SimpleNamespace(consume=lambda: None)
                raise _date_error()

        return fn(_Tx(), *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def driver(monkeypatch):
    handle = SimpleNamespace(session=_Session())
    module = types.ModuleType("neo4j")
    module.GraphDatabase = SimpleNamespace(
        driver=lambda *a, **k: SimpleNamespace(session=lambda **kw: handle.session, close=lambda: None))
    module.unit_of_work = _real_neo4j.unit_of_work
    monkeypatch.setitem(sys.modules, "neo4j", module)
    return handle


def _config(scope):
    return with_scope(SimpleNamespace(NEO4J_PASSWORD="p", NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="u"), scope)


def _run(op, scope):
    build = MagicMock(return_value=SimpleNamespace(
        endpoint="/nextseek_api/samples/graph_search/", method="POST", requestBody={}, queryParameters=None,
        model_dump=lambda: {}))
    rest = MagicMock(return_value={"ok": True, "status_code": 200, "data": {"total": 1, "rows": []}})
    with patch("chat_nextseek.portable.entity_agent", return_value=SimpleNamespace(model_dump=lambda: {})), \
         patch("chat_nextseek.portable.parser_agent", return_value=ParserPlan(mode="graph_query")), \
         patch("chat_nextseek.portable.graph_agent", return_value=GraphAgentPlan(cypher=PROBE, parameters={})), \
         patch("chat_nextseek.portable.api_agent_build_request", build), \
         patch("chat_nextseek.helpers.tool_nextseek_api_request", rest):
        return run_op(op, {"query": QUESTION, "parts": ""}, config=_config(scope), session=SimpleNamespace(),
                      write_gate=MagicMock())


def test_the_graph_op_withholds_the_value_from_a_member(driver):
    out = _run("graph", MEMBER)

    assert out["result"]["ok"] is False
    assert CODE in out["result"]["error"]
    assert MARKER not in json.dumps(out, default=str)
    assert "fallback" not in out  # a runtime failure is not a scope refusal


def test_the_aggregate_op_withholds_the_value_from_a_member(driver):
    out = _run("aggregate", MEMBER)

    part = out["parts"][0]
    assert part["status"] == "error"
    assert CODE in part["error"]
    assert MARKER not in json.dumps(out, default=str)
    assert any(CODE in note for note in out["notes"])


@pytest.mark.parametrize("op", ["graph", "aggregate"])
def test_an_admin_keeps_the_full_text(driver, op):
    out = _run(op, ADMIN)

    assert MARKER in json.dumps(out, default=str)
    assert not any(s.startswith("EXPLAIN ") for s in driver.session.statements)
