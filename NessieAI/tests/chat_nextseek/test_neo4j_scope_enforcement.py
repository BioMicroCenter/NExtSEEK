"""
The Neo4j tool enforces the caller's project scope before a driver opens.

Order (spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 6.1): the write check first, unchanged;
then the config's ``GraphScope`` (none refuses with ``no_scope``); then the prover (a refusal returns without a
driver); then the existing READ path on the statement the prover returned, total probe included; and, for a caller
who is not an admin, ``strip_hidden`` over the rows. Every result, success or failure, names the statement that ran,
the one that was submitted, the parameters and the scope decision (section 6.2).

A fake driver records every statement and its parameters; no database is reached.
"""
from __future__ import annotations

import inspect
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import neo4j as _real_neo4j
import pytest

from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import SCOPE_ATTR, SCOPE_PARAM, GraphScope, with_scope
from chat_nextseek.helpers.tools import neo4j as tool_module
from chat_nextseek.helpers.tools.neo4j import (
    NO_SCOPE_REFUSED,
    RUNTIME_ERROR_WITHHELD,
    SCOPE_REFUSED,
    is_scope_refusal,
    tool_neo4j_query,
)

SCOPE_KEYS = {"decision", "source", "injected", "joined", "codes", "reasons"}
TYPED = "MATCH (s:T_TIS) WHERE s.Organ = $organ RETURN count(*) AS n"
CAPPED = "MATCH (s:T_TIS) RETURN s.uuid AS uuid LIMIT 3"
CATALOG_READ = "MATCH (a:Attribute) RETURN a.title AS title"
MEMBER = GraphScope.for_projects([3, 1], source="test")
ADMIN = GraphScope.admin("test")


class _Node:
    """Duck-typed graph node: labels, element_id and items(), as the driver's Node has."""

    def __init__(self, labels, props):
        self.labels = frozenset(labels)
        self.element_id = "4:x:1"
        self._props = dict(props)

    def items(self):
        return self._props.items()


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return SimpleNamespace(counters=None)


class _Tx:
    def __init__(self, session):
        self._session = session

    def run(self, cypher, parameters=None, **kwargs):
        self._session.statements.append((cypher, dict(parameters or {})))
        if "__total" in cypher:
            return _Result([{"__total": self._session.total}])
        if self._session.fail_query:
            raise RuntimeError("query blew up")
        return _Result(self._session.rows)


class _Session:
    def __init__(self, rows=(), total=None, fail_query=False):
        self.rows = list(rows)
        self.total = total
        self.fail_query = fail_query
        self.statements: list[tuple[str, dict]] = []

    def execute_read(self, fn, *args, **kwargs):
        return fn(_Tx(self), *args, **kwargs)

    def run(self, *args, **kwargs):
        raise AssertionError("session.run called outside a READ transaction")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake(monkeypatch):
    """Install a fake `neo4j` module; `.install(session)` returns the session, `.opened` lists driver opens."""
    handle = SimpleNamespace(opened=[])

    def install(session):
        driver = SimpleNamespace(session=lambda **k: session, close=lambda: None)

        def factory(*args, **kwargs):
            handle.opened.append((args, kwargs))
            return driver

        module = types.ModuleType("neo4j")
        module.GraphDatabase = SimpleNamespace(driver=factory)
        module.unit_of_work = _real_neo4j.unit_of_work
        monkeypatch.setitem(sys.modules, "neo4j", module)
        return session

    handle.install = install
    return handle


def _cfg(scope, **overrides):
    base = SimpleNamespace(NEO4J_URI="bolt://graph:7687", NEO4J_USER="u", NEO4J_PASSWORD="p",
                           NEO4J_DATABASE="neo4j")
    for key, value in overrides.items():
        setattr(base, key, value)
    return with_scope(base, scope)


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #

def test_the_signature_is_unchanged():
    # The positional contract is unchanged; the only additions are the graph reviewer's keyword-only timeout_s and
    # total_only. A scope is never a parameter: it rides on the config.
    params = inspect.signature(tool_neo4j_query).parameters
    assert list(params) == ["config", "cypher", "parameters", "timeout_s", "total_only"]
    assert params["parameters"].default is None
    assert params["timeout_s"].kind is inspect.Parameter.KEYWORD_ONLY and params["timeout_s"].default is None
    assert params["total_only"].kind is inspect.Parameter.KEYWORD_ONLY and params["total_only"].default is False


def test_the_refusal_texts():
    assert SCOPE_REFUSED == ("This graph query could not be confirmed to stay within your projects, "
                             "so it was not run.")
    assert NO_SCOPE_REFUSED == "No project scope is set for this request, so no graph query can run."


# --------------------------------------------------------------------------- #
# No scope refuses before a driver opens
# --------------------------------------------------------------------------- #

def _bare():
    return SimpleNamespace(NEO4J_URI="bolt://graph:7687", NEO4J_USER="u", NEO4J_PASSWORD="p")


def _none_scope():
    return with_scope(_bare(), None)


def _dict_scope():
    config = _bare()
    setattr(config, SCOPE_ATTR, {"is_admin": True, "project_ids": []})
    return config


@pytest.mark.parametrize("make_config", [_bare, MagicMock, _none_scope, _dict_scope],
                         ids=["no attribute", "MagicMock", "None", "a plain dict"])
def test_a_config_without_a_graph_scope_refuses_before_any_driver(fake, make_config):
    session = fake.install(_Session([{"n": 1}]))

    out = tool_neo4j_query(make_config(), TYPED, {"organ": "Lung"})

    assert fake.opened == [] and session.statements == []
    assert out["ok"] is False and out["data"] is None
    assert out["error"].startswith(NO_SCOPE_REFUSED)
    assert out["cypher"] == TYPED and out["submitted_cypher"] == TYPED
    assert out["parameters"] == {"organ": "Lung"}
    assert set(out["scope"]) >= SCOPE_KEYS
    assert out["scope"]["decision"] == "refused"
    assert out["scope"]["codes"] == ["no_scope"]
    assert out["scope"]["reasons"]
    assert is_scope_refusal(out) is True


# --------------------------------------------------------------------------- #
# A write is refused before the scope check, and is not a scope refusal
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("make_config", [_bare, lambda: _cfg(MEMBER), lambda: _cfg(ADMIN)],
                         ids=["no scope", "member", "admin"])
def test_a_write_is_refused_first_and_keeps_the_write_text(fake, make_config):
    fake.install(_Session([{"n": 1}]))
    text = "MATCH (s:T_TIS) DETACH DELETE s"

    out = tool_neo4j_query(make_config(), text, {})

    assert fake.opened == []
    assert out["ok"] is False
    assert out["error"].startswith("Write operations are not permitted")
    assert "DETACH DELETE" in out["error"]
    assert out["cypher"] == text and out["submitted_cypher"] == text
    assert out["scope"]["decision"] == "not_checked"
    assert out["scope"]["codes"] == ["write"]
    assert is_scope_refusal(out) is False


# --------------------------------------------------------------------------- #
# Admin runs the submitted text
# --------------------------------------------------------------------------- #

def test_admin_runs_exactly_the_submitted_text(fake):
    session = fake.install(_Session([{"n": 7}]))

    out = tool_neo4j_query(_cfg(ADMIN), TYPED, {"organ": "Lung"})

    assert out["ok"] is True and out["data"] == [{"n": 7}]
    assert session.statements == [(TYPED, {"organ": "Lung"})]
    assert out["cypher"] == TYPED and out["submitted_cypher"] == TYPED
    assert out["parameters"] == {"organ": "Lung"}
    assert out["scope"]["decision"] == "admin"
    assert out["scope"]["source"] == "test"
    assert "project_ids" not in out["scope"]
    assert is_scope_refusal(out) is False


def test_admin_is_refused_a_reserved_parameter(fake):
    fake.install(_Session([{"n": 7}]))

    out = tool_neo4j_query(_cfg(ADMIN), TYPED, {"organ": "Lung", SCOPE_PARAM: [1, 2, 3]})

    assert fake.opened == []
    assert out["ok"] is False and out["error"].startswith(SCOPE_REFUSED)
    assert out["scope"]["decision"] == "refused"
    assert out["scope"]["codes"] == ["reserved_parameter"]
    assert is_scope_refusal(out) is True


# --------------------------------------------------------------------------- #
# A caller who is not an admin runs the injected text with the scope parameter
# --------------------------------------------------------------------------- #

def test_a_member_runs_the_injected_text_with_the_scope_parameter(fake):
    session = fake.install(_Session([{"n": 2}]))
    expected = scope_cypher(TYPED, {"organ": "Lung"}, MEMBER)
    assert isinstance(expected, Scoped) and expected.cypher != TYPED

    out = tool_neo4j_query(_cfg(MEMBER), TYPED, {"organ": "Lung"})

    assert out["ok"] is True
    assert session.statements == [(expected.cypher, {"organ": "Lung", SCOPE_PARAM: [1, 3]})]
    assert out["cypher"] == expected.cypher
    assert out["submitted_cypher"] == TYPED
    assert out["parameters"] == {"organ": "Lung", SCOPE_PARAM: [1, 3]}
    assert out["scope"]["decision"] == "proven"
    assert out["scope"]["project_ids"] == [1, 3]
    assert out["scope"]["injected"] == list(expected.injected)
    assert out["scope"]["joined"] == list(expected.joined)
    assert out["scope"]["codes"] == [] and out["scope"]["reasons"] == []


def test_an_empty_project_set_binds_an_empty_list(fake):
    session = fake.install(_Session([{"n": 0}]))

    out = tool_neo4j_query(_cfg(GraphScope.for_projects([], source="test")), TYPED, {"organ": "Lung"})

    assert out["ok"] is True
    assert session.statements[0][1][SCOPE_PARAM] == []
    assert out["scope"]["project_ids"] == []


def test_the_total_probe_wraps_the_injected_text(fake):
    session = fake.install(_Session([{"uuid": f"TIS-{i}"} for i in range(3)], total=40))
    injected = scope_cypher(CAPPED, {}, MEMBER)

    out = tool_neo4j_query(_cfg(MEMBER), CAPPED, {})

    assert out["truncated"] is True and out["total"] == 40
    probe, probe_params = next((c, p) for c, p in session.statements if "__total" in c)
    assert probe.startswith("CALL () {")
    assert injected.cypher.rsplit("LIMIT", 1)[0].strip() in probe
    assert f"${SCOPE_PARAM}" in probe
    assert probe_params == {SCOPE_PARAM: [1, 3]}


def test_a_statement_the_prover_cannot_prove_is_refused_before_any_driver(fake):
    session = fake.install(_Session([{"title": "x"}]))

    out = tool_neo4j_query(_cfg(MEMBER), CATALOG_READ, {})

    assert fake.opened == [] and session.statements == []
    assert out["ok"] is False and out["data"] is None
    assert out["error"].startswith(SCOPE_REFUSED)
    assert out["cypher"] == CATALOG_READ and out["submitted_cypher"] == CATALOG_READ
    assert out["scope"]["decision"] == "refused"
    assert out["scope"]["codes"] == ["label_not_allowed"]
    assert out["scope"]["reasons"] and out["scope"]["reasons"][0] in out["error"]
    assert out["scope"]["project_ids"] == [1, 3]
    assert out["scope"]["source"] == "test"
    assert is_scope_refusal(out) is True


# --------------------------------------------------------------------------- #
# Hidden properties are stripped for a member only
# --------------------------------------------------------------------------- #

def _node_rows():
    node = _Node({"Sample", "T_SLD"}, {"uuid": "SLD-1", "parent_titles": ["TIS-9 foreign"],
                                       "parent_title_hashes": ["h"]})
    return [{"s": node}], node


def test_a_member_gets_whole_nodes_without_the_hidden_properties(fake):
    rows, _ = _node_rows()
    fake.install(_Session(rows))

    out = tool_neo4j_query(_cfg(MEMBER), "MATCH (s:T_SLD) RETURN s", {})

    assert out["ok"] is True
    assert out["data"] == [{"s": {"uuid": "SLD-1"}}]


def test_an_admin_gets_the_rows_untouched(fake):
    rows, node = _node_rows()
    fake.install(_Session(rows))

    out = tool_neo4j_query(_cfg(ADMIN), "MATCH (s:T_SLD) RETURN s", {})

    assert out["ok"] is True
    assert out["data"][0]["s"] is node


# --------------------------------------------------------------------------- #
# Every failure shape carries the fields
# --------------------------------------------------------------------------- #

def test_a_failing_query_carries_what_ran_and_the_scope(fake):
    fake.install(_Session([], fail_query=True))
    injected = scope_cypher(TYPED, {"organ": "Lung"}, MEMBER)

    out = tool_neo4j_query(_cfg(MEMBER), TYPED, {"organ": "Lung"})

    # A member is told the failure's codes only, never its message (test_neo4j_error_redaction.py).
    assert out["ok"] is False and out["error"] == RUNTIME_ERROR_WITHHELD.format(codes="RuntimeError")
    assert out["cypher"] == injected.cypher
    assert out["submitted_cypher"] == TYPED
    assert out["parameters"][SCOPE_PARAM] == [1, 3]
    assert out["scope"]["decision"] == "proven"
    assert is_scope_refusal(out) is False


def test_a_missing_password_is_checked_after_the_scope(fake):
    fake.install(_Session([]))

    no_scope = tool_neo4j_query(_cfg(None, NEO4J_PASSWORD=""), TYPED, {"organ": "Lung"})
    member = tool_neo4j_query(_cfg(MEMBER, NEO4J_PASSWORD=""), TYPED, {"organ": "Lung"})

    assert no_scope["error"].startswith(NO_SCOPE_REFUSED)
    assert member["error"] == "NEO4J_PASSWORD not configured"
    assert member["scope"]["decision"] == "proven"
    assert member["submitted_cypher"] == TYPED
    assert fake.opened == []


# --------------------------------------------------------------------------- #
# is_scope_refusal
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("result, expected", [
    ({"ok": False, "scope": {"decision": "refused", "codes": ["no_scope"]}}, True),
    ({"ok": False, "scope": {"decision": "refused", "codes": ["union"]}}, True),
    ({"ok": False, "scope": {"decision": "not_checked", "codes": ["write"]}}, False),
    ({"ok": True, "scope": {"decision": "proven"}}, False),
    ({"ok": True, "scope": {"decision": "admin"}}, False),
    ({"ok": False, "error": "boom"}, False),
    ({"ok": False, "scope": None}, False),
    ({"ok": False, "scope": "refused"}, False),
    (None, False),
    ("refused", False),
])
def test_is_scope_refusal(result, expected):
    assert is_scope_refusal(result) is expected


def test_the_module_exports_the_helper_beside_matched_nothing():
    assert callable(tool_module.is_scope_refusal) and callable(tool_module.matched_nothing)
