"""
A statement that fails while it runs never hands a caller who is not an admin Neo4j's message.

Neo4j's message for a runtime failure can quote the stored value it failed on: a type error prints the value, and a
date it cannot parse prints the text. The prover still accepts statements where a later clause reads a node an earlier
pattern scoped (``MATCH (s {uuid: 'X'}) WITH s WHERE date(s.Organ) IS NULL``), and Neo4j may evaluate that clause on a
foreign node before the node's scope clause, so the value can be another project's. For such a caller the tool keeps
only the error's codes. A statement Neo4j cannot compile fails the same way whatever the graph holds: EXPLAIN, which
reads no data, reproduces it, and its message is kept so the graph agent's retry can see what to fix. Neo4j files an
unparsable date under ``Neo.ClientError.Statement.SyntaxError``, so the code alone cannot tell the two apart. An admin
keeps the full text.

The NS graph turn feeds the error into its retry prompt, its debug payload, the graph debug JSON it registers as a
download, the bundle and the chatter: none of them may carry the value for a member.

A fake ``neo4j`` module raises real driver errors; no database or model is reached.
"""
from __future__ import annotations

import json
import sys
import time
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import neo4j as _real_neo4j
import pytest
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from chat_nextseek import orchestrator as orch
from chat_nextseek.graph_scope import GraphScope, with_scope
from chat_nextseek.helpers.tools import neo4j as tool_module
from chat_nextseek.helpers.tools.neo4j import is_scope_refusal, tool_neo4j_query
from chat_nextseek.schemas import EntityAgentOutput, GraphAgentPlan, ParserPlan

MEMBER = GraphScope.for_projects([2, 13], source="test")
ADMIN = GraphScope.admin("test")
MARKER = "Ada Lovelace ZQF4"
PROBE = "MATCH (s:Sample {uuid: 'MUS-230101AAA-1'}) WITH s WHERE date(s.Scientist) IS NULL RETURN count(s) AS n"


def _withheld(codes: str | None = None) -> str:
    """The fixed text a member gets (or its opening, before the codes)."""
    text = tool_module.RUNTIME_ERROR_WITHHELD
    return text.format(codes=codes) if codes is not None else text.split("{", 1)[0]


def _server_error(code: str, message: str, gql_status: str = "22007") -> Neo4jError:
    """A driver error as the server sends it (neo4j 6: GQL status, description, message, Neo4j code)."""
    return Neo4jError._hydrate_gql(
        gql_status=gql_status, description=f"error: data exception. {message}", message=message,
        neo4j_code=code, diagnostic_record={"_classification": "CLIENT_ERROR", "OPERATION": "", "CURRENT_SCHEMA": "/"},
    )


def _date_error() -> Neo4jError:
    # Neo4j files a date it cannot parse as a syntax error, with the text in the message.
    return _server_error("Neo.ClientError.Statement.SyntaxError",
                         f'Text cannot be parsed to a Date\n"{MARKER}"\n ^', gql_status="22007")


def _type_error() -> Neo4jError:
    return _server_error("Neo.ClientError.Statement.TypeError",
                         f"Expected a string value for `toLower`, but got: String(\"{MARKER}\"); consider converting",
                         gql_status="22N01")


def _compile_error() -> Neo4jError:
    return _server_error("Neo.ClientError.Statement.SyntaxError", "Variable `t` not defined (line 1, column 60)",
                         gql_status="42N62")


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return SimpleNamespace(counters=None)


class _Session:
    """Raises ``run_error`` for the statement; for EXPLAIN, raises ``plan_error`` or plans cleanly."""

    def __init__(self, run_error, plan_error=None):
        self.run_error = run_error
        self.plan_error = plan_error
        self.statements: list[str] = []

    def execute_read(self, fn, *args, **kwargs):
        session = self

        class _Tx:
            def run(self, cypher, parameters=None, **kw):
                session.statements.append(cypher)
                if cypher.startswith("EXPLAIN "):
                    if session.plan_error is not None:
                        raise session.plan_error
                    return _Result()
                raise session.run_error

        return fn(_Tx(), *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake(monkeypatch):
    """Install a fake ``neo4j`` module; ``.install(session)`` wires it in."""
    handle = SimpleNamespace(session=None)

    def install(session):
        handle.session = session
        driver = SimpleNamespace(session=lambda **k: session, close=lambda: None)
        module = types.ModuleType("neo4j")
        module.GraphDatabase = SimpleNamespace(driver=lambda *a, **k: driver)
        module.unit_of_work = _real_neo4j.unit_of_work
        monkeypatch.setitem(sys.modules, "neo4j", module)
        return session

    handle.install = install
    return handle


def _cfg(scope):
    base = SimpleNamespace(NEO4J_URI="bolt://graph:7687", NEO4J_USER="u", NEO4J_PASSWORD="p",
                           NEO4J_DATABASE="neo4j", MODEL_MODE="test")
    return with_scope(base, scope)


def _visible(value) -> str:
    return json.dumps(value, default=str)


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("error", [_date_error, _type_error], ids=["date_parse_coded_as_syntax", "type_error"])
def test_a_members_runtime_failure_keeps_only_its_codes(fake, error):
    raised = error()
    session = fake.install(_Session(raised))

    out = tool_neo4j_query(_cfg(MEMBER), PROBE, {})

    assert out["ok"] is False
    assert MARKER not in _visible(out)
    assert out["error"].startswith(_withheld())
    assert raised.code in out["error"] and raised.gql_status in out["error"]
    assert out["scope"]["decision"] == "proven" and is_scope_refusal(out) is False
    assert [s for s in session.statements if s.startswith("EXPLAIN ")] == [f"EXPLAIN {out['cypher']}"]


def test_an_admin_keeps_neo4js_full_text(fake):
    session = fake.install(_Session(_date_error()))

    out = tool_neo4j_query(_cfg(ADMIN), PROBE, {})

    assert out["ok"] is False
    assert MARKER in out["error"]
    assert out["scope"]["decision"] == "admin"
    assert not any(s.startswith("EXPLAIN ") for s in session.statements)


def test_a_statement_neo4j_cannot_compile_keeps_the_plans_message(fake):
    fake.install(_Session(_compile_error(), plan_error=_compile_error()))

    out = tool_neo4j_query(_cfg(MEMBER), PROBE, {})

    assert "Variable `t` not defined" in out["error"]
    assert not out["error"].startswith(_withheld())


def test_a_plan_that_fails_for_another_reason_does_not_release_the_text(fake):
    busy = _server_error("Neo.TransientError.General.DatabaseUnavailable", "database unavailable", "50N42")
    fake.install(_Session(_date_error(), plan_error=busy))

    out = tool_neo4j_query(_cfg(MEMBER), PROBE, {})

    assert MARKER not in _visible(out)
    assert "database unavailable" not in out["error"]
    assert out["error"].startswith(_withheld())


def test_a_driver_failure_is_withheld_and_never_planned(fake):
    session = fake.install(_Session(ServiceUnavailable(f"routing failed near {MARKER}")))

    out = tool_neo4j_query(_cfg(MEMBER), PROBE, {})

    assert MARKER not in _visible(out)
    assert out["error"] == _withheld("ServiceUnavailable")
    assert not any(s.startswith("EXPLAIN ") for s in session.statements)


# --------------------------------------------------------------------------- #
# The NS graph turn
# --------------------------------------------------------------------------- #

def _turn(monkeypatch, tmp_path, scope):
    """One graph turn over the real tool; every statement fails with the date error. Returns what the turn exposed."""
    seen = {"retry_contexts": [], "chatter": None, "events": []}

    def graph_agent(config, user_text, entity_result, plan, retry_context=None, refine_context=None):
        if retry_context:
            seen["retry_contexts"].append(retry_context)
        return GraphAgentPlan(cypher=PROBE, context_mode="catalog")

    def chatter(*args, **kwargs):
        seen["chatter"] = {"args": args, "kwargs": kwargs}
        return "reply"

    monkeypatch.setattr(orch, "graph_agent", graph_agent)
    monkeypatch.setattr(orch, "chatter_agent_answer", chatter)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: seen.setdefault("append_turn", k))
    session: dict = {}
    debug: dict = {}
    payload = orch._execute_graph_turn(
        config=_cfg(scope), session=session, user_text="when were these samples dated",
        entity_result=EntityAgentOutput(), plan=ParserPlan(mode="graph_query", intent_summary="dates"),
        log_dir=str(tmp_path), artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda name, data: seen["events"].append((name, data)), debug_payload=debug,
        t_total_start=time.perf_counter(),
    )
    files = {str(p): p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file()}
    return {"payload": payload, "debug": debug, "session": session, "files": files, **seen}


def test_a_members_graph_turn_never_carries_the_value(fake, monkeypatch, tmp_path):
    fake.install(_Session(_date_error()))

    exposed = _turn(monkeypatch, tmp_path, MEMBER)

    assert exposed["retry_contexts"], "the turn should have retried the failed statement"
    assert exposed["files"], "the turn should have written its graph debug JSON"
    assert MARKER not in _visible(exposed)
    assert "Neo.ClientError.Statement.SyntaxError" in exposed["retry_contexts"][0]
    assert all(a["error"].startswith(_withheld()) for a in exposed["debug"]["graph_attempts"])


def test_an_admins_graph_turn_keeps_the_text(fake, monkeypatch, tmp_path):
    fake.install(_Session(_date_error()))

    exposed = _turn(monkeypatch, tmp_path, ADMIN)

    assert MARKER in exposed["retry_contexts"][0]
    assert MARKER in _visible(exposed["debug"]["graph_attempts"])
