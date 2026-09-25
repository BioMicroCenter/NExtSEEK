"""graph_review Tier 2: bounded count variants, and the live catalog Tier 1 reads in production.

Every Neo4j call is stubbed: through ``tool_neo4j_query`` (the module-level name graph_review_counts calls), through
``graph_catalog.get_snapshot``, or, for the tests that pin the tool's own timeout and total probe, through a fake
``neo4j`` module whose driver records every transaction. No driver, no network.
"""
from __future__ import annotations

import sys
import threading
import types
from types import SimpleNamespace

import neo4j as _real_neo4j
import pytest

from chat_nextseek import graph_catalog
from chat_nextseek import graph_review_counts as g2
from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_review import Check, DictCatalog, GraphReview, ReviewInput, review_tier1
from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope
from chat_nextseek.helpers.tools import neo4j as tool_module
from chat_nextseek.helpers.tools.neo4j import split_trailing_limit

MEMBER = GraphScope.for_projects([3, 1], source="test")
OTHER_MEMBER = GraphScope.for_projects([7], source="test")
ADMIN = GraphScope.admin("test")
TIFF = "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN s.id AS id"


def _cfg(scope=MEMBER, **overrides):
    base = dict(NEO4J_URI="bolt://graph:7687", NEO4J_USER="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="neo4j")
    base[SCOPE_ATTR] = scope
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fresh_cache():
    g2.reset_values_cache()
    yield
    g2.reset_values_cache()


# ------------------------------------------------------------------ the brief's four ------------------------------
def test_count_of_asks_the_tool_for_the_total_only(monkeypatch):
    # fix round 1: the brief's `LIMIT 1` + probe ran two statements per count; one total_only probe replaces it
    seen = {}
    def fake(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        seen.update(cypher=cypher, timeout_s=timeout_s, total_only=total_only)
        return {"ok": True, "count": None, "total": 8324, "truncated": False, "data": []}
    monkeypatch.setattr(g2, "tool_neo4j_query", fake)
    out = g2.count_of(object(), "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN s.id AS id LIMIT 5000", {"t": "tif"})
    assert out["ok"] and out["total"] == 8324
    assert seen["cypher"] == TIFF and seen["timeout_s"] == 5 and seen["total_only"] is True


def test_a_refused_variant_is_skipped_not_retried(monkeypatch):
    calls = []
    def fake(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        calls.append(cypher); return {"ok": False, "error": "This graph query could not be confirmed to stay within your projects"}
    monkeypatch.setattr(g2, "tool_neo4j_query", fake)
    assert g2.count_of(object(), "MATCH (s) RETURN s", {})["ok"] is False
    assert len(calls) == 1


def test_tier2_respects_the_budget(monkeypatch):
    import time
    monkeypatch.setattr(g2, "count_of", lambda *a, **k: (time.sleep(0.2), {"ok": True, "total": 1, "elapsed_ms": 200})[1])
    inp = ReviewInput("q", "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN s.id AS id", {"t": "tiff"},
                      {}, [], 1306, 1306, True, None)
    rv = GraphReview("suggest", [Check("stem_miss", True, "tif")], "d", None, [], 0)
    out = g2.run_tier2(object(), inp, rv, budget_s=0.1, max_variants=2)
    assert len(out.variants) <= 1


def test_stem_miss_variant_swaps_the_parameter():
    inp = ReviewInput("q", "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN s.id AS id", {"t": "tiff"},
                      {}, [], 1306, 1306, True, None)
    rv = GraphReview("suggest", [Check("stem_miss", True, "misses ['tif', 'TIF']")], "d", None, [], 0)
    edits = g2.relaxed_variants(inp, rv)
    assert any(p.get("t") == "tif" for _e, _c, p in edits)


# ------------------------------------------------------------------ the tool's timeout_s ---------------------------
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
    """Records (mode, timeout) per transaction and every statement; the probe answers ``total``."""

    def __init__(self, rows, total=None):
        self.rows, self.total = rows, total
        self.transactions, self.statements = [], []

    def execute_read(self, fn, *args, **kwargs):
        self.transactions.append(("READ", getattr(fn, "timeout", None)))
        session = self

        class _Tx:
            def run(self, cypher, parameters=None, **_kw):
                session.statements.append(cypher)
                return _Result([{"__total": session.total}] if "__total" in cypher else session.rows)
        return fn(_Tx(), *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_driver(monkeypatch):
    def install(session):
        driver = SimpleNamespace(session=lambda **kw: session, close=lambda: None)
        module = types.ModuleType("neo4j")
        module.GraphDatabase = SimpleNamespace(driver=lambda *a, **k: driver)
        module.unit_of_work = _real_neo4j.unit_of_work
        monkeypatch.setitem(sys.modules, "neo4j", module)
        return session
    return install


def test_the_tool_default_timeout_is_unchanged(fake_driver):
    session = fake_driver(_Session([{"id": 1}], total=9))
    out = tool_module.tool_neo4j_query(_cfg(ADMIN), "MATCH (s:T_TIS) RETURN s.id AS id LIMIT 1", {})
    assert out["ok"] and out["total"] == 9
    assert session.transactions == [("READ", 60), ("READ", 60)]


def test_timeout_s_bounds_the_query_and_its_total_probe(fake_driver):
    session = fake_driver(_Session([{"id": 1}], total=9))
    out = tool_module.tool_neo4j_query(_cfg(ADMIN), "MATCH (s:T_TIS) RETURN s.id AS id LIMIT 1", {}, timeout_s=3)
    assert out["ok"] and out["total"] == 9
    assert session.transactions == [("READ", 3), ("READ", 3)]


def test_timeout_s_is_keyword_only_with_no_default_change():
    import inspect
    params = inspect.signature(tool_module.tool_neo4j_query).parameters
    assert list(params)[:3] == ["config", "cypher", "parameters"]
    assert params["timeout_s"].kind is inspect.Parameter.KEYWORD_ONLY and params["timeout_s"].default is None
    assert params["total_only"].kind is inspect.Parameter.KEYWORD_ONLY and params["total_only"].default is False


def test_total_only_is_one_transaction_under_the_timeout(fake_driver):
    session = fake_driver(_Session([{"id": 1}], total=9))
    out = tool_module.tool_neo4j_query(_cfg(ADMIN), "MATCH (s:T_TIS) RETURN s.id AS id LIMIT 5", {}, timeout_s=4,
                                       total_only=True)
    assert session.transactions == [("READ", 4)]
    assert session.statements == ["CALL () {\nMATCH (s:T_TIS) RETURN s.id AS id\n}\nRETURN count(*) AS __total"]
    assert out["ok"] is True and out["total"] == 9 and out["count"] is None
    assert out["data"] == [] and out["truncated"] is False
    assert out["cypher"] == "MATCH (s:T_TIS) RETURN s.id AS id LIMIT 5"


def test_total_only_without_a_trailing_limit_counts_the_whole_statement(fake_driver):
    session = fake_driver(_Session([], total=0))
    out = tool_module.tool_neo4j_query(_cfg(ADMIN), "MATCH (s:T_TIS) RETURN s.id AS id", {}, total_only=True)
    assert session.transactions == [("READ", 60)]
    assert session.statements == ["CALL () {\nMATCH (s:T_TIS) RETURN s.id AS id\n}\nRETURN count(*) AS __total"]
    assert out["ok"] is True and out["total"] == 0


def test_total_only_still_refuses_a_write_and_scopes_a_member(fake_driver):
    session = fake_driver(_Session([], total=3))
    refused = tool_module.tool_neo4j_query(_cfg(ADMIN), "MATCH (s:T_TIS) DETACH DELETE s", {}, total_only=True)
    assert refused["ok"] is False and refused["error"].startswith("Write operations are not permitted")
    unscoped = tool_module.tool_neo4j_query(_cfg(MEMBER), "MATCH (a:Attribute) RETURN a.title AS t", {},
                                            total_only=True)
    assert unscoped["ok"] is False and unscoped["scope"]["decision"] == "refused"
    assert tool_module.tool_neo4j_query(_cfg(None), TIFF, {"t": "x"}, total_only=True)["ok"] is False
    assert session.transactions == []                                    # none of the three reached the database
    out = tool_module.tool_neo4j_query(_cfg(MEMBER), TIFF + " LIMIT 50", {"t": "x"}, timeout_s=2, total_only=True)
    assert out["ok"] is True and out["total"] == 3 and out["scope"]["decision"] == "proven"
    (probe,) = session.statements
    assert probe.startswith("CALL () {") and "__scope_projects" in probe and "LIMIT 50" not in probe
    assert out["parameters"]["__scope_projects"] == [1, 3] and session.transactions == [("READ", 2)]


# ------------------------------------------------------------------ count_of ---------------------------------------
def _recording(monkeypatch, result):
    calls = []

    def fake(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        calls.append(SimpleNamespace(config=config, cypher=cypher, parameters=parameters, timeout_s=timeout_s,
                                     total_only=total_only))
        if isinstance(result, Exception):
            raise result
        return result(cypher) if callable(result) else result
    monkeypatch.setattr(g2, "tool_neo4j_query", fake)
    return calls


def test_count_of_through_the_real_tool_reads_the_probe_scoped_and_bounded(fake_driver):
    session = fake_driver(_Session([{"id": 1}], total=8324))
    out = g2.count_of(_cfg(MEMBER), TIFF + " ORDER BY id LIMIT 5000", {"t": "tif"}, timeout_s=4)
    assert out == {"ok": True, "total": 8324, "error": None, "elapsed_ms": out["elapsed_ms"]}
    (probe,) = session.statements                                        # one statement: the total, nothing else
    assert probe.startswith("CALL () {") and "__scope_projects" in probe  # scoped by the prover
    assert "ORDER BY" not in probe and "LIMIT" not in probe             # a trailing sort buys a count nothing
    assert session.transactions == [("READ", 4)]


def test_count_of_a_zero_result_through_the_real_tool_is_zero(fake_driver):
    session = fake_driver(_Session([], total=0))
    out = g2.count_of(_cfg(MEMBER), TIFF, {"t": "nothing"})
    assert out["ok"] is True and out["total"] == 0 and session.transactions == [("READ", 5)]


def test_count_of_zero_rows_is_a_zero(monkeypatch):
    _recording(monkeypatch, {"ok": True, "count": None, "total": 0, "data": []})
    assert g2.count_of(object(), TIFF, {"t": "x"}) == {"ok": True, "total": 0, "error": None,
                                                       "elapsed_ms": pytest.approx(0, abs=1000)}


def test_count_of_an_unknown_total_is_not_a_count(monkeypatch):
    # the tool says total None: never report a count it did not make
    _recording(monkeypatch, {"ok": True, "count": None, "total": None, "truncated": False})
    out = g2.count_of(object(), TIFF, {"t": "x"})
    assert out["ok"] is False and out["total"] is None and out["error"]


def test_count_of_never_raises(monkeypatch):
    calls = _recording(monkeypatch, RuntimeError("driver exploded"))
    out = g2.count_of(object(), TIFF, {"t": "x"})
    assert out["ok"] is False and "driver exploded" in out["error"] and len(calls) == 1
    assert g2.count_of(object(), None, {})["ok"] is False


def test_count_of_strips_an_unbound_limit_parameter_and_skip(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "count": None, "total": 12})
    assert g2.count_of(object(), TIFF + " SKIP 10 LIMIT $n;", {"t": "x"})["total"] == 12
    assert calls[0].cypher == TIFF and calls[0].total_only is True


# ------------------------------------------------------------------ live_values: the provider (R1, R2, R4, R6) -----
VALUES_ROWS = [{"v": "tif", "n": 7000}, {"v": "TIF", "n": 1300}, {"v": "tiff", "n": 1306}, {"v": None, "n": 4}]


def test_live_values_returns_a_fresh_provider_with_all_three_methods():
    a, b = g2.live_values(_cfg()), g2.live_values(_cfg())
    assert a is not b
    for provider in (a, b):
        for method in ("values", "attributes", "type_name"):
            assert callable(getattr(provider, method))


def test_values_is_one_distinct_query_through_the_tool_with_a_3s_timeout(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": VALUES_ROWS, "count": 4, "total": 4})
    got = g2.live_values(_cfg()).values("T_D_IMG", "DataType")
    assert got == [("tif", 7000), ("tiff", 1306), ("TIF", 1300)]      # most frequent first, a null skipped
    assert len(calls) == 1 and calls[0].timeout_s == 3 and calls[0].total_only is False
    assert calls[0].cypher == g2.values_statement("T_D_IMG", "DataType")
    assert "s.DataType IS NOT NULL" in calls[0].cypher and "LIMIT 50" in calls[0].cypher


def test_the_values_statement_has_no_trailing_limit_so_the_tool_never_probes_it():
    statement = g2.values_statement("T_D_IMG", "DataType")
    assert split_trailing_limit(statement, {}) == (None, None)


def test_the_values_statement_is_proven_and_scoped_for_a_member():
    outcome = scope_cypher(g2.values_statement("T_D_IMG", "DataType"), {}, MEMBER)
    assert isinstance(outcome, Scoped), getattr(outcome, "reasons", None)
    assert outcome.decision == "proven" and outcome.injected
    assert "__scope_projects" in outcome.cypher and outcome.parameters["__scope_projects"] == [1, 3]


def test_values_through_the_real_tool_runs_one_transaction_no_probe(fake_driver):
    rows = [{"v": f"v{i}", "n": 100 - i} for i in range(50)]           # a full page of 50 distinct values
    session = fake_driver(_Session(rows, total=999))
    got = g2.live_values(_cfg(MEMBER)).values("T_D_IMG", "DataType")
    assert len(got) == 50
    assert session.transactions == [("READ", 3)]                         # no second scan for a total
    assert "__scope_projects" in session.statements[0]


def test_a_cached_value_list_is_served_to_the_same_scope_without_a_query(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": VALUES_ROWS, "count": 4})
    first = g2.live_values(_cfg(GraphScope.for_projects([1, 3]))).values("T_D_IMG", "DataType")
    again = g2.live_values(_cfg(GraphScope.for_projects([3, 1]))).values("T_D_IMG", "DataType")
    assert first == again and len(calls) == 1
    again.append(("mutated", 1))                                          # a caller's list is its own copy
    assert ("mutated", 1) not in g2.live_values(_cfg()).values("T_D_IMG", "DataType")


def test_another_scope_never_gets_a_cached_entry(monkeypatch):
    calls = _recording(monkeypatch, lambda cy: {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    for scope in (MEMBER, OTHER_MEMBER, ADMIN, GraphScope.for_projects([], source="test")):
        g2.live_values(_cfg(scope)).values("T_D_IMG", "DataType")
    assert len(calls) == 4
    assert [scope_of_call.config for scope_of_call in calls][2].GRAPH_SCOPE.is_admin


def test_the_cache_key_names_the_graph(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    g2.live_values(_cfg(NEO4J_DATABASE="neo4j")).values("T_D_IMG", "DataType")
    g2.live_values(_cfg(NEO4J_DATABASE="other")).values("T_D_IMG", "DataType")
    g2.live_values(_cfg(NEO4J_URI="bolt://elsewhere:7687")).values("T_D_IMG", "DataType")
    assert len(calls) == 3


def test_no_scope_returns_none_without_a_query(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": VALUES_ROWS, "count": 4})
    assert g2.live_values(_cfg(None)).values("T_D_IMG", "DataType") is None
    assert g2.live_values(SimpleNamespace(NEO4J_URI="bolt://g", GRAPH_SCOPE={"is_admin": True})).values(
        "T_D_IMG", "DataType") is None
    assert calls == []


@pytest.mark.parametrize("label, attr", [
    ("T_d_img", "DataType"), ("Sample", "DataType"), ("T_", "DataType"), ("T_X\n", "DataType"),
    ("T_X) DETACH DELETE s //", "DataType"), ("T_X:Sample", "DataType"), (None, "DataType"),
    ("T_D_IMG", "Data Type"), ("T_D_IMG", "1st"), ("T_D_IMG", "a`b"), ("T_D_IMG", "x\n"),
    ("T_D_IMG", "x) RETURN 1 //"), ("T_D_IMG", ""), ("T_D_IMG", None),
])
def test_an_invalid_label_or_attribute_issues_no_query(monkeypatch, label, attr):
    calls = _recording(monkeypatch, {"ok": True, "data": VALUES_ROWS, "count": 4})
    assert g2.live_values(_cfg()).values(label, attr) is None
    assert calls == []


def test_at_most_max_cold_uncached_queries_per_provider(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    warm = g2.live_values(_cfg())
    assert warm.values("T_A", "Warm") == [("x", 1)]
    provider = g2.live_values(_cfg())                                     # default max_cold=2
    assert provider.values("T_A", "One") == [("x", 1)]
    assert provider.values("T_A", "Two") == [("x", 1)]
    assert provider.values("T_A", "Three") is None                        # the third cold key: no query
    assert provider.values("T_A", "Warm") == [("x", 1)]                   # a cache hit is always served
    assert provider.values("T_A", "One") == [("x", 1)]
    assert len(calls) == 3
    assert g2.live_values(_cfg()).values("T_A", "Three") == [("x", 1)]    # a new turn gets its own two


def test_max_cold_zero_is_cache_only(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    g2.live_values(_cfg()).values("T_A", "Warm")
    provider = g2.live_values(_cfg(), max_cold=0)
    assert provider.values("T_A", "Warm") == [("x", 1)]
    assert provider.values("T_A", "Cold") is None
    assert len(calls) == 1


def test_an_invalid_key_or_missing_scope_does_not_spend_a_cold_fetch(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    provider = g2.live_values(_cfg(), max_cold=1)
    assert provider.values("nope", "DataType") is None
    assert provider.values("T_A", "bad attr") is None
    assert provider.values("T_A", "Good") == [("x", 1)]
    assert len(calls) == 1


# ------------------------------------------------------------------ live_values: the cache (R3) --------------------
def test_an_entry_expires_after_the_detail_ttl(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(g2, "_now", lambda: clock[0])
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    assert g2.VALUES_TTL_S == graph_catalog.DETAIL_TTL_S == 600
    g2.live_values(_cfg()).values("T_A", "Attr")
    clock[0] += 599
    g2.live_values(_cfg()).values("T_A", "Attr")
    assert len(calls) == 1
    clock[0] += 1
    g2.live_values(_cfg()).values("T_A", "Attr")
    assert len(calls) == 2


def test_a_failed_or_refused_query_is_not_cached(monkeypatch):
    calls = _recording(monkeypatch, {"ok": False, "error": "refused"})
    assert g2.live_values(_cfg()).values("T_A", "Attr") is None
    assert g2.live_values(_cfg()).values("T_A", "Attr") is None
    assert len(calls) == 2
    calls = _recording(monkeypatch, RuntimeError("down"))
    assert g2.live_values(_cfg()).values("T_A", "Attr") is None
    assert g2.live_values(_cfg()).values("T_A", "Attr") is None
    assert len(calls) == 2
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    assert g2.live_values(_cfg()).values("T_A", "Attr") == [("x", 1)]
    assert g2.live_values(_cfg()).values("T_A", "Attr") == [("x", 1)]
    assert len(calls) == 1


def test_the_cache_holds_512_entries_least_recently_used_out(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    assert g2.VALUES_CACHE_MAX == 512
    provider = g2.live_values(_cfg(), max_cold=10_000)
    for i in range(512):
        provider.values("T_A", f"A{i}")
    provider.values("T_A", "A0")                                          # touch the oldest: now the newest
    provider.values("T_A", "A512")                                        # evicts A1, not A0
    assert len(calls) == 513
    assert g2.live_values(_cfg(), max_cold=0).values("T_A", "A0") == [("x", 1)]
    assert g2.live_values(_cfg(), max_cold=0).values("T_A", "A1") is None
    assert g2.cache_size() == 512


def test_reset_values_cache_forgets_everything(monkeypatch):
    calls = _recording(monkeypatch, {"ok": True, "data": [{"v": "x", "n": 1}], "count": 1})
    g2.live_values(_cfg()).values("T_A", "Attr")
    g2.reset_values_cache()
    assert g2.cache_size() == 0
    g2.live_values(_cfg()).values("T_A", "Attr")
    assert len(calls) == 2


def test_the_cache_is_thread_safe(monkeypatch):
    _recording(monkeypatch, lambda cy: {"ok": True, "data": [{"v": cy[-40:], "n": 1}], "count": 1})
    monkeypatch.setattr(g2, "VALUES_CACHE_MAX", 64)
    errors = []

    def work(t):
        try:
            provider = g2.live_values(_cfg(), max_cold=10_000)
            for i in range(200):
                got = provider.values("T_A", f"A{(t * 37 + i) % 150}")
                assert got and got[0][1] == 1
        except Exception as exc:                                          # pragma: no cover - the failure itself
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and g2.cache_size() <= 64


# ------------------------------------------------------------------ live_values: attributes and type_name (R5) -----
def _snapshot():
    row = graph_catalog.TypeIndexRow(title="D.IMG", label="T_D_IMG", name="Imaging Data", clade=None,
                                     sample_count=None, deprecated=False, attributes_with_values=2)
    return graph_catalog.CatalogSnapshot(catalog_hash="h", synced_at=None, has_usage=False, index=(row,),
                                         guard={"T_D_IMG": frozenset({"Instrument", "DataType"}),
                                                "T_EMPTY": frozenset()})


def test_attributes_and_type_name_come_from_the_snapshot_with_no_query(monkeypatch):
    calls = _recording(monkeypatch, AssertionError("no query of its own"))
    seen = []
    monkeypatch.setattr(graph_catalog, "get_snapshot", lambda config: (seen.append(config), _snapshot())[1])
    config = _cfg()
    provider = g2.live_values(config)
    assert provider.attributes("T_D_IMG") == ["DataType", "Instrument"]
    assert provider.attributes("T_EMPTY") == []
    assert provider.type_name("T_D_IMG") == "Imaging Data"
    assert provider.attributes("T_NOPE") is None and provider.type_name("T_NOPE") is None
    assert calls == [] and seen and all(c is config for c in seen)


@pytest.mark.parametrize("error", [graph_catalog.CatalogUnavailable("no GraphMeta"), RuntimeError("boom")])
def test_an_unavailable_catalog_is_none(monkeypatch, error):
    def fail(config):
        raise error
    monkeypatch.setattr(graph_catalog, "get_snapshot", fail)
    provider = g2.live_values(_cfg())
    assert provider.attributes("T_D_IMG") is None and provider.type_name("T_D_IMG") is None


def test_tier1_runs_on_the_live_provider(monkeypatch):
    # the real review_tier1 over the live provider: a stem miss from one DISTINCT query and the snapshot
    calls = _recording(monkeypatch, {"ok": True, "data": VALUES_ROWS, "count": 4})
    monkeypatch.setattr(graph_catalog, "get_snapshot", lambda config: _snapshot())
    inp = ReviewInput("how many TIFF images are there", TIFF, {"t": "tiff"}, {}, [{"id": 1}], 1, 1306, True, None)
    review = review_tier1(inp, g2.live_values(_cfg()))
    assert review.verdict == "suggest"
    assert next(c for c in review.checks if c.name == "stem_miss").fired
    assert all(c.timeout_s == 3 for c in calls) and len(calls) <= 2


# ------------------------------------------------------------------ relaxed_variants ------------------------------
def _review(*checks, suggestion=None):
    return GraphReview("suggest", [Check(n, True, d) for n, d in checks], "d", suggestion, [], 0)


def _inp(cypher, params=None, rows=None, count=None, total=None, question="q", elapsed_ms=None):
    rows = rows if rows is not None else []
    return ReviewInput(question, cypher, params or {}, {}, rows, count if count is not None else len(rows),
                       total, True, None, elapsed_ms=elapsed_ms)


def test_stem_miss_from_the_real_tier1_detail():
    catalog = DictCatalog({"T_D_IMG.DataType": [["tif", 7000], ["TIF", 1300], ["tiff", 1306]]})
    inp = _inp(TIFF + " LIMIT 5000", {"t": "tiff"}, rows=[{"id": 1}], total=1306)
    review = review_tier1(inp, catalog)
    edit, cypher, params = g2.relaxed_variants(inp, review)[0]
    assert edit.startswith("stem_miss") and params == {"t": "tif"} and cypher == TIFF + " LIMIT 5000"
    assert inp.parameters == {"t": "tiff"}                                # the turn's own parameters untouched


def test_stem_miss_swaps_a_quoted_literal():
    cy = "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS 'tiff' RETURN s.id AS id"
    edits = g2.relaxed_variants(_inp(cy), _review(("stem_miss", "DataType CONTAINS 'tiff' misses ['tif', 'TIF']")))
    assert edits[0][1] == "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS 'tif' RETURN s.id AS id"


def test_stem_miss_on_a_count_only_query_counts_rows_not_the_one_aggregate_row():
    cy = "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN count(DISTINCT s) AS n"
    _e, cypher, params = g2.relaxed_variants(_inp(cy, {"t": "tiff"}), _review(("stem_miss", "misses ['tif']")))[0]
    assert params == {"t": "tif"}
    assert cypher.endswith("WITH DISTINCT s AS k WHERE k IS NOT NULL\nRETURN 1 AS n")
    assert "count(" not in cypher


SAMPLE_COUNT_DETAIL = "sample_count > 0 on an all/different question"
ZERO_DETAIL = "zero behind a fuzzy anchor and another filter; base never counted"


def test_all_question_narrowed_drops_sample_count():
    cy = ("MATCH (st:SampleType) WHERE st.title STARTS WITH 'D.' AND st.sample_count > 0 "
          "RETURN count(st) AS n")
    edits = g2.relaxed_variants(_inp(cy), _review(("all_question_narrowed", SAMPLE_COUNT_DETAIL)))
    assert len(edits) == 1
    assert edits[0][1] == ("MATCH (st:SampleType)\nWHERE st.title STARTS WITH 'D.'\n"
                           "WITH st AS k WHERE k IS NOT NULL\nRETURN 1 AS n")


def test_all_question_narrowed_drops_a_sole_is_not_null_and_its_where():
    cy = "MATCH (s:T_D_FILE) WHERE s.Format IS NOT NULL RETURN DISTINCT s.type AS type ORDER BY type"
    edits = g2.relaxed_variants(_inp(cy), _review(("all_question_narrowed",
                                                   "s.Format IS NOT NULL on an all/different question")))
    assert edits[0][1] == "MATCH (s:T_D_FILE)\nRETURN DISTINCT s.type AS type ORDER BY type"


def test_a_where_with_a_top_level_or_is_not_edited():
    cy = "MATCH (st:SampleType) WHERE st.sample_count > 0 OR st.title = 'x' RETURN st.title AS t"
    assert g2.relaxed_variants(_inp(cy), _review(("all_question_narrowed", SAMPLE_COUNT_DETAIL))) == []


R3_601 = ("MATCH (nhp:T_NHP) WHERE toLower(nhp.search_text) CONTAINS toLower($uid) AND EXISTS { MATCH "
          "(dflow:T_D_FLOW)-[:DERIVED_FROM*1..12]->(nhp) } RETURN count(DISTINCT nhp) AS n")


def _zero_edits(cy, params=None):
    return [c for e, c, _p in g2.relaxed_variants(_inp(cy, params or {}, rows=[{"n": 0}], count=1),
                                                  _review(("zero_unproven_base", ZERO_DETAIL)))
            if e.startswith("zero_unproven_base")]


def test_zero_unproven_base_keeps_the_anchors_own_text_match_and_drops_the_rest():
    # R3 601: NHP by search_text CONTAINS a UID, then a lineage filter; the base (the text match alone) was
    # never counted
    (variant,) = _zero_edits(R3_601, {"uid": "MDL-1"})
    assert variant == ("MATCH (nhp:T_NHP)\nWHERE toLower(nhp.search_text) CONTAINS toLower($uid)\n"
                       "WITH DISTINCT nhp AS k\nRETURN 1 AS n")
    assert "toLower(nhp.search_text) CONTAINS toLower($uid)" in variant
    assert "EXISTS" not in variant and "DERIVED_FROM" not in variant and "T_D_FLOW" not in variant


def test_zero_unproven_base_keeps_a_multi_node_anchor_pattern():
    cy = ("MATCH (s:T_PAT)-[:IN_STUDY]->(st:Study)\nWHERE (toLower(st.title) CONTAINS toLower($project)\n"
          "   OR EXISTS { MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation) WHERE toLower(inv.title) CONTAINS "
          "toLower($project) })\n  AND toLower(toString(s.Scientist)) CONTAINS toLower($scientist)\n"
          "RETURN count(DISTINCT s) AS n")
    assert _zero_edits(cy, {"project": "x", "scientist": "y"}) == [
        "MATCH (s:T_PAT)-[:IN_STUDY]->(st:Study)\nWHERE toLower(toString(s.Scientist)) CONTAINS toLower($scientist)\n"
        "WITH DISTINCT s AS k\nRETURN 1 AS n"]


@pytest.mark.parametrize("cypher, kept", [
    ("MATCH (s:T_X) WHERE s.title STARTS WITH $p AND s.Sex = 'F' RETURN count(s) AS n", "s.title STARTS WITH $p"),
    ("MATCH (s:T_X) WHERE s.uuid =~ ('(?i)^[^-]+-[0-9]{6}' + $lab + '-.*') AND s.Sex = 'F' RETURN count(s) AS n",
     "s.uuid =~ ('(?i)^[^-]+-[0-9]{6}' + $lab + '-.*')"),
    ("MATCH (s:T_X) WHERE toLower(s.search_text) CONTAINS 'mdl' AND s.Sex = 'F' RETURN s.id AS id",
     "toLower(s.search_text) CONTAINS 'mdl'"),
    ("MATCH (s:T_X) WHERE s.search_text CONTAINS $u MATCH (s)-[:DERIVED_FROM]->(p:T_Y) RETURN count(s) AS n",
     "s.search_text CONTAINS $u"),
])
def test_zero_unproven_base_recognises_each_text_match(cypher, kept):
    (variant,) = _zero_edits(cypher, {"p": "a", "lab": "ENG", "u": "x"})
    assert variant == f"MATCH (s:T_X)\nWHERE {kept}\nWITH DISTINCT s AS k\nRETURN 1 AS n"
    assert isinstance(scope_cypher(variant, {"p": "a", "lab": "ENG", "u": "x"}, MEMBER), Scoped)


@pytest.mark.parametrize("cypher", [
    # a top-level OR: the anchor is not a conjunct
    "MATCH (s:T_X) WHERE toLower(s.search_text) CONTAINS $u OR s.Sex = 'F' RETURN count(s) AS n",
    # AND binds tighter than OR: (text match AND sex) OR age, so the text match is not a conjunct of the whole
    "MATCH (s:T_X) WHERE s.title CONTAINS $u AND s.Sex = 'F' OR s.Age > 3 RETURN count(s) AS n",
    # NOT written like a function call still negates the whole comparison
    "MATCH (s:T_X) WHERE NOT(s.title) CONTAINS $u AND s.Sex = 'F' RETURN count(s) AS n",
    # the right-hand side reads another variable, even inside a function call
    "MATCH (s:T_X)-[:IN_STUDY]->(o:Study) WHERE s.title CONTAINS toString(o) AND s.Sex = 'F' RETURN count(s) AS n",
    # no text match on the anchor variable at all
    "MATCH (s:T_X) WHERE s.Sex = 'F' AND EXISTS { MATCH (s)-[:DERIVED_FROM]->(:T_Y) } RETURN count(s) AS n",
    # the text match is on another variable, not on the counted one
    "MATCH (s:T_PAT)-[:IN_STUDY]->(st:Study) WHERE toLower(st.title) CONTAINS $u AND s.Sex = 'F' "
    "RETURN count(DISTINCT s) AS n",
    # the text match sits inside a list predicate
    "MATCH (s:T_X) WHERE any(v IN [s.a, s.b] WHERE toLower(v) CONTAINS $u) AND s.Sex = 'F' RETURN count(s) AS n",
    # a negated text match is not an anchor
    "MATCH (s:T_X) WHERE NOT s.title CONTAINS $u AND s.Sex = 'F' RETURN count(s) AS n",
    # the right-hand side reads another node
    "MATCH (s:T_X), (o:T_Y) WHERE s.title CONTAINS o.title AND s.Sex = 'F' RETURN count(s) AS n",
    # the anchor's text match inside an OR group
    "MATCH (s:T_X) WHERE (s.title CONTAINS $u OR s.name CONTAINS $u) AND s.Sex = 'F' RETURN count(s) AS n",
    # nothing to drop: the text match is the whole query
    "MATCH (s:T_X) WHERE s.title CONTAINS $u AND s.name CONTAINS $v RETURN count(s) AS n",
    # no WHERE on the anchor MATCH
    "MATCH (s:T_X) MATCH (s)-[:DERIVED_FROM]->(p:T_Y) WHERE p.title CONTAINS $u RETURN count(s) AS n",
])
def test_an_unisolatable_anchor_gives_no_zero_variant(cypher):
    assert _zero_edits(cypher, {"u": "x", "v": "y"}) == []


def test_unapplied_value_groups_the_matched_set_by_the_named_attribute():
    cy = ("MATCH (s:T_D_SEQ)-[:DERIVED_FROM*1..6]->(p:T_PAT) WHERE p.Project = $p "
          "RETURN count(DISTINCT p) AS patients")
    detail = "question names T_D_SEQ.DataType='RNA-Seq', Cypher never applies it"
    edits = g2.relaxed_variants(_inp(cy, {"p": "TCGA"}), _review(("unapplied_value", detail)))
    assert edits[0][1] == ("MATCH (s:T_D_SEQ)-[:DERIVED_FROM*1..6]->(p:T_PAT) WHERE p.Project = $p\n"
                           "WITH s WHERE s.DataType IS NOT NULL\nRETURN DISTINCT s.DataType AS value")


R6_1225 = ("MATCH (s:T_PAT)\nWHERE EXISTS {\n  MATCH (s)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(inv:Investigation)\n"
           "  WHERE inv.title = $investigation\n}\nAND EXISTS {\n"
           "  MATCH (aln:T_A_ALN)-[:DERIVED_FROM*1..12]->(r:T_RNA)-[:DERIVED_FROM*1..12]->(s)\n}\nRETURN count(s) AS n")
ALN_DETAIL = "question names T_A_ALN.DataType='RNA-Seq', Cypher never applies it"
SEQ_DETAIL = "question names T_D_SEQ.DataType='RNA-Seq', Cypher never applies it"


def _breakdowns(cy, params=None, detail=ALN_DETAIL):
    return [(c, p) for e, c, p in g2.relaxed_variants(_inp(cy, params or {}), _review(("unapplied_value", detail)))
            if e.startswith("unapplied_value")]


def test_a_variable_bound_only_inside_exists_gets_no_breakdown():
    """r6-1225: aln is bound only inside EXISTS {}, so grouping by it after the subquery is invalid Cypher. The
    prover refuses it for a member ("the name aln is not bound here"); an admin's path skips the prover and would
    send it to Neo4j."""
    assert _breakdowns(R6_1225, {"investigation": "TCGA"}) == []


@pytest.mark.parametrize("cypher", [
    "MATCH (s:T_PAT) WHERE COUNT { MATCH (aln:T_A_ALN)-[:DERIVED_FROM*1..12]->(s) } > 0 RETURN count(s) AS n",
    "MATCH (s:T_PAT) CALL { WITH s MATCH (aln:T_A_ALN)-[:DERIVED_FROM*1..12]->(s) RETURN count(aln) AS k } "
    "WITH s, k WHERE k > 0 RETURN count(s) AS n",
    "MATCH (s:T_PAT WHERE EXISTS { MATCH (aln:T_A_ALN)-[:DERIVED_FROM*1..12]->(s) }) RETURN count(s) AS n",
], ids=["count-subquery", "call-subquery", "inline-node-where"])
def test_a_variable_bound_only_inside_any_subquery_gets_no_breakdown(cypher):
    assert _breakdowns(cypher) == []


def test_a_variable_bound_in_a_top_level_match_still_gets_its_breakdown():
    """The same question with aln bound by a top-level MATCH: the variant is built as before and is proven."""
    cy = ("MATCH (s:T_PAT)\nMATCH (aln:T_A_ALN)-[:DERIVED_FROM*1..12]->(r:T_RNA)-[:DERIVED_FROM*1..12]->(s)\n"
          "WHERE EXISTS { MATCH (s)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(inv:Investigation) "
          "WHERE inv.title = $investigation }\nRETURN count(DISTINCT s) AS n")
    ((variant, params),) = _breakdowns(cy, {"investigation": "TCGA"})
    assert variant == (cy.rsplit("\nRETURN", 1)[0] + "\nWITH aln WHERE aln.DataType IS NOT NULL\n"
                       "RETURN DISTINCT aln.DataType AS value")
    assert isinstance(scope_cypher(variant, params, MEMBER), Scoped)


def test_a_variable_a_later_with_leaves_behind_gets_no_breakdown():
    """A top-level WITH ends every name it does not carry, so d is not bound at the RETURN of the first statement."""
    dropped = "MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..6]->(p:T_PAT) WITH DISTINCT p RETURN count(p) AS n"
    assert _breakdowns(dropped, detail=SEQ_DETAIL) == []
    carried = "MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..6]->(p:T_PAT) WITH DISTINCT d, p RETURN count(p) AS n"
    ((variant, params),) = _breakdowns(carried, detail=SEQ_DETAIL)
    assert "\nWITH d WHERE d.DataType IS NOT NULL\nRETURN DISTINCT d.DataType AS value" in variant
    assert isinstance(scope_cypher(variant, params, MEMBER), Scoped)


def test_variants_come_in_the_fixed_order_one_per_fired_check():
    cy = ("MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t AND s.Size IS NOT NULL "
          "RETURN s.id AS id")
    review = GraphReview("suggest", [
        Check("unapplied_value", True, "question names T_D_IMG.Instrument='Zeiss', Cypher never applies it"),
        Check("zero_unproven_base", True, "zero"),
        Check("all_question_narrowed", True, "s.Size IS NOT NULL on an all/different question"),
        Check("stem_miss", True, "DataType CONTAINS 'tiff' misses ['tif']"),
        Check("negated_value", False, ""),
    ], "d", None, [], 0)
    edits = [e.split(":")[0] for e, _c, _p in g2.relaxed_variants(_inp(cy, {"t": "tiff"}), review)]
    assert edits == ["stem_miss", "all_question_narrowed", "zero_unproven_base", "unapplied_value"]


def test_no_fired_check_no_variant():
    review = GraphReview("ok", [Check("stem_miss", False, ""), Check("value_split_rows", True, "x")], None, None, [], 0)
    assert g2.relaxed_variants(_inp(TIFF, {"t": "tiff"}), review) == []
    assert g2.relaxed_variants(_inp(None), _review(("stem_miss", "misses ['tif']"))) == []


def test_an_aggregate_that_is_not_one_count_is_skipped():
    cy = "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN sum(s.Size) AS bytes"
    assert g2.relaxed_variants(_inp(cy, {"t": "tiff"}), _review(("stem_miss", "misses ['tif']"))) == []
    cy = ("MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t WITH s.type AS t, count(*) AS n "
          "RETURN t, n")
    assert g2.relaxed_variants(_inp(cy, {"t": "tiff"}), _review(("stem_miss", "misses ['tif']"))) == []


UNION_COUNT = ("MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t AND s.Size IS NOT NULL "
               "RETURN count(s) AS n\nUNION ALL\n"
               "MATCH (s:T_D_FILE) WHERE toLower(s.Format) CONTAINS $t AND s.Size IS NOT NULL RETURN count(s) AS n")


@pytest.mark.parametrize("union", ["UNION ALL", "UNION", "union"])
def test_a_union_statement_gets_no_variant(monkeypatch, union):
    # an admin skips the prover, so a UNION count can reach the reviewer; only the last branch would be rewritten
    cy = UNION_COUNT.replace("UNION ALL", union)
    calls = _counting(monkeypatch, [61, 61, 61, 61])
    inp = _inp(cy, {"t": "tiff"}, rows=[{"n": 60}, {"n": 1}], count=2, total=2)
    rv = _review(*FOUR_REVIEW, ("all_question_narrowed", "s.Size IS NOT NULL on an all/different question"),
                 suggestion={"kind": "relaxed_variant", "label": "Include all spellings"})
    assert g2.relaxed_variants(inp, rv) == []
    assert g2._row_level(cy) is None and g2._original_n(inp) is None
    assert g2._original_n(_inp(cy, {"t": "tiff"}, rows=[], count=0)) is None
    out = g2.run_tier2(object(), inp, rv)
    assert calls == [] and out.variants == [] and out.disclosure == "d" and "expected_count" not in out.suggestion


def test_a_union_inside_a_subquery_is_not_top_level():
    cy = ("MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t AND EXISTS { MATCH (s)-[:DERIVED_FROM]->(:T_X) "
          "RETURN 1 AS x UNION MATCH (s)-[:DERIVED_FROM]->(:T_Y) RETURN 1 AS x } RETURN s.id AS id")
    assert g2._row_level(cy) == cy
    assert [e.split(":")[0] for e, _c, _p in g2.relaxed_variants(_inp(cy, {"t": "tiff"}), _review(*FOUR_REVIEW))] \
        == ["stem_miss", "zero_unproven_base", "unapplied_value"]


VARIANT_CASES = [
    (TIFF + " ORDER BY id LIMIT 5000", {"t": "tiff"}, ("stem_miss", "misses ['tif']")),
    ("MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN s.type AS type, count(*) AS n",
     {"t": "tiff"}, ("stem_miss", "misses ['tif']")),
    ("MATCH (s:T_D_FILE) WHERE s.Format IS NOT NULL AND s.Size > 3 RETURN count(s) AS n", {},
     ("all_question_narrowed", "s.Format IS NOT NULL on an all/different question")),
    ("MATCH (nhp:T_NHP) WHERE toLower(nhp.search_text) CONTAINS toLower($uid) AND nhp.Sex = 'F' "
     "RETURN count(DISTINCT nhp) AS n", {"uid": "MDL-1"}, ("zero_unproven_base", "zero")),
    ("MATCH (s:T_D_SEQ)-[:DERIVED_FROM*1..6]->(p:T_PAT) WHERE p.Project = $p RETURN count(DISTINCT p) AS n",
     {"p": "x"}, ("unapplied_value", "question names T_D_SEQ.DataType='RNA-Seq', Cypher never applies it")),
]


@pytest.mark.parametrize("cypher, params, check", VARIANT_CASES)
def test_every_variant_is_proven_for_a_member_and_counts_rows(cypher, params, check):
    (_e, variant, vparams), = g2.relaxed_variants(_inp(cypher, params), _review(check))
    outcome = scope_cypher(variant, vparams, MEMBER)
    assert isinstance(outcome, Scoped), (variant, getattr(outcome, "reasons", None))
    final_return = variant.rsplit("RETURN", 1)[1]
    assert "count(" not in final_return and "sum(" not in final_return


# ------------------------------------------------------------------ run_tier2 --------------------------------------
@pytest.fixture
def fake_clock(monkeypatch):
    clock = SimpleNamespace(t=100.0)
    monkeypatch.setattr(g2, "_clock", lambda: clock.t)
    return clock


def _counting(monkeypatch, totals, clock=None, advance=0.0):
    calls = []

    def fake(config, cypher, parameters, *, timeout_s=5):
        calls.append(SimpleNamespace(cypher=cypher, parameters=parameters, timeout_s=timeout_s))
        if clock is not None:
            clock.t += advance
        got = totals[len(calls) - 1]
        return got if isinstance(got, dict) else {"ok": True, "total": got, "error": None, "elapsed_ms": 12}
    monkeypatch.setattr(g2, "count_of", fake)
    return calls


FOUR = ("MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t AND s.Size IS NOT NULL RETURN s.id AS id")
FOUR_REVIEW = [("stem_miss", "misses ['tif']"), ("all_question_narrowed", "s.Size IS NOT NULL on an all question"),
               ("zero_unproven_base", "zero"),
               ("unapplied_value", "question names T_D_IMG.Instrument='Zeiss', Cypher never applies it")]


def test_the_timeout_is_clamped_to_the_budget_left(monkeypatch, fake_clock):
    calls = _counting(monkeypatch, [10, 20], clock=fake_clock, advance=0.0)
    g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}, rows=[{"id": 1}]), _review(*FOUR_REVIEW), budget_s=2.5)
    assert [c.timeout_s for c in calls] == [2, 2]


def test_no_variant_starts_with_under_one_second_left(monkeypatch, fake_clock):
    calls = _counting(monkeypatch, [10, 20], clock=fake_clock, advance=1.7)
    out = g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}, rows=[{"id": 1}]), _review(*FOUR_REVIEW), budget_s=2.5)
    assert [c.timeout_s for c in calls] == [2]                           # 0.8 s left: the second never starts
    assert len(out.variants) == 1
    assert g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}), _review(*FOUR_REVIEW), budget_s=0.99).variants == []


def test_a_full_budget_gives_five_second_timeouts_and_two_variants_at_most(monkeypatch, fake_clock):
    calls = _counting(monkeypatch, [10, 20, 30, 40], clock=fake_clock, advance=3.0)
    out = g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}, rows=[{"id": 1}]), _review(*FOUR_REVIEW))
    assert [c.timeout_s for c in calls] == [5, 5]                        # 8 s, then 5 s left
    assert [v["edit"].split(":")[0] for v in out.variants] == ["stem_miss", "all_question_narrowed"]
    assert set(out.variants[0]) == {"edit", "total", "elapsed_ms", "ok"}


def test_a_slow_original_statement_skips_tier2(monkeypatch):
    calls = _counting(monkeypatch, [10])
    rv = _review(*FOUR_REVIEW)
    out = g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}, elapsed_ms=5001), rv)
    assert calls == [] and out.variants == [] and out.disclosure == rv.disclosure
    g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}, elapsed_ms=5000), rv, max_variants=1)
    assert len(calls) == 1


def test_a_differing_total_is_disclosed_and_sets_the_suggestions_expected_count(monkeypatch):
    _counting(monkeypatch, [8324])
    catalog = DictCatalog({"T_D_IMG.DataType": [["tif", 7000], ["TIF", 1300], ["tiff", 1306]]})
    inp = _inp(TIFF + " LIMIT 5000", {"t": "tiff"}, rows=[{"id": i} for i in range(3)], total=1306)
    tier1 = review_tier1(inp, catalog)
    out = g2.run_tier2(object(), inp, tier1)
    assert out.disclosure.endswith("Every spelling of 'tif' gives 8,324.")
    assert out.suggestion["expected_count"] == 8324 and out.suggestion["kind"] == "relaxed_variant"
    assert out.variants == [{"edit": out.variants[0]["edit"], "total": 8324, "elapsed_ms": 12, "ok": True}]
    assert "expected_count" not in (tier1.suggestion or {}) and tier1.variants == []   # the input is not changed
    assert out.verdict == tier1.verdict and out.checks == tier1.checks


def test_a_case_sensitive_search_is_not_called_every_spelling(monkeypatch):
    _counting(monkeypatch, [7000])
    cy = "MATCH (s:T_D_IMG) WHERE s.DataType CONTAINS $t RETURN s.id AS id"
    out = g2.run_tier2(object(), _inp(cy, {"t": "tiff"}, rows=[{"id": 1}], total=1306),
                       _review(("stem_miss", "DataType CONTAINS 'tiff' misses ['tif']")))
    assert out.disclosure.endswith("Searching for 'tif' instead of 'tiff' gives 7,000.")


def test_an_equal_total_adds_nothing(monkeypatch):
    _counting(monkeypatch, [1306])
    rv = _review(("stem_miss", "misses ['tif']"), suggestion={"kind": "relaxed_variant", "label": "x"})
    out = g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}, rows=[{"id": 1}], total=1306), rv)
    assert out.disclosure == "d" and "expected_count" not in out.suggestion
    assert out.variants[0]["total"] == 1306


def test_a_count_only_original_is_compared_by_its_value(monkeypatch):
    _counting(monkeypatch, [43])
    cy = "MATCH (st:SampleType) WHERE st.title STARTS WITH 'D.' AND st.sample_count > 0 RETURN count(st) AS n"
    rv = _review(("all_question_narrowed", "sample_count > 0 on an all/different question"),
                 suggestion={"kind": "relaxed_variant", "label": "Count every defined type"})
    out = g2.run_tier2(object(), _inp(cy, rows=[{"n": 40}], count=1, total=1), rv)
    assert out.disclosure == "d Counting every defined type, including those with no samples, gives 43."
    assert out.suggestion["expected_count"] == 43


def test_a_breakdown_original_is_compared_by_the_sum_of_its_counts(monkeypatch):
    cy = "MATCH (s:T_D_IMG) WHERE toLower(s.DataType) CONTAINS $t RETURN s.type AS type, count(*) AS n"
    rows = [{"type": "a", "n": 1000}, {"type": "b", "n": 306}]
    rv = _review(("stem_miss", "misses ['tif']"))
    _counting(monkeypatch, [1306])
    assert g2.run_tier2(object(), _inp(cy, {"t": "tiff"}, rows=rows, total=2), rv).disclosure == "d"
    _counting(monkeypatch, [8324])
    assert g2.run_tier2(object(), _inp(cy, {"t": "tiff"}, rows=rows, total=2), rv).disclosure.endswith("8,324.")
    # a count(DISTINCT ...) per group does not sum to the whole, and a capped breakdown is not the whole
    _counting(monkeypatch, [1306])
    distinct = cy.replace("count(*)", "count(DISTINCT s)")
    assert g2.run_tier2(object(), _inp(distinct, {"t": "tiff"}, rows=rows, total=2), rv).disclosure.endswith("1,306.")
    _counting(monkeypatch, [1306])
    assert g2.run_tier2(object(), _inp(cy, {"t": "tiff"}, rows=rows, total=9), rv).disclosure.endswith("1,306.")


def test_a_value_split_suggestion_never_takes_a_variants_count(monkeypatch):
    _counting(monkeypatch, [8324])
    split = {"kind": "value_split", "label": "Only Converter", "query": "q", "reason": "r"}
    rv = _review(("value_split_rows", "x"), ("stem_miss", "misses ['tif']"), suggestion=dict(split))
    out = g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}, rows=[{"id": 1}], total=1306), rv)
    assert out.suggestion == split and "8,324" in out.disclosure


def test_the_base_count_and_the_breakdown_are_disclosed(monkeypatch):
    _counting(monkeypatch, [725, 3])
    cy = ("MATCH (nhp:T_NHP) WHERE toLower(nhp.search_text) CONTAINS toLower($uid) AND nhp.Sex = 'F' "
          "RETURN count(DISTINCT nhp) AS n")
    rv = _review(("zero_unproven_base", "zero"),
                 ("unapplied_value", "question names T_NHP.Species='Macaca mulatta', Cypher never applies it"))
    out = g2.run_tier2(object(), _inp(cy, {"uid": "MDL-1"}, rows=[{"n": 0}], count=1, total=1), rv)
    assert out.disclosure == ("d Before its other filters, the search matches 725 records. "
                              "The matched records hold 3 different values where the question named "
                              "'Macaca mulatta'.")
    assert out.suggestion is None


def test_a_breakdown_of_one_value_is_not_disclosed(monkeypatch):
    _counting(monkeypatch, [1])
    rv = _review(("unapplied_value", "question names T_NHP.Species='Macaca mulatta', Cypher never applies it"))
    out = g2.run_tier2(object(), _inp("MATCH (nhp:T_NHP) RETURN nhp.id AS id", rows=[{"id": 1}]), rv)
    assert out.disclosure == "d" and out.variants[0]["total"] == 1


def test_a_refused_variant_is_recorded_and_counts_against_the_cap(monkeypatch):
    calls = _counting(monkeypatch, [{"ok": False, "total": None, "error": "refused", "elapsed_ms": 1}, 50, 60])
    out = g2.run_tier2(object(), _inp(FOUR, {"t": "tiff"}, rows=[{"id": 1}]), _review(*FOUR_REVIEW))
    assert len(calls) == 2
    assert out.variants[0] == {"edit": out.variants[0]["edit"], "total": None, "elapsed_ms": 1, "ok": False}


def test_the_disclosure_stays_under_its_cap(monkeypatch):
    _counting(monkeypatch, [8324])
    long = "x" * 290
    out = g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}, rows=[{"id": 1}], total=1306),
                       GraphReview("suggest", [Check("stem_miss", True, "misses ['tif']")], long, None, [], 0))
    assert out.disclosure == long and out.variants[0]["total"] == 8324


def test_a_missing_disclosure_takes_the_fact(monkeypatch):
    _counting(monkeypatch, [8324])
    out = g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}, rows=[{"id": 1}], total=1306),
                       GraphReview("suggest", [Check("stem_miss", True, "misses ['tif']")], None, None, [], 0))
    assert out.disclosure == "Every spelling of 'tif' gives 8,324."


def test_run_tier2_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("count exploded")
    monkeypatch.setattr(g2, "count_of", boom)
    rv = _review(("stem_miss", "misses ['tif']"))
    out = g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}), rv)
    assert out.verdict == "suggest" and "count exploded" in out.error and out.disclosure == "d"
    monkeypatch.setattr(g2, "_variants", boom)
    assert "count exploded" in g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}), rv).error
    assert g2.relaxed_variants(_inp(TIFF, {"t": "tiff"}), rv) == []


def test_run_tier2_adds_its_time_to_the_review(monkeypatch, fake_clock):
    _counting(monkeypatch, [8324], clock=fake_clock, advance=0.25)
    rv = GraphReview("suggest", [Check("stem_miss", True, "misses ['tif']")], "d", None, [], 40)
    out = g2.run_tier2(object(), _inp(TIFF, {"t": "tiff"}, rows=[{"id": 1}], total=1306), rv)
    assert out.elapsed_ms == 290
