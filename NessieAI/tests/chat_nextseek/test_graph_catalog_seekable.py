"""graph_catalog.get_seekable: the range indexes the graph reviewer seeks a value by, read like the rest of the catalog.

A fake driver answers the catalog's own statements (META, INDEX, GUARD) and SHOW INDEXES; no network.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_nextseek import graph_catalog as gc
from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope

INDEXES = [
    {"labelsOrTypes": ["T_A_ALN"], "properties": ["DataType"]},
    {"labelsOrTypes": ["T_A_ALN"], "properties": ["Aligner"]},
    {"labelsOrTypes": ["T_PAT"], "properties": ["Sex"]},
    {"labelsOrTypes": ["Sample"], "properties": ["uuid"]},           # not a type label: left out
    {"labelsOrTypes": ["T_PAT"], "properties": ["Sex", "Race"]},     # composite: an equality on one is no seek
]


class _Graph:
    def __init__(self):
        self.catalog_hash = "h1"
        self.indexes = [dict(r) for r in INDEXES]
        self.fail_show = False
        self.statements: list[str] = []

    def rows(self, text):
        self.statements.append(text)
        if text == gc.META:
            return [{"schema_version": "1.2", "catalog_hash": self.catalog_hash, "synced_at": None,
                     "has_usage": False}]
        if text == gc.INDEX:
            return [{"title": "A.ALN", "label": "T_A_ALN", "name": "Sequence Alignment Analysis", "clade": None,
                     "sample_count": 91323, "deprecated": False, "attributes_with_values": 2}]
        if text == gc.GUARD:
            return [{"label": "T_A_ALN", "titles": ["DataType", "Aligner"]}]
        if text == gc.SEEKABLE:
            if self.fail_show:
                raise RuntimeError("SHOW INDEXES is not allowed")
            return [dict(r) for r in self.indexes]
        raise AssertionError(f"unexpected statement: {text!r}")


class _Session:
    def __init__(self, graph):
        self.graph = graph

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, fn, *args, **kwargs):
        return fn(SimpleNamespace(run=lambda text, params=None, **k: iter(self.graph.rows(text))), *args, **kwargs)


class _Driver:
    def __init__(self, graph):
        self.graph = graph

    def session(self, **kwargs):
        return _Session(self.graph)

    def close(self):
        pass


@pytest.fixture
def graph(monkeypatch):
    gc.reset_cache()
    g = _Graph()
    monkeypatch.setattr(gc, "_make_driver", lambda config: _Driver(g))
    clock = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(gc, "_now", lambda: clock.t)
    g.clock = clock
    yield g
    gc.reset_cache()


def _cfg(scope):
    return SimpleNamespace(NEO4J_URI="bolt://graph:7687", NEO4J_PASSWORD="p", NEO4J_DATABASE="neo4j",
                           **{SCOPE_ATTR: scope})


def test_each_type_label_maps_to_its_single_property_range_indexes(graph):
    got = gc.get_seekable(_cfg(GraphScope.for_projects([1], source="t")))
    assert dict(got) == {"T_A_ALN": frozenset({"DataType", "Aligner"}), "T_PAT": frozenset({"Sex"})}


def test_the_map_is_the_same_for_every_caller_and_read_once(graph):
    member = gc.get_seekable(_cfg(GraphScope.for_projects([1], source="t")))
    admin = gc.get_seekable(_cfg(GraphScope.admin("t")))
    assert member == admin
    assert graph.statements.count(gc.SEEKABLE) == 1


def test_a_new_catalog_hash_reads_it_again(graph):
    cfg = _cfg(GraphScope.admin("t"))
    gc.get_seekable(cfg)
    graph.catalog_hash = "h2"
    graph.indexes = [{"labelsOrTypes": ["T_A_ALN"], "properties": ["DataType"]}]
    graph.clock.t += gc.HASH_RECHECK_S + 1
    assert dict(gc.get_seekable(cfg)) == {"T_A_ALN": frozenset({"DataType"})}


def test_a_failed_read_is_none_and_leaves_the_catalog_usable(graph):
    graph.fail_show = True
    cfg = _cfg(GraphScope.admin("t"))
    assert gc.get_seekable(cfg) is None
    assert gc.get_snapshot(cfg).guard["T_A_ALN"] == frozenset({"DataType", "Aligner"})
    assert gc.cache_state(cfg).get("state") == "live"
    assert gc.get_seekable(cfg) is None and graph.statements.count(gc.SEEKABLE) == 1   # remembered for a while
    graph.fail_show = False
    graph.clock.t += gc.FAILURE_MEMORY_S + 1
    assert gc.get_seekable(cfg) is not None


def test_an_unavailable_catalog_is_none(monkeypatch):
    gc.reset_cache()
    monkeypatch.setattr(gc, "_make_driver", lambda config: (_ for _ in ()).throw(RuntimeError("down")))
    assert gc.get_seekable(_cfg(GraphScope.admin("t"))) is None
    gc.reset_cache()


def test_the_statement_is_read_only_schema():
    assert gc.SEEKABLE.startswith("SHOW INDEXES") and "RANGE" in gc.SEEKABLE and "ONLINE" in gc.SEEKABLE
