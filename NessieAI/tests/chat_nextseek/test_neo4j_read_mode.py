"""
D3: every statement tool_neo4j_query runs goes through a READ transaction.

`session.execute_read` makes the server refuse a write, whatever the text check
missed, and the transaction function carries a 60 s timeout (`neo4j.unit_of_work`),
so a runaway traversal is ended by the database. The query and its total probe each
get their own READ transaction; `session.run` (an auto-commit transaction in the
default WRITE access mode) is never called.

A fake driver records the access mode and the timeout of every transaction. The
real `neo4j.unit_of_work` decorates the transaction functions, so the timeout seen
here is the attribute the real driver reads.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import neo4j as _real_neo4j
import pytest

from chat_nextseek.helpers.tools import neo4j as tool_module
from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

RESULT_KEYS = {"ok", "data", "count", "total", "truncated", "limit", "cypher", "parameters", "counters"}
FAILURE_KEYS = {"ok", "error", "data", "cypher"}

CAPPED = "MATCH (s:T_TIS) RETURN DISTINCT s.id AS id LIMIT 3"


class _Result:
    def __init__(self, tx, rows):
        self._tx = tx
        self._rows = rows

    def _live(self):
        if self._tx.closed:
            raise AssertionError("result read after its transaction closed")

    def __iter__(self):
        self._live()
        return iter(self._rows)

    def single(self):
        self._live()
        return self._rows[0] if self._rows else None

    def consume(self):
        self._live()
        return SimpleNamespace(counters=SimpleNamespace(nodes_created=0, properties_set=0))


class _Tx:
    def __init__(self, session):
        self._session = session
        self.closed = False

    def run(self, cypher, parameters=None, **kwargs):
        self._session.statements.append((self._session.current_mode, cypher, parameters))
        if "__total" in cypher:
            if self._session.fail_probe:
                raise RuntimeError("probe blew up")
            return _Result(self, [{"__total": self._session.total}])
        if self._session.fail_query:
            raise RuntimeError("query blew up")
        return _Result(self, self._session.rows)


class _Session:
    def __init__(self, rows, total=None, fail_probe=False, fail_query=False):
        self.rows = rows
        self.total = total
        self.fail_probe = fail_probe
        self.fail_query = fail_query
        self.transactions: list[tuple[str, object]] = []   # (mode, timeout)
        self.statements: list[tuple[str, str, dict]] = []  # (mode, cypher, params)
        self.current_mode = None
        self.database = None

    def _managed(self, mode, fn, args, kwargs):
        self.transactions.append((mode, getattr(fn, "timeout", None)))
        self.current_mode = mode
        tx = _Tx(self)
        try:
            return fn(tx, *args, **kwargs)
        finally:
            tx.closed = True
            self.current_mode = None

    def execute_read(self, fn, *args, **kwargs):
        return self._managed("READ", fn, args, kwargs)

    def execute_write(self, fn, *args, **kwargs):
        return self._managed("WRITE", fn, args, kwargs)

    def run(self, *args, **kwargs):
        self.statements.append(("AUTOCOMMIT", args[0] if args else kwargs.get("query"), None))
        raise AssertionError("session.run called outside a transaction function")

    def begin_transaction(self, *args, **kwargs):
        raise AssertionError("an explicit transaction was opened")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Driver:
    def __init__(self, session):
        self._session = session
        self.closed = False

    def session(self, **kwargs):
        self._session.database = kwargs.get("database")
        return self._session

    def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    """Install a fake `neo4j` module; returns a handle with `.install(session)`."""
    handle = SimpleNamespace(opened=[], driver=None)

    def install(session):
        handle.driver = _Driver(session)

        def factory(*args, **kwargs):
            handle.opened.append((args, kwargs))
            return handle.driver

        module = types.ModuleType("neo4j")
        module.GraphDatabase = SimpleNamespace(driver=factory)
        module.unit_of_work = _real_neo4j.unit_of_work
        monkeypatch.setitem(sys.modules, "neo4j", module)
        return session

    handle.install = install
    return handle


def _cfg(**overrides):
    base = dict(NEO4J_URI="bolt://graph:7687", NEO4J_USER="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="neo4j")
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# READ transactions with a timeout
# --------------------------------------------------------------------------- #

def test_the_timeout_is_sixty_seconds():
    assert tool_module.QUERY_TIMEOUT_S == 60


def test_a_query_runs_in_one_read_transaction_with_the_timeout(fake):
    session = fake.install(_Session([{"n": 7}]))

    out = tool_neo4j_query(_cfg(), "MATCH (s:T_TIS) RETURN count(s) AS n", {})

    assert out["ok"] is True
    assert out["data"] == [{"n": 7}]
    assert session.transactions == [("READ", 60)]
    assert [s[0] for s in session.statements] == ["READ"]
    assert session.database == "neo4j"
    assert fake.driver.closed is True


def test_the_query_and_the_total_probe_each_run_read_with_the_timeout(fake):
    session = fake.install(_Session([{"id": i} for i in range(3)], total=10688))

    out = tool_neo4j_query(_cfg(), CAPPED, {"x": 1})

    assert out["total"] == 10688
    assert out["truncated"] is True
    assert session.transactions == [("READ", 60), ("READ", 60)]
    modes_and_kinds = [(mode, "__total" in cypher) for mode, cypher, _ in session.statements]
    assert modes_and_kinds == [("READ", False), ("READ", True)]
    assert all(params == {"x": 1} for _, _, params in session.statements)


def test_session_run_is_never_called(fake):
    """The fake's `run` raises; a direct call would surface as ok False."""
    session = fake.install(_Session([{"id": i} for i in range(3)], total=5))

    out = tool_neo4j_query(_cfg(), CAPPED, {})

    assert out["ok"] is True
    assert not any(mode == "AUTOCOMMIT" for mode, _, _ in session.statements)
    assert all(mode == "READ" for mode, _ in session.transactions)


def test_records_are_read_inside_the_transaction(fake):
    """A result is only valid inside its transaction function; the fake enforces it."""
    fake.install(_Session([{"id": 1}, {"id": 2}]))

    out = tool_neo4j_query(_cfg(), "MATCH (s:Sample) RETURN s.id AS id", {})

    assert out["ok"] is True
    assert out["data"] == [{"id": 1}, {"id": 2}]
    assert out["counters"] == {"nodes_created": 0, "properties_set": 0}


def test_a_failing_probe_keeps_the_rows(fake):
    session = fake.install(_Session([{"id": i} for i in range(3)], total=9, fail_probe=True))

    out = tool_neo4j_query(_cfg(), CAPPED, {})

    assert out["ok"] is True
    assert out["truncated"] is True
    assert out["total"] is None
    assert len(session.transactions) == 2


def test_a_failing_query_returns_the_failure_shape(fake):
    fake.install(_Session([], fail_query=True))

    out = tool_neo4j_query(_cfg(), "MATCH (s) RETURN s.id", {})

    assert set(out) == FAILURE_KEYS
    assert out["ok"] is False
    assert "query blew up" in out["error"]
    assert fake.driver.closed is True


# --------------------------------------------------------------------------- #
# Result shape unchanged
# --------------------------------------------------------------------------- #

def test_the_result_keys_are_unchanged(fake):
    fake.install(_Session([{"n": 1}]))

    out = tool_neo4j_query(_cfg(), "MATCH (s) RETURN count(s) AS n")

    assert set(out) == RESULT_KEYS
    assert out["cypher"] == "MATCH (s) RETURN count(s) AS n"
    assert out["parameters"] == {}
    assert out["count"] == 1
    assert out["limit"] is None


def test_the_capped_result_keys_are_unchanged(fake):
    fake.install(_Session([{"id": i} for i in range(3)], total=4))

    out = tool_neo4j_query(_cfg(), CAPPED, None)

    assert set(out) == RESULT_KEYS
    assert (out["count"], out["total"], out["truncated"], out["limit"]) == (3, 4, True, 3)


# --------------------------------------------------------------------------- #
# The write check runs first, on masked text
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "cypher, clause",
    [
        ("CREATE (n:Sample {id: 1})", "CREATE"),
        ("MATCH (s) DETACH DELETE s", "DETACH DELETE"),
        ("CALL db.labels()", "CALL db.labels"),
        ("CALL apoc.refactor.mergeNodes([]) YIELD node RETURN node", "CALL apoc.refactor.mergeNodes"),
        ("CALL () { MATCH (s) SET s.x = 1 RETURN s } RETURN count(*)", "SET"),
    ],
)
def test_a_refused_statement_never_opens_a_driver(fake, cypher, clause):
    fake.install(_Session([{"n": 1}]))

    out = tool_neo4j_query(_cfg(), cypher, {})

    assert fake.opened == []
    assert set(out) == FAILURE_KEYS
    assert out["ok"] is False
    assert out["data"] is None
    assert out["cypher"] == cypher
    assert out["error"].startswith("Write operations are not permitted")
    assert clause in out["error"]


def test_a_refused_statement_is_refused_without_a_password(fake):
    """The check does not depend on the driver or the configuration."""
    fake.install(_Session([]))

    out = tool_neo4j_query(_cfg(NEO4J_PASSWORD=None), "CREATE (n)", {})

    assert out["ok"] is False
    assert out["error"].startswith("Write operations are not permitted")


@pytest.mark.parametrize(
    "cypher",
    [
        "CALL db.index.fulltext.queryNodes('sample_search', $q) YIELD node RETURN node.id AS id",
        "MATCH (s:Sample) WHERE s.type = 'Data Set' RETURN s.id AS id",
        "MATCH (s:Sample) WHERE s.`SET_ID` IS NOT NULL RETURN s.id AS id",
        "MATCH (s:Sample) // DELETE later\nRETURN s.id AS id",
    ],
)
def test_reads_the_old_regex_refused_now_run(fake, cypher):
    session = fake.install(_Session([{"id": 1}]))

    out = tool_neo4j_query(_cfg(), cypher, {"q": "lung"})

    assert out["ok"] is True
    assert len(fake.opened) == 1
    assert session.transactions == [("READ", 60)]


def test_a_missing_password_still_refuses_before_connecting(fake):
    fake.install(_Session([]))

    out = tool_neo4j_query(_cfg(NEO4J_PASSWORD=""), "MATCH (s) RETURN s.id", {})

    assert out["ok"] is False
    assert out["error"] == "NEO4J_PASSWORD not configured"
    assert fake.opened == []
