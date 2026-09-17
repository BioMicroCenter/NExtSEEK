"""A2: the live catalog reader, ``chat_nextseek.graph_catalog``.

Spec: ``docs/superpowers/specs/2026-09-15-graph-search-nessie-design.md`` section 4.1, D6 and D7; plan task T1.
Every test drives a fake driver and a patched clock: nothing reaches a network or a real Neo4j.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import types
from pathlib import Path

import pytest

from NessieAI import paths
from chat_nextseek import graph_catalog as gc
from chat_nextseek.config import ChatConfig

CONTEXT = paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context"
URI_A = "bolt://graph-a:7687"
URI_B = "bolt://graph-b:7687"

# The statement constants the module promises (plan task T1, "Interfaces").
STATEMENT_NAMES = (
    "META", "INDEX", "GUARD", "TYPES_ADMIN",
    "VOCAB_INVESTIGATIONS", "VOCAB_PROJECTS", "VOCAB_STUDIES", "VOCAB_PUBLISHED", "VOCAB_EDGES",
)


class FakeServiceUnavailable(Exception):
    """Stands in for the driver's connection errors."""


# --- the fake graph -------------------------------------------------------------------------------------------------


def _type_rows() -> dict:
    return {
        "TIS": {
            "title": "TIS", "label": "T_TIS", "name": "Tissue Sample", "summary": "A piece of tissue. More text.",
            "clade": "Source", "sample_count": 107412, "curated_parents": "PAT, MUS", "curated_children": "D.SEQ",
            "attributes": [
                {"title": "Organ", "value_type": "string", "declared": True, "needs_backticks": False,
                 "sample_count": 16841, "meaning": "The organ the tissue came from, as a word.", "unit_key": None,
                 "role": "descriptive", "top_values": ["Lung", "lung"], "top_counts": [16841, 5893]},
                {"title": "Catalog#", "value_type": "string", "declared": False, "needs_backticks": True,
                 "sample_count": 12},
            ],
            "never_filled": 7,
        },
        "D.SEQ": {
            "title": "D.SEQ", "label": "T_D_SEQ", "name": "Sequencing Data", "summary": None, "clade": "Data",
            "sample_count": 5000, "curated_parents": ["TIS", "CEL"], "curated_children": None,
            "attributes": [
                {"title": "ReadLength", "value_type": "integer", "declared": True, "needs_backticks": False,
                 "sample_count": 4000, "num_min": 50, "num_max": 150},
                {"title": "Run Date", "value_type": "date", "declared": True, "needs_backticks": True,
                 "sample_count": 3000, "date_min": "2019-01-02", "date_max": "2024-05-06"},
            ],
            "never_filled": 0,
        },
        "CEL": {
            "title": "CEL", "label": "T_CEL", "name": "Cells", "summary": "Cells.", "clade": "Source",
            "sample_count": 10, "curated_parents": None, "curated_children": None, "attributes": [],
            "never_filled": 3,
        },
    }


class FakeGraph:
    """One Neo4j database: what each statement returns, and a record of every call."""

    def __init__(self, catalog_hash: str = "h1"):
        self.meta = {"schema_version": "1.1", "catalog_hash": catalog_hash,
                     "synced_at": "2026-09-15T08:00:00Z", "has_usage": False}
        self.index = [
            {"title": "CEL", "label": "T_CEL", "name": "Cells", "clade": "Source", "sample_count": 10,
             "deprecated": False, "attributes_with_values": 0},
            {"title": "D.SEQ", "label": "T_D_SEQ", "name": "Sequencing Data", "clade": "Data", "sample_count": 5000,
             "deprecated": False, "attributes_with_values": 2},
            {"title": "OLD", "label": "T_OLD", "name": None, "clade": None, "sample_count": 0,
             "deprecated": True, "attributes_with_values": 0},
            {"title": "TIS", "label": "T_TIS", "name": "Tissue Sample", "clade": "Source", "sample_count": 107412,
             "deprecated": False, "attributes_with_values": 2},
        ]
        self.guard = [
            {"label": "T_TIS", "titles": ["Organ", "Catalog#"]},
            {"label": "T_D_SEQ", "titles": ["ReadLength", "Run Date"]},
        ]
        self.types = _type_rows()
        self.vocab = {
            "VOCAB_INVESTIGATIONS": [{"title": "MetNet"}, {"title": "GBM_BTC"}],
            "VOCAB_PROJECTS": [{"title": "Project B"}, {"title": "Project A"}],
            "VOCAB_STUDIES": [{"title": "Study 2"}, {"title": "Study 1"}, {"title": None}],
            "VOCAB_PUBLISHED": [{"title": "Study 1", "doi": "10.1000/example", "pmid": ""}],
            "VOCAB_EDGES": [
                {"assay": "Short Read Sequencing", "protocol": "RNA prep", "parent_type": "TIS", "child_type": "D.SEQ"},
                {"assay": "Short Read Sequencing", "protocol": None, "parent_type": "CEL", "child_type": "D.SEQ"},
                {"assay": "Flow Cytometry", "protocol": "RNA prep", "parent_type": "TIS", "child_type": "D.FLOW"},
                {"assay": None, "protocol": "Dissection", "parent_type": "PAT", "child_type": "TIS"},
            ],
        }
        self.has_meta = True
        self.error: Exception | None = None     # raised by every statement while set
        self.fail_on: set[str] = set()          # statement names that raise FakeServiceUnavailable
        self.calls: list[dict] = []             # one per statement run inside a transaction
        self.sessions: list[dict] = []          # the keyword arguments of every driver.session()
        self.violations: list[str] = []         # session.run / execute_write / execute_query

    def names(self) -> list[str]:
        return [c["name"] for c in self.calls]

    def rows(self, name: str, params: dict) -> list[dict]:
        if name == "META":
            return [dict(self.meta)] if self.has_meta else []
        if name == "INDEX":
            return [dict(r) for r in self.index]
        if name == "GUARD":
            return [dict(r) for r in self.guard]
        if name == "TYPES_ADMIN":
            return [self.types[t] for t in params.get("types", []) if t in self.types]
        return [dict(r) for r in self.vocab[name]]


class FakeTx:
    def __init__(self, graph: FakeGraph, session: "FakeSession", timeout):
        self.graph, self.session, self.timeout = graph, session, timeout

    def run(self, text, parameters=None, **kwargs):
        by_text = {getattr(gc, n): n for n in STATEMENT_NAMES}
        assert text in by_text, f"unexpected statement: {text!r}"
        name = by_text[text]
        params = dict(parameters or {}, **kwargs)
        self.graph.calls.append({"name": name, "params": params, "via": "execute_read",
                                 "timeout": self.timeout, "mode": self.session.mode,
                                 "database": self.session.database})
        if self.graph.error is not None:
            raise self.graph.error
        if name in self.graph.fail_on:
            raise FakeServiceUnavailable(f"{name} failed")
        return iter(self.graph.rows(name, params))


class FakeSession:
    def __init__(self, graph: FakeGraph, **kwargs):
        self.graph = graph
        self.mode = kwargs.get("default_access_mode")
        self.database = kwargs.get("database")
        graph.sessions.append(dict(kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def execute_read(self, fn, *args, **kwargs):
        return fn(FakeTx(self.graph, self, getattr(fn, "timeout", None)), *args, **kwargs)

    def execute_write(self, fn, *args, **kwargs):
        self.graph.violations.append("execute_write")
        raise AssertionError("the catalog reader must never open a write transaction")

    def run(self, *args, **kwargs):
        self.graph.violations.append("session.run")
        raise AssertionError("session.run outside a transaction function")


class FakeDriver:
    def __init__(self, graph: FakeGraph):
        self.graph = graph
        self.closed = False

    def session(self, **kwargs):
        return FakeSession(self.graph, **kwargs)

    def execute_query(self, *args, **kwargs):
        self.graph.violations.append("execute_query")
        raise AssertionError("the catalog reader reads through execute_read only")

    def close(self):
        self.closed = True


class Harness:
    """The patched driver factory: one FakeGraph per URI, and every factory call and driver it made."""

    def __init__(self):
        self.graphs: dict[str, FakeGraph] = {URI_A: FakeGraph()}
        self.factory_calls: list[str] = []
        self.drivers: list[FakeDriver] = []
        self.factory_error: Exception | None = None

    @property
    def graph(self) -> FakeGraph:
        return self.graphs[URI_A]

    def factory(self, config):
        self.factory_calls.append(config.NEO4J_URI)
        if self.factory_error is not None:
            raise self.factory_error
        driver = FakeDriver(self.graphs.setdefault(config.NEO4J_URI, FakeGraph()))
        self.drivers.append(driver)
        return driver


def cfg(uri: str | None = URI_A, database: str = "neo4j"):
    return types.SimpleNamespace(NEO4J_URI=uri, NEO4J_DATABASE=database, NEO4J_USER="neo4j",
                                 NEO4J_PASSWORD="not-a-secret")


@pytest.fixture(autouse=True)
def _fresh_cache():
    gc.reset_cache()
    yield
    gc.reset_cache()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gc, "_now", lambda: now[0])
    return now


@pytest.fixture
def harness(monkeypatch, clock):
    h = Harness()
    monkeypatch.setattr(gc, "_make_driver", h.factory)
    return h


# --- the interface --------------------------------------------------------------------------------------------------


def test_interface_constants():
    assert gc.SCHEMA_VERSION == "1.1"
    assert (gc.HASH_RECHECK_S, gc.DETAIL_TTL_S, gc.VOCAB_TTL_S, gc.FAILURE_MEMORY_S, gc.QUERY_TIMEOUT_S) == (
        60, 600, 3600, 60, 10)
    assert issubclass(gc.CatalogUnavailable, RuntimeError)
    for name in STATEMENT_NAMES:
        assert isinstance(getattr(gc, name), str), name


def test_vocabulary_statements_carry_no_cap():
    # The old assay-connection fetch stopped at LIMIT 300 (spec D6).
    for name in STATEMENT_NAMES:
        if name.startswith("VOCAB_"):
            assert "LIMIT" not in getattr(gc, name).upper(), name


# --- the snapshot and its cache -------------------------------------------------------------------------------------


def test_first_call_reads_meta_index_and_guard_once(harness):
    snap = gc.get_snapshot(cfg())

    assert harness.graph.names() == ["META", "INDEX", "GUARD"]
    assert isinstance(snap, gc.CatalogSnapshot)
    assert snap.catalog_hash == "h1"
    assert snap.synced_at == "2026-09-15T08:00:00Z"
    assert snap.has_usage is False
    assert [r.title for r in snap.index] == ["CEL", "D.SEQ", "OLD", "TIS"]
    tis = next(r for r in snap.index if r.title == "TIS")
    assert tis == gc.TypeIndexRow(title="TIS", label="T_TIS", name="Tissue Sample", clade="Source",
                                  sample_count=107412, deprecated=False, attributes_with_values=2)
    old = next(r for r in snap.index if r.title == "OLD")
    assert old.deprecated is True and old.sample_count == 0 and old.name is None


def test_guard_maps_every_type_label_to_its_attributes_with_values(harness):
    snap = gc.get_snapshot(cfg())

    assert snap.guard["T_TIS"] == frozenset({"Organ", "Catalog#"})
    assert snap.guard["T_D_SEQ"] == frozenset({"ReadLength", "Run Date"})
    # A known type with no filled attribute is still a known label, with nothing to read.
    assert snap.guard["T_CEL"] == frozenset()
    assert "T_NOPE" not in snap.guard
    with pytest.raises(TypeError):
        snap.guard["T_NEW"] = frozenset()  # read-only


def test_second_call_inside_the_recheck_window_reads_nothing(harness, clock):
    first = gc.get_snapshot(cfg())
    clock[0] += gc.HASH_RECHECK_S - 1
    second = gc.get_snapshot(cfg())

    assert second is first
    assert harness.graph.names() == ["META", "INDEX", "GUARD"]


def test_same_hash_after_the_window_reads_meta_only(harness, clock):
    first = gc.get_snapshot(cfg())
    clock[0] += gc.HASH_RECHECK_S
    harness.graph.meta["synced_at"] = "2026-09-15T09:00:00Z"
    second = gc.get_snapshot(cfg())

    assert harness.graph.names() == ["META", "INDEX", "GUARD", "META"]
    assert second.index == first.index and second.guard == first.guard
    assert second.synced_at == "2026-09-15T09:00:00Z"
    # and the window restarts from that check
    clock[0] += gc.HASH_RECHECK_S - 1
    gc.get_snapshot(cfg())
    assert harness.graph.names() == ["META", "INDEX", "GUARD", "META"]


def test_new_hash_rereads_index_and_guard_and_drops_cached_details(harness, clock):
    gc.get_snapshot(cfg())
    gc.get_type_details(cfg(), ["TIS"])
    assert harness.graph.names() == ["META", "INDEX", "GUARD", "TYPES_ADMIN"]

    harness.graph.meta["catalog_hash"] = "h2"
    harness.graph.index = [r for r in harness.graph.index if r["title"] != "OLD"]
    harness.graph.guard = [{"label": "T_TIS", "titles": ["Organ", "Sequencer"]}]
    clock[0] += gc.HASH_RECHECK_S
    snap = gc.get_snapshot(cfg())

    assert harness.graph.names()[4:] == ["META", "INDEX", "GUARD"]
    assert snap.catalog_hash == "h2"
    assert "OLD" not in [r.title for r in snap.index]
    assert snap.guard["T_TIS"] == frozenset({"Organ", "Sequencer"})

    # Well inside the detail lifetime, but the hash moved: the detail is read again.
    gc.get_type_details(cfg(), ["TIS"])
    assert harness.graph.names()[7:] == ["TYPES_ADMIN"]


def test_two_uris_get_separate_snapshots(harness):
    harness.graphs[URI_B] = FakeGraph(catalog_hash="hB")

    a = gc.get_snapshot(cfg(URI_A))
    b = gc.get_snapshot(cfg(URI_B))

    assert (a.catalog_hash, b.catalog_hash) == ("h1", "hB")
    assert harness.factory_calls == [URI_A, URI_B]
    assert harness.graphs[URI_A].names() == ["META", "INDEX", "GUARD"]
    assert harness.graphs[URI_B].names() == ["META", "INDEX", "GUARD"]
    # each is cached on its own key
    assert gc.get_snapshot(cfg(URI_A)) is a
    assert gc.get_snapshot(cfg(URI_B)) is b
    assert harness.factory_calls == [URI_A, URI_B]


def test_the_database_name_is_part_of_the_key(harness):
    gc.get_snapshot(cfg(URI_A, "neo4j"))
    gc.get_snapshot(cfg(URI_A, "other"))

    assert harness.factory_calls == [URI_A, URI_A]
    assert [s["database"] for s in harness.graph.sessions] == ["neo4j"] * 3 + ["other"] * 3


# --- failures -------------------------------------------------------------------------------------------------------


def _break(harness, how: str) -> None:
    if how == "no_graphmeta":
        harness.graph.has_meta = False
    elif how == "schema_1_0":
        harness.graph.meta["schema_version"] = "1.0"
    elif how == "driver_error":
        harness.graph.error = FakeServiceUnavailable("connection refused")
    elif how == "factory_error":
        harness.factory_error = FakeServiceUnavailable("cannot open a driver")
    else:
        raise AssertionError(how)


def _repair(harness) -> None:
    harness.graph.has_meta = True
    harness.graph.meta["schema_version"] = "1.1"
    harness.graph.error = None
    harness.factory_error = None


@pytest.mark.parametrize("how", ["no_graphmeta", "schema_1_0", "driver_error", "factory_error"])
def test_failure_raises_and_is_remembered_for_the_failure_window(harness, clock, how):
    _break(harness, how)
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())
    factory_calls, statements = len(harness.factory_calls), len(harness.graph.calls)
    assert factory_calls == 1

    # Remembered: no new driver, no statement, the same refusal.
    _repair(harness)
    clock[0] += gc.FAILURE_MEMORY_S - 1
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())
    assert len(harness.factory_calls) == factory_calls
    assert len(harness.graph.calls) == statements

    # After the window the reader tries again and recovers.
    clock[0] += 1
    snap = gc.get_snapshot(cfg())
    assert snap.catalog_hash == "h1"


# --- schema versions ------------------------------------------------------------------------------------------------
# The sync work bumps the graph to 1.2 (Sample.source_hash, GraphMeta.label_maps_hash; the catalog is unchanged), so
# the reader accepts 1.1 or any later version. An exact match would send a 1.2 graph back to the committed fallback.


@pytest.mark.parametrize("version", ["1.1", "1.2", "1.10", " 1.2 ", "2.0"])
def test_schema_version_at_or_above_the_minimum_is_supported(version):
    assert gc.schema_version_supported(version)


@pytest.mark.parametrize("version", ["1.0", "0.9", "1", "1.x", "", "abc", None])
def test_schema_version_below_the_minimum_or_malformed_is_not(version):
    assert not gc.schema_version_supported(version)


def test_a_1_2_graph_is_read_live_and_its_version_recorded(harness):
    harness.graph.meta["schema_version"] = "1.2"

    snap = gc.get_snapshot(cfg())

    assert snap.catalog_hash == "h1"
    assert snap.schema_version == "1.2"
    assert gc.cache_state(cfg())["graph_schema_version"] == "1.2"


def test_a_version_bump_with_the_same_hash_reads_meta_only_and_updates_the_version(harness, clock):
    gc.get_snapshot(cfg())
    harness.graph.meta["schema_version"] = "1.2"  # the catalog, and so its hash, is unchanged
    clock[0] += gc.HASH_RECHECK_S

    snap = gc.get_snapshot(cfg())

    assert harness.graph.names() == ["META", "INDEX", "GUARD", "META"]
    assert snap.schema_version == "1.2"


def test_a_1_0_graph_names_the_minimum_in_its_refusal(harness):
    harness.graph.meta["schema_version"] = "1.0"

    with pytest.raises(gc.CatalogUnavailable, match="1.1"):
        gc.get_snapshot(cfg())


def test_a_driver_error_closes_and_forgets_the_driver(harness, clock):
    gc.get_snapshot(cfg())
    first_driver = harness.drivers[0]
    harness.graph.error = FakeServiceUnavailable("connection reset")
    clock[0] += gc.HASH_RECHECK_S
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())
    assert first_driver.closed is True

    harness.graph.error = None
    clock[0] += gc.FAILURE_MEMORY_S
    gc.get_snapshot(cfg())
    assert len(harness.drivers) == 2 and harness.drivers[1] is not first_driver


def test_a_failed_recheck_drops_the_cached_snapshot(harness, clock):
    gc.get_snapshot(cfg())
    harness.graph.meta["schema_version"] = "1.0"
    clock[0] += gc.HASH_RECHECK_S
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())
    # the old snapshot is not served while the graph is refused
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())


@pytest.mark.parametrize("uri", [None, ""])
def test_an_unset_uri_is_unavailable_without_a_driver(harness, uri):
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg(uri))
    assert harness.factory_calls == []


def test_an_unset_password_is_unavailable_without_a_driver(harness):
    config = cfg()
    config.NEO4J_PASSWORD = None
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(config)
    assert harness.factory_calls == []


# --- type details ---------------------------------------------------------------------------------------------------


def test_type_details_query_only_the_requested_known_titles(harness):
    details = gc.get_type_details(cfg(), ["TIS", "NOPE", "D.SEQ", "TIS"])

    admin = [c for c in harness.graph.calls if c["name"] == "TYPES_ADMIN"]
    assert len(admin) == 1
    assert admin[0]["params"]["types"] == ["TIS", "D.SEQ"]
    assert [d.title for d in details] == ["TIS", "D.SEQ"]


def test_type_details_carry_the_admin_form(harness):
    tis, dseq = gc.get_type_details(cfg(), ["TIS", "D.SEQ"])

    assert isinstance(tis, gc.TypeDetail)
    assert (tis.label, tis.name, tis.clade, tis.sample_count) == ("T_TIS", "Tissue Sample", "Source", 107412)
    assert tis.summary == "A piece of tissue. More text."
    assert (tis.curated_parents, tis.curated_children) == ("PAT, MUS", "D.SEQ")
    assert tis.never_filled == 7
    organ, catalog = tis.attributes
    assert organ == gc.AttributeRow(
        title="Organ", value_type="string", declared=True, needs_backticks=False, sample_count=16841,
        meaning="The organ the tissue came from, as a word.", unit_key=None, role="descriptive",
        top_values=("Lung", "lung"), top_counts=(16841, 5893))
    assert (catalog.title, catalog.declared, catalog.needs_backticks) == ("Catalog#", False, True)
    assert catalog.top_values == () and catalog.top_counts == () and catalog.meaning is None

    # a list of curated parents is joined, numbers and dates come through as their fields
    assert dseq.curated_parents == "TIS, CEL" and dseq.curated_children is None and dseq.summary is None
    reads, run_date = dseq.attributes
    assert (reads.value_type, reads.num_min, reads.num_max) == ("integer", 50.0, 150.0)
    assert (run_date.date_min, run_date.date_max) == ("2019-01-02", "2024-05-06")


def test_type_details_are_cached_per_hash_and_title_then_expire(harness, clock):
    gc.get_type_details(cfg(), ["TIS"])
    gc.get_type_details(cfg(), ["TIS"])
    gc.get_type_details(cfg(), ["TIS", "CEL"])

    admin = [c["params"]["types"] for c in harness.graph.calls if c["name"] == "TYPES_ADMIN"]
    assert admin == [["TIS"], ["CEL"]]

    clock[0] += gc.DETAIL_TTL_S - 1
    gc.get_type_details(cfg(), ["TIS"])
    admin = [c["params"]["types"] for c in harness.graph.calls if c["name"] == "TYPES_ADMIN"]
    assert admin == [["TIS"], ["CEL"]]

    clock[0] += 1
    gc.get_type_details(cfg(), ["TIS"])
    admin = [c["params"]["types"] for c in harness.graph.calls if c["name"] == "TYPES_ADMIN"]
    assert admin == [["TIS"], ["CEL"], ["TIS"]]


def test_type_details_for_unknown_titles_run_no_query(harness):
    assert gc.get_type_details(cfg(), ["NOPE", None, ""]) == []
    assert gc.get_type_details(cfg(), []) == []
    assert "TYPES_ADMIN" not in harness.graph.names()


def test_type_details_raise_when_the_catalog_is_unavailable(harness):
    harness.graph.has_meta = False
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_type_details(cfg(), ["TIS"])


def test_a_failed_detail_read_is_unavailable_and_remembered(harness, clock):
    gc.get_snapshot(cfg())
    harness.graph.fail_on = {"TYPES_ADMIN"}
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_type_details(cfg(), ["TIS"])
    harness.graph.fail_on = set()
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())
    clock[0] += gc.FAILURE_MEMORY_S
    assert [d.title for d in gc.get_type_details(cfg(), ["TIS"])] == ["TIS"]


# --- P7b: is the value list all of them? ----------------------------------------------------------------------------
#
# PROPOSALS.md P7b: "add Attribute.distinct_count so the agent can tell 'these 10 values are all of them' from
# 'top 10 of 62'". Every number in these fixtures was read off the deployed graph (catalog_hash 1168b5e6…a362ba,
# schema 1.2, 2026-09-17) with read-only Cypher, so the cases are the review's own failures:
#
#   T_MUS.Sex     8 distinct values on 5,631 samples: F 2,913, M 2,371, Male 179, Female 160, and four more.
#                 A complete list makes the right predicate derivable: F + Female = 3,073, which is exactly the
#                 answer key for advanced.female_mice, where toLower(s.Sex) = 'female' returned 160.
#   T_TIS.Organ   106 distinct values on 95,630 samples. Its true top ten totals 63,045 and does NOT hold LUNG
#                 (2,532, twelfth), so the lung answer is 22,734 and a value-set predicate built from the list
#                 alone reaches 20,202. This is the list the agent must not read as exhaustive.
#   T_BAC.Strain  8 designations on 19 samples, none of them mTB (FINDINGS H3: toLower(s.Strain) CONTAINS 'mtb'
#                 answered 0 where the truth is 2,999). A list known to be whole says the field is the wrong one.


def _attr(**kw) -> gc.AttributeRow:
    """An AttributeRow with the required fields filled, and whatever a test varies."""
    fields = {"title": "Strain", "value_type": "string", "declared": True, "needs_backticks": False,
              "sample_count": 19, "meaning": None, "unit_key": None, "role": "data"}
    return gc.AttributeRow(**{**fields, **kw})


def _values(n: int) -> tuple:
    return tuple(f"v{i}" for i in range(n))


# T_MUS.Sex, whole. The counts total sample_count, which is what makes the set closed.
SEX = {"title": "Sex", "value_type": "string", "declared": True, "needs_backticks": False,
       "sample_count": 5631, "distinct_count": 8,
       "top_values": ["F", "M", "Male", "Female", "Female and male in equal ratios", "DAM", "F?", "Male-hydro"],
       "top_counts": [2913, 2371, 179, 160, 5, 1, 1, 1]}
# T_TIS.Organ, truncated: the true top ten of 106 values.
ORGAN = {"title": "Organ", "value_type": "string", "declared": True, "needs_backticks": False,
         "sample_count": 95630, "distinct_count": 106,
         "top_values": ["Lung", "Blood", "Liver", "Lymph Node", "Kidney", "LIVER", "lung", "Pancreas", "Brain",
                        "Bronchus and lung"],
         "top_counts": [16841, 11189, 7443, 6329, 5363, 4154, 3361, 3080, 2669, 2616]}
# T_BAC.Strain, whole.
STRAIN = {"title": "Strain", "value_type": "string", "declared": True, "needs_backticks": False,
          "sample_count": 19, "distinct_count": 8,
          "top_values": ["H37Rv", "Erdman", "L2-G2G (strain 8165)", "YFP-tagged H37Rv", "HN878", "BcRv",
                         "Danish SSI 1331", "mc2 155"],
          "top_counts": [10, 3, 1, 1, 1, 1, 1, 1]}


def _only_attribute(harness, attribute: dict) -> gc.AttributeRow:
    """Read one attribute through the whole reader: injected on CEL, the fixture type with none of its own, so no
    other test's expectations move."""
    harness.graph.types["CEL"]["attributes"] = [dict(attribute)]
    detail, = gc.get_type_details(cfg(), ["CEL"])
    row, = detail.attributes
    return row


def test_the_statement_asks_for_the_distinct_count():
    # A catalog that carries it needs no reader change; on one that does not, the map projection returns null.
    assert ".distinct_count" in gc.TYPES_ADMIN


def test_the_statement_caps_the_value_list_it_reads(harness):
    # What the reader shows is capped in Cypher, so ten values are a top ten unless a distinct count says otherwise.
    gc.get_type_details(cfg(), ["TIS"])

    admin = next(c for c in harness.graph.calls if c["name"] == "TYPES_ADMIN")
    assert admin["params"]["top"] == gc.TOP_VALUES_MAX
    assert "a.top_values[0..$top]" in gc.TYPES_ADMIN and "a.top_counts[0..$top]" in gc.TYPES_ADMIN


def test_a_catalog_without_a_distinct_count_reads_as_unknown_not_zero(harness):
    # The state of every deployed graph today: no Attribute carries top_values or a distinct count, and a missing
    # property must read as unknown, never as "no values" or "zero distinct values".
    tis, = gc.get_type_details(cfg(), ["TIS"])
    organ, catalog = tis.attributes

    assert (organ.distinct_count, catalog.distinct_count) == (None, None)
    # This fixture's Organ counts (16,841 + 5,893) exceed its sample_count, so the numbers settle nothing either way.
    assert organ.values_complete is None
    # Nothing is listed for Catalog#, so there is nothing to qualify.
    assert catalog.values_complete is None


def test_a_complete_value_set_reads_as_complete(harness):
    sex = _only_attribute(harness, SEX)

    assert (sex.distinct_count, len(sex.top_values)) == (8, 8)
    assert sex.values_complete is True
    # Because the set is closed, the counts answer the question the agent got wrong: F + Female = 3,073.
    by_value = dict(zip(sex.top_values, sex.top_counts))
    assert by_value["F"] + by_value["Female"] == 3073


def test_a_truncated_value_list_is_not_complete(harness):
    organ = _only_attribute(harness, ORGAN)

    assert (organ.distinct_count, len(organ.top_values)) == (106, gc.TOP_VALUES_MAX)
    assert organ.values_complete is False
    # What the list alone would give for lung is 20,202 against a truth of 22,734: LUNG is the value it cannot see.
    by_value = dict(zip(organ.top_values, organ.top_counts))
    assert sum(n for v, n in by_value.items() if v.lower() == "lung") == 20202
    assert "LUNG" not in by_value


def test_the_wrong_field_is_settled_by_a_complete_value_set(harness):
    strain = _only_attribute(harness, STRAIN)

    assert strain.values_complete is True
    # Every value Strain takes is on the page and none of them is mTB, so the field is wrong, not the predicate.
    assert not any("mtb" in value.lower() for value in strain.top_values)
    assert sum(strain.top_counts) == strain.sample_count == 19


@pytest.mark.parametrize("distinct, listed, expected", [
    (8, 8, True),               # every value is listed
    (10, 10, True),             # the cap is reached and the cap is the whole set
    (11, 10, False),            # one value is missing, which is enough to break a value-set predicate
    (106, 10, False),           # T_TIS.Organ: the proposal's "top ten of sixty-two", measured as ten of 106
    (0, 0, None),               # nothing listed, nothing to qualify
    (3, 5, None),               # the count contradicts the list: claim nothing
])
def test_the_distinct_count_decides_when_the_catalog_carries_one(distinct, listed, expected):
    row = _attr(distinct_count=distinct, top_values=_values(listed), top_counts=(1,) * listed,
                sample_count=max(listed, 1))

    assert row.values_complete is expected


@pytest.mark.parametrize("sample_count, values, counts, expected", [
    # T_MUS.Sex: the listed counts total every sample holding a value, so no other value exists.
    (SEX["sample_count"], 8, tuple(SEX["top_counts"]), True),
    # T_TIS.Organ: 63,045 of 95,630, so values are missing even without a distinct count to say so.
    (ORGAN["sample_count"], 10, tuple(ORGAN["top_counts"]), False),
    (19, 2, (), None),                            # values with no counts settle nothing
    (19, 3, (6, 3), None),                        # fewer counts than values: the list cannot be totalled
    (None, 2, (6, 3), None),                      # no sample count
    (5, 2, (6, 3), None),                         # the counts exceed the sample count: claim nothing
])
def test_without_a_distinct_count_the_sample_counts_settle_it(sample_count, values, counts, expected):
    row = _attr(sample_count=sample_count, top_values=_values(values), top_counts=counts)

    assert row.values_complete is expected


@pytest.mark.parametrize("kw", [
    {"top_values": None, "top_counts": None},                                 # a row built with nothing
    {"top_values": ("F", "M"), "top_counts": (2913, None)},                   # a count that is not a number
    {"top_values": ("F",), "top_counts": ("many",), "sample_count": "19"},    # counts that are not numbers
    {"top_values": ("F",), "distinct_count": "eight"},                        # a distinct count that is not a number
])
def test_a_malformed_row_is_unknown_and_never_an_exception(kw):
    # This is read while a prompt is being built, so a bad statistic must cost the note, not the turn.
    assert _attr(**kw).values_complete is None


def test_completeness_is_derived_so_the_row_keeps_its_identity():
    # A field would have to be kept in step with the values by everything that builds an AttributeRow; a property
    # cannot drift, and equality (which test_type_details_carry_the_admin_form asserts in full) is unaffected.
    names = [f.name for f in dataclasses.fields(gc.AttributeRow)]
    assert "distinct_count" in names and "values_complete" not in names
    assert _attr(distinct_count=8, top_values=_values(8)) == _attr(distinct_count=8, top_values=_values(8))


# --- vocabulary -----------------------------------------------------------------------------------------------------


def test_vocabulary_reads_every_source(harness):
    vocab = gc.get_vocabulary(cfg())

    assert isinstance(vocab, gc.Vocabulary)
    assert vocab.investigation_titles == ("GBM_BTC", "MetNet")
    assert vocab.project_titles == ("Project A", "Project B")
    assert vocab.study_titles == ("Study 1", "Study 2")
    assert vocab.published_studies == ({"title": "Study 1", "doi": "10.1000/example", "pmid": ""},)
    assert vocab.assay_titles == ("Flow Cytometry", "Short Read Sequencing")
    assert vocab.protocol_titles == ("Dissection", "RNA prep")
    assert vocab.assay_connections == (
        {"assay": "Flow Cytometry", "parent_type": "TIS", "child_type": "D.FLOW"},
        {"assay": "Short Read Sequencing", "parent_type": "CEL", "child_type": "D.SEQ"},
        {"assay": "Short Read Sequencing", "parent_type": "TIS", "child_type": "D.SEQ"},
    )


def test_vocabulary_lives_an_hour(harness, clock):
    gc.get_vocabulary(cfg())
    reads = [n for n in harness.graph.names() if n.startswith("VOCAB_")]
    assert sorted(reads) == sorted(n for n in STATEMENT_NAMES if n.startswith("VOCAB_"))

    clock[0] += gc.VOCAB_TTL_S - 1
    gc.get_vocabulary(cfg())
    assert [n for n in harness.graph.names() if n.startswith("VOCAB_")] == reads

    clock[0] += 1
    gc.get_vocabulary(cfg())
    assert len([n for n in harness.graph.names() if n.startswith("VOCAB_")]) == 2 * len(reads)


def test_a_failed_vocabulary_source_is_empty_and_retried_after_the_failure_window(harness, clock):
    harness.graph.fail_on = {"VOCAB_EDGES"}
    vocab = gc.get_vocabulary(cfg())

    assert vocab.assay_titles == () and vocab.protocol_titles == () and vocab.assay_connections == ()
    assert vocab.investigation_titles == ("GBM_BTC", "MetNet")
    # the snapshot is not poisoned by a vocabulary failure
    assert gc.get_snapshot(cfg()).catalog_hash == "h1"

    harness.graph.fail_on = set()
    clock[0] += gc.FAILURE_MEMORY_S - 1
    assert gc.get_vocabulary(cfg()).assay_titles == ()
    clock[0] += 1
    assert gc.get_vocabulary(cfg()).assay_titles == ("Flow Cytometry", "Short Read Sequencing")


def test_vocabulary_raises_when_the_catalog_is_unavailable(harness):
    harness.graph.meta["schema_version"] = "1.0"
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_vocabulary(cfg())
    assert not [n for n in harness.graph.names() if n.startswith("VOCAB_")]


# --- read-only transactions -----------------------------------------------------------------------------------------


def test_every_statement_runs_in_a_read_transaction_with_a_timeout(harness, clock):
    gc.get_snapshot(cfg())
    gc.get_type_details(cfg(), ["TIS", "D.SEQ"])
    gc.get_vocabulary(cfg())
    clock[0] += gc.HASH_RECHECK_S
    gc.get_snapshot(cfg())

    graph = harness.graph
    assert set(graph.names()) == set(STATEMENT_NAMES)
    for call in graph.calls:
        assert call["via"] == "execute_read"
        assert call["timeout"] == gc.QUERY_TIMEOUT_S
        assert call["mode"] == "READ"
        assert call["database"] == "neo4j"
    assert all(s.get("default_access_mode") == "READ" for s in graph.sessions)
    assert graph.violations == []


# --- cache_state ----------------------------------------------------------------------------------------------------


def test_cache_state_makes_no_call(harness, clock):
    state = gc.cache_state(cfg())
    assert state["state"] == "unread"
    assert harness.factory_calls == []

    gc.get_snapshot(cfg())
    calls = len(harness.graph.calls)
    state = gc.cache_state(cfg())
    assert state["state"] == "live"
    assert state["schema_version"] == "1.1"
    assert state["catalog_hash"] == "h1"
    assert state["types"] == 4
    assert len(harness.graph.calls) == calls
    json.dumps(state)  # loggable as it is

    harness.graph.has_meta = False
    clock[0] += gc.HASH_RECHECK_S
    with pytest.raises(gc.CatalogUnavailable):
        gc.get_snapshot(cfg())
    state = gc.cache_state(cfg())
    assert state["state"] == "unavailable"
    assert "GraphMeta" in state["failure"]

    assert gc.cache_state(cfg(None))["state"] == "unconfigured"


def test_reset_cache_closes_the_drivers(harness):
    gc.get_snapshot(cfg())
    gc.reset_cache()
    assert harness.drivers[0].closed is True
    assert gc.cache_state(cfg())["state"] == "unread"


# --- ChatConfig: nothing at construction, nothing on disk -----------------------------------------------------------


def _tree_state(root: Path) -> list[tuple]:
    """Every file and directory under ``root`` with its size, mtime and content hash."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames):
            p = Path(dirpath) / name
            entries.append((str(p.relative_to(root)), "dir", p.stat().st_mtime_ns))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            st = p.stat()
            entries.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns,
                            hashlib.sha256(p.read_bytes()).hexdigest()))
    return sorted(entries)


@pytest.fixture
def built_config(tmp_path, monkeypatch, harness):
    """A real ChatConfig over a copy of the committed context directory, with every DB and LLM dial patched."""
    ctx = tmp_path / "context"
    shutil.copytree(CONTEXT, ctx)
    monkeypatch.setenv("NEXTSEEK_MODE", "gcp")
    for name in ("SEMANTIC_SHORTLIST_ENABLED", "SEMANTIC_ENDPOINTS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("chat_nextseek.config.build_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(ChatConfig, "_build_secondary_clients", lambda self: {})
    monkeypatch.setattr(ChatConfig, "_connect_db", lambda self, env="dev": None)
    monkeypatch.setattr(ChatConfig, "_fetch_context_files_from_db", lambda self, env="prod": {})
    monkeypatch.setattr(ChatConfig, "_load_name_to_id_from_db", lambda self, table, env="prod": {})
    before = _tree_state(ctx)
    config = ChatConfig({
        "CONTEXT_DIR": str(ctx),
        "CATALOG_FILE": str(paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json"),
        "GCP_API_KEY": "dummy",
        "NEO4J_URI": URI_A,
        "NEO4J_DATABASE": "neo4j",
        "NEO4J_PASSWORD": "not-a-secret",
        "API_SCHEMA": {},
        # get_config_snapshot reads these; an unset environment value leaves the attribute missing altogether.
        "NEXTSEEK_BASE_URL": "http://127.0.0.1:8000", "API_USER": "", "API_PASS": "",
        "MYSQL_HOST_PROD": "", "MYSQL_HOST_DEV": "", "MYSQL_USER": "",
        "MYSQL_PROD_PASSWORD": "", "MYSQL_DEV_PASSWORD": "",
    })
    return types.SimpleNamespace(config=config, ctx=ctx, before=before)


def test_constructing_a_chat_config_touches_no_graph(built_config, harness):
    assert harness.factory_calls == []
    assert harness.graph.calls == []


def test_the_catalog_reader_writes_nothing_under_the_context_dir(built_config):
    config = built_config.config
    snap = gc.get_snapshot(config)
    gc.get_type_details(config, [r.title for r in snap.index])
    gc.get_vocabulary(config)

    assert _tree_state(built_config.ctx) == built_config.before


def test_the_committed_graph_json_is_the_fallback(built_config):
    config = built_config.config
    for attr, name in (("NEO4J_SCHEMA", "neo4j_schema.json"),
                       ("PROTOCOL_SCHEMA", "neo4j_protocol_schema.json"),
                       ("ASSAY_SAMPLE_CONNECTIONS", "neo4j_assay-sample-conn.json")):
        assert getattr(config, attr) == json.loads((CONTEXT / name).read_text(encoding="utf-8")), attr


def test_the_schema_fetchers_are_gone():
    for name in ("_fetch_neo4j_schema", "_ensure_neo4j_schema", "_ensure_schema_file",
                 "_fetch_assay_sample_connections", "_ensure_assay_sample_connections",
                 "_fetch_protocol_schema", "_ensure_protocol_schema"):
        assert not hasattr(ChatConfig, name), name
    assert hasattr(ChatConfig, "_is_today")  # the context export still uses it


def test_config_snapshot_reports_the_catalog_without_a_call(built_config, harness):
    config = built_config.config
    snapshot = config.get_config_snapshot()
    assert snapshot["graph_catalog"]["state"] == "unread"
    assert harness.factory_calls == [] and harness.graph.calls == []

    gc.get_snapshot(config)
    calls = len(harness.graph.calls)
    snapshot = config.get_config_snapshot()
    assert snapshot["graph_catalog"]["state"] == "live"
    assert snapshot["graph_catalog"]["catalog_hash"] == "h1"
    assert len(harness.graph.calls) == calls
