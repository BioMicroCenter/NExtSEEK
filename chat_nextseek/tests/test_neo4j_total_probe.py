"""
Regression lock for T1.11: a capped graph result must report its true total.

`chatter.py` set total_matches = graph_result["count"], and `count` is len(records).
So a query that hit its LIMIT reported the limit as if it were the answer. Raising the
cap from 250 to 5000 only moved the wall: graph.tissue_cell_impact's legitimate answer
is 10,688 (TIS + CEL samples in IMPACT), so it can never pass under any sane limit.

The fix is a probe rather than a bigger cap. Validated live during triage: on task 793's
query the probe returns exactly 10,688.
"""
from __future__ import annotations

import pytest

from chat_nextseek.helpers.tools.neo4j import split_trailing_limit

# task 793, verbatim from the run report's gplan.cypher.
CYPHER_793 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WHERE s.type IN $types AND (toLower(st.title) CONTAINS toLower($project) OR toLower(inv.title) CONTAINS toLower($project))
RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type
LIMIT 5000"""

# task 817, verbatim — a count query with no LIMIT.
CYPHER_817 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WHERE toLower(st.title) CONTAINS toLower($project)
   OR toLower(inv.title) CONTAINS toLower($project)
RETURN count(DISTINCT s) AS total"""


def test_the_real_capped_query_is_split_correctly():
    body, limit = split_trailing_limit(CYPHER_793, {"types": ["TIS", "CEL"], "project": "IMPACT"})

    assert limit == 5000
    assert body.rstrip().endswith("RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type")
    assert "LIMIT" not in body


def test_a_count_query_has_no_trailing_limit():
    assert split_trailing_limit(CYPHER_817, {"project": "GBM"}) == (None, None)


@pytest.mark.parametrize(
    "cypher, params, expected",
    [
        ("MATCH (n) RETURN n LIMIT 250", {}, 250),
        ("MATCH (n) RETURN n limit 5000", {}, 5000),
        ("MATCH (n) RETURN n LIMIT 100;", {}, 100),
        ("MATCH (n) RETURN n LIMIT 100  \n ", {}, 100),
        ("MATCH (n) RETURN n SKIP 10 LIMIT 50", {}, 50),
        ("MATCH (n) RETURN n LIMIT $cap", {"cap": 42}, 42),
        ("MATCH (n) RETURN n SKIP $off LIMIT $cap", {"off": 5, "cap": 42}, 42),
    ],
)
def test_limit_forms_are_recognised(cypher, params, expected):
    assert split_trailing_limit(cypher, params)[1] == expected


@pytest.mark.parametrize(
    "cypher, params",
    [
        ("MATCH (n) RETURN n", {}),
        ("", {}),
        (None, {}),
        # An unbound parameter cannot be compared against the row count.
        ("MATCH (n) RETURN n LIMIT $cap", {}),
        ("MATCH (n) RETURN n LIMIT $cap", {"cap": "lots"}),
        # A LIMIT that is not at the end is not the cap on the final result.
        ("MATCH (n) WITH n LIMIT 10 MATCH (n)-->(m) RETURN m", {}),
    ],
)
def test_non_trailing_or_unresolvable_limits_are_ignored(cypher, params):
    assert split_trailing_limit(cypher, params) == (None, None)


def test_the_probe_body_is_a_prefix_of_the_original():
    """No new write surface: the probe runs a prefix of an already write-checked query."""
    body, _ = split_trailing_limit(CYPHER_793, {})
    assert CYPHER_793.startswith(body)


# --------------------------------------------------------------------------- #
# The probe end to end, against a fake driver (no live Neo4j).
# --------------------------------------------------------------------------- #

import sys
import types as _types

from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return _types.SimpleNamespace(counters=None)


class _FakeSession:
    def __init__(self, rows, total, fail_probe=False):
        self._rows = rows
        self._total = total
        self._fail_probe = fail_probe
        self.queries: list[str] = []

    def run(self, cypher, params=None):
        self.queries.append(cypher)
        if "__total" in cypher:
            if self._fail_probe:
                raise RuntimeError("probe blew up")
            return _FakeResult([{"__total": self._total}])
        return _FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_fake_driver(monkeypatch, session):
    driver = _types.SimpleNamespace(
        session=lambda **k: session,
        close=lambda: None,
    )
    fake_neo4j = _types.ModuleType("neo4j")
    fake_neo4j.GraphDatabase = _types.SimpleNamespace(driver=lambda *a, **k: driver)
    monkeypatch.setitem(sys.modules, "neo4j", fake_neo4j)
    return session


def _cfg():
    return _types.SimpleNamespace(
        NEO4J_URI="bolt://x", NEO4J_USER="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="neo4j"
    )


def test_a_capped_result_reports_the_probed_total(monkeypatch):
    """The task 793 shape: 5,000 rows returned, 10,688 actually match."""
    session = _install_fake_driver(
        monkeypatch, _FakeSession([{"id": i} for i in range(5000)], total=10688)
    )

    out = tool_neo4j_query(_cfg(), CYPHER_793, {"types": ["TIS", "CEL"], "project": "IMPACT"})

    assert out["ok"] is True
    assert out["count"] == 5000, "count stays len(records) for back-compat"
    assert out["total"] == 10688
    assert out["truncated"] is True
    assert out["limit"] == 5000
    assert any("__total" in q for q in session.queries), "the probe never ran"


def test_an_uncapped_result_is_not_probed(monkeypatch):
    session = _install_fake_driver(
        monkeypatch, _FakeSession([{"id": i} for i in range(12)], total=999)
    )

    out = tool_neo4j_query(_cfg(), "MATCH (n) RETURN n LIMIT 5000", {})

    assert out["truncated"] is False
    assert out["total"] == 12
    assert not any("__total" in q for q in session.queries), "probed a result that fit"


def test_a_query_with_no_limit_is_not_probed(monkeypatch):
    session = _install_fake_driver(monkeypatch, _FakeSession([{"total": 0}], total=0))

    out = tool_neo4j_query(_cfg(), CYPHER_817, {"project": "GBM"})

    assert out["truncated"] is False
    assert out["limit"] is None
    assert not any("__total" in q for q in session.queries)


def test_a_failing_probe_still_reports_truncation(monkeypatch):
    """Best effort: an unknown total beats a capped count presented as complete."""
    _install_fake_driver(
        monkeypatch,
        _FakeSession([{"id": i} for i in range(5000)], total=10688, fail_probe=True),
    )

    out = tool_neo4j_query(_cfg(), CYPHER_793, {})

    assert out["ok"] is True, "a failed probe must not fail the query"
    assert out["truncated"] is True
    assert out["total"] is None


def test_the_probe_wraps_the_body_in_a_call_subquery(monkeypatch):
    """CALL () { } preserves DISTINCT and ORDER BY without parsing the projection."""
    session = _install_fake_driver(
        monkeypatch, _FakeSession([{"id": i} for i in range(5000)], total=10688)
    )

    tool_neo4j_query(_cfg(), CYPHER_793, {})

    probe = next(q for q in session.queries if "__total" in q)
    assert probe.startswith("CALL () {")
    assert "RETURN count(*) AS __total" in probe
    assert "LIMIT" not in probe
    assert "RETURN DISTINCT s.id AS id" in probe
