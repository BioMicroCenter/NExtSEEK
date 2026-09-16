"""The drift check (nextseek_api/graph_sync/drift.py; the sync design, sections 10.3 and 13, CI-4).

No Neo4j: a fake driver answers the graph's statements from a small world. MySQL's sample stream is replaced in
``sources``. The drift check's freshness and run records use the dmac tables in the SQLite test database; every time
is passed in, so no test depends on the clock.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.db import connection
from neo4j import RoutingControl

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import drift, run, sources, verify, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun
from nextseek_api.graph_sync.projection import project_sample

T0 = datetime(2026, 9, 15, 2, 30, tzinfo=dt_timezone.utc)


class FakeDriver:
    """Records ``execute_query`` calls; ``responder(query, params)`` gives each call's records."""

    def __init__(self, responder):
        self.calls = []
        self.responder = responder

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        self.calls.append(SimpleNamespace(query=query, params=params, database=database_, kwargs=kwargs))
        records = list(self.responder(query, params))
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace()))


# --- merge_digests: the pure merge of the two ordered streams ------------------------------------

def _merge(mysql, graph):
    return [(d.kind, d.id) for d in drift.merge_digests(mysql, graph)]


def test_merge_yields_nothing_for_equal_streams():
    assert _merge([(1, "a", None), (2, "b", None)], [(1, "a"), (2, "b")]) == []


def test_merge_finds_a_changed_digest_a_missing_node_and_an_extra_node():
    mysql = [(1, "a", None), (2, "b", None), (4, "d", None), (6, "f", None)]
    graph = [(2, "B"), (3, "c"), (4, "d"), (5, "e"), (7, "g")]
    assert _merge(mysql, graph) == [("missing_in_graph", 1), ("changed", 2), ("not_in_mysql", 3),
                                    ("not_in_mysql", 5), ("missing_in_graph", 6), ("not_in_mysql", 7)]


def test_merge_counts_a_node_without_a_hash_as_changed():
    (diff,) = drift.merge_digests([(1, "a", "row")], [(1, None)])
    assert (diff.kind, diff.id, diff.item, diff.graph_hash) == ("changed", 1, (1, "a", "row"), None)


def test_merge_counts_a_row_without_a_digest_as_changed():
    assert _merge([(1, None, None)], [(1, None)]) == [("changed", 1)]


@pytest.mark.parametrize("mysql, graph", [([(2, "b", None), (1, "a", None)], []),
                                          ([], [(2, "b"), (2, "b")])])
def test_merge_refuses_a_stream_out_of_id_order(mysql, graph):
    with pytest.raises(RuntimeError, match="out of id order"):
        list(drift.merge_digests(mysql, graph))


# --- detect_sample_drift ---------------------------------------------------------------------------

CAT = run.Catalog(sample_types=[], attributes=[], type_titles={26: "TIS"}, value_types={26: {"Organ": "string"}})


def _row(sample_id, uuid, projects=(2,), assays=(7,), organ="Lung", updated_at=None):
    return {"id": sample_id, "uuid": uuid, "title": f"s{sample_id}", "sample_type_id": 26,
            "json_metadata": json.dumps({"UID": uuid, "Organ": organ}), "project_ids": list(projects),
            "assay_ids": list(assays), "updated_at": updated_at}


def _hash(row):
    """The hash the projection writes on the row's node."""
    return project_sample(row, "TIS", CAT.value_types[26], row["project_ids"],
                          assay_ids=row["assay_ids"]).props["source_hash"]


class DriftGraph:
    """The graph side: ``hashes`` (id to source_hash), ``uuids`` (id to uuid) and the GraphMeta node."""

    def __init__(self, rows=(), schema_version="1.2"):
        self.hashes = {r["id"]: _hash(r) for r in rows}
        self.uuids = {r["id"]: r["uuid"] for r in rows}
        self.meta = [{"props": {"schema_version": schema_version, "catalog_hash": "c"}}] if schema_version else []

    def __call__(self, query, params):
        if query == q.SAMPLE_HASHES_PAGE:
            ids = sorted(i for i in self.hashes if i > params["after"])[:params["limit"]]
            return [{"id": i, "source_hash": self.hashes[i]} for i in ids]
        if query == drift.NODE_UUIDS:
            return [{"id": i, "uuid": self.uuids[i]} for i in params["ids"] if i in self.uuids]
        if query == drift.UUIDS_ON_NODES:
            carried = set(self.uuids.values())
            return [{"uuid": u} for u in params["uuids"] if u in carried]
        if query == q.READ_GRAPHMETA:
            return self.meta
        raise AssertionError(f"unexpected statement: {query}")


@pytest.fixture
def mysql_rows(monkeypatch):
    """MySQL's digest stream: tests fill the returned list; pages follow ``chunk``."""
    rows: list = []

    def iter_digest_rows(chunk=5000):
        ordered = sorted(rows, key=lambda r: r["id"])
        for start in range(0, len(ordered), chunk):
            yield [dict(r) for r in ordered[start:start + chunk]]

    monkeypatch.setattr(sources, "iter_digest_rows", iter_digest_rows)
    monkeypatch.setattr(writer, "HASH_PAGE", 2)   # the graph stream pages too
    return rows


def _detect(graph, **kwargs):
    kwargs.setdefault("chunk", 2)
    kwargs.setdefault("cat", CAT)
    driver = FakeDriver(graph)
    return drift.detect_sample_drift(driver, "neo4j", **kwargs), driver


def test_detect_finds_no_drift_when_every_node_carries_the_projections_hash(mysql_rows):
    mysql_rows.extend([_row(1, "TIS-220119FLY-1"), _row(2, "TIS-220119FLY-2", projects=(2, 16, 16)),
                       _row(3, "TIS-220119FLY-3", assays=())])
    result, _ = _detect(DriftGraph(mysql_rows))
    assert (result["changed"], result["missing_in_graph"], result["not_in_mysql"], result["new_uuids"]) == (0, 0, 0, 0)
    assert (result["changed_ids"], result["missing_in_graph_ids"], result["not_in_mysql_ids"]) == ([], [], [])
    assert (result["mysql_samples"], result["graph_samples"]) == (3, 3)


def test_detect_counts_changed_missing_and_extra_samples(mysql_rows):
    stored = [_row(1, "TIS-220119FLY-1"), _row(2, "TIS-220119FLY-2"), _row(4, "TIS-220119FLY-4"),
              _row(5, "TIS-220119FLY-5"), _row(9, "TIS-220119FLY-9")]
    graph = DriftGraph(stored)
    graph.hashes[5] = None                                                    # written before schema 1.2
    mysql_rows.extend([_row(1, "TIS-220119FLY-1", organ="Liver"),             # its bytes changed
                       _row(2, "TIS-220119FLY-2", projects=(2, 16)),          # a project link added
                       _row(3, "TIS-220119FLY-3"),                            # never synced
                       _row(4, "TIS-220119FLY-4"),
                       _row(5, "TIS-220119FLY-5")])                           # 9: deleted from MySQL
    result, _ = _detect(graph)
    assert (result["changed"], result["changed_ids"]) == (3, [1, 2, 5])
    assert result["changed_without_hash"] == 1
    assert (result["missing_in_graph"], result["missing_in_graph_ids"]) == (1, [3])
    assert (result["not_in_mysql"], result["not_in_mysql_ids"]) == (1, [9])
    assert (result["mysql_samples"], result["graph_samples"]) == (5, 5)


def test_detect_counts_a_sample_whose_type_is_not_in_the_catalog_as_changed(mysql_rows):
    row = _row(1, "TIS-220119FLY-1")
    graph = DriftGraph([row])
    mysql_rows.append(dict(row, sample_type_id=40))
    result, _ = _detect(graph)
    assert (result["changed_ids"], result["untyped"]) == ([1], 1)


def test_detect_reports_the_uuids_no_node_carries(mysql_rows):
    graph = DriftGraph([_row(1, "TIS-220119FLY-1"), _row(2, "TIS-220119FLY-2"), _row(5, "TIS-220119FLY-5")])
    mysql_rows.extend([
        _row(1, "TIS-220119FLY-1", organ="Liver"),   # changed, same uuid: not new
        _row(2, "TIS-220119FLY-7"),                  # changed, uuid moved: new
        _row(3, "TIS-220119FLY-3"),                  # missing: new
        _row(4, "TIS-220119FLY-5"),                  # missing, but node 5 carries its uuid: not new
        _row(5, "TIS-220119FLY-5"),
        _row(6, "TIS-220119FLY-3"),                  # a duplicate uuid counts once
    ])
    result, _ = _detect(graph)
    assert (result["new_uuids"], result["new_uuid_list"]) == (2, ["TIS-220119FLY-3", "TIS-220119FLY-7"])


def test_detect_caps_the_lists_but_not_the_counts(mysql_rows):
    mysql_rows.extend(_row(i, f"TIS-220119FLY-{i}") for i in range(1, 6))
    result, _ = _detect(DriftGraph(), cap=2)
    assert (result["missing_in_graph"], result["missing_in_graph_ids"]) == (5, [1, 2])
    assert (result["new_uuids"], len(result["new_uuid_list"])) == (5, 2)
    result, _ = _detect(DriftGraph(), cap=None)
    assert result["missing_in_graph_ids"] == [1, 2, 3, 4, 5]


def test_detect_records_the_highest_id_and_updated_at_seen(mysql_rows):
    mysql_rows.extend([_row(3, "TIS-220119FLY-3", updated_at=datetime(2026, 9, 1, 12, 0)),
                       _row(8, "TIS-220119FLY-8", updated_at=datetime(2026, 8, 1, 12, 0)),
                       _row(9, "TIS-220119FLY-9", updated_at=None)])
    result, _ = _detect(DriftGraph(mysql_rows))
    assert (result["max_id"], result["max_updated_at"]) == (9, "2026-09-01T12:00:00")


def test_detect_reads_only(mysql_rows):
    mysql_rows.extend([_row(1, "TIS-220119FLY-1", organ="Liver"), _row(3, "TIS-220119FLY-3")])
    _, driver = _detect(DriftGraph([_row(1, "TIS-220119FLY-1"), _row(2, "TIS-220119FLY-2")]))
    assert driver.calls and all(c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)


def test_detect_builds_the_catalog_when_none_is_given(mysql_rows, monkeypatch):
    built = []
    monkeypatch.setattr(run, "build_catalog", lambda: built.append(1) or CAT)
    mysql_rows.append(_row(1, "TIS-220119FLY-1"))
    result, _ = _detect(DriftGraph(mysql_rows), cat=None)
    assert built == [1] and result["changed"] == 0


# --- drift_check -----------------------------------------------------------------------------------

GATE_OK = {"checks": [{"name": "4.samples.graph_count", "expected": 1, "actual": 1, "pass": True}],
           "pass": True, "stats": {"seed": 7}}


@pytest.fixture
def gate(monkeypatch):
    """Replaces gate G; the test sets ``result`` and reads the call's arguments from ``calls``."""
    fake = SimpleNamespace(result=GATE_OK, calls=[])

    def gate_g(driver, db, sample_size=verify.SAMPLE_SIZE, **kwargs):
        fake.calls.append(dict(kwargs, sample_size=sample_size, db=db))
        return json.loads(json.dumps(fake.result))

    monkeypatch.setattr(verify, "gate_g", gate_g)
    return fake


def _fresh_runs(full_age=timedelta(days=1), reconcile_age=timedelta(hours=1)):
    for kind, age in (("full", full_age), ("reconcile", reconcile_age)):
        if age is not None:
            GraphSyncRun.objects.create(kind=kind, status="ok", started_at=T0 - age, finished_at=T0 - age)


def _check_drift(graph, **kwargs):
    kwargs.setdefault("now", T0)
    kwargs.setdefault("sample_size", 10)
    kwargs.setdefault("seed", 7)
    kwargs.setdefault("chunk", 2)
    driver = FakeDriver(graph)
    return drift.drift_check(driver, "neo4j", **kwargs), driver


def _named(result, name):
    (check,) = [c for c in result["checks"] if c["name"] == name]
    return check


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(run, "build_catalog", lambda: CAT)


@pytest.mark.django_db
def test_drift_check_is_ok_when_nothing_drifted(mysql_rows, gate, catalog):
    mysql_rows.append(_row(1, "TIS-220119FLY-1"))
    _fresh_runs()
    result, _ = _check_drift(DriftGraph(mysql_rows))
    assert (result["status"], result["pass"]) == ("ok", True)
    names = [c["name"] for c in result["checks"]]
    assert names == ["samples.missing_in_graph", "samples.not_in_mysql", "samples.source_hash_mismatch",
                     "samples.new_uuids", "freshness.full", "freshness.reconcile", "freshness.outbox",
                     "4.samples.graph_count"]
    assert all({"name", "expected", "actual", "pass"} <= set(c) for c in result["checks"])
    assert result["stats"]["gate_g"] == {"seed": 7}
    assert result["stats"]["detection"]["mysql_samples"] == 1


@pytest.mark.django_db
def test_drift_check_runs_gate_g_with_its_arguments_and_without_the_named_accounts(mysql_rows, gate, catalog):
    _fresh_runs()
    _check_drift(DriftGraph(), sample_size=25, seed=3, chunk=4)
    assert gate.calls == [{"db": "neo4j", "sample_size": 25, "seed": 3, "chunk": 4, "accounts": ()}]


@pytest.mark.django_db
def test_drift_check_reports_each_kind_of_sample_drift(mysql_rows, gate, catalog):
    graph = DriftGraph([_row(1, "TIS-220119FLY-1"), _row(9, "TIS-220119FLY-9")])
    mysql_rows.extend([_row(1, "TIS-220119FLY-1", organ="Liver"), _row(3, "TIS-220119FLY-3")])
    _fresh_runs()
    result, _ = _check_drift(graph)
    assert (result["status"], result["pass"]) == ("drift", False)
    for name, ids in (("samples.missing_in_graph", [3]), ("samples.not_in_mysql", [9]),
                      ("samples.source_hash_mismatch", [1])):
        check = _named(result, name)
        assert (check["expected"], check["actual"], check["pass"], check["detail"]) == (0, 1, False, ids)
    new = _named(result, "samples.new_uuids")
    assert (new["actual"], new["pass"], new["detail"]) == (1, True, ["TIS-220119FLY-3"])


@pytest.mark.django_db
@pytest.mark.parametrize("job, ages", [("full", {"full_age": timedelta(days=9)}),
                                       ("reconcile", {"full_age": timedelta(days=3),
                                                      "reconcile_age": timedelta(hours=27)}),
                                       ("full", {"full_age": None, "reconcile_age": timedelta(hours=1)})])
def test_a_stale_or_missing_sync_is_drift(mysql_rows, gate, catalog, job, ages):
    _fresh_runs(**ages)
    result, _ = _check_drift(DriftGraph())
    check = _named(result, f"freshness.{job}")
    assert check["pass"] is False and check["actual"] in ("stale", "never")
    assert result["status"] == "drift"


@pytest.mark.django_db
def test_a_full_sync_counts_for_the_reconcile(mysql_rows, gate, catalog):
    _fresh_runs(full_age=timedelta(hours=2), reconcile_age=None)
    result, _ = _check_drift(DriftGraph())
    assert _named(result, "freshness.reconcile")["actual"] == "ok"
    assert result["status"] == "ok"


@pytest.mark.django_db
def test_an_outbox_row_waiting_over_an_hour_is_drift(mysql_rows, gate, catalog):
    _fresh_runs()
    GraphSyncOutbox.objects.create(kind="catalog", key="*", enqueued_at=T0 - timedelta(hours=2))
    result, _ = _check_drift(DriftGraph())
    assert _named(result, "freshness.outbox")["actual"] == "stale"
    assert result["status"] == "drift"


@pytest.mark.django_db
def test_unreadable_run_records_fail_the_freshness_check(mysql_rows, gate, catalog):
    with connection.cursor() as cur:
        cur.execute('DROP TABLE "graph_sync_run"')   # rolled back with the test's transaction
    result, _ = _check_drift(DriftGraph())
    check = _named(result, "freshness.readable")
    assert check["pass"] is False and "graph_sync_run" in check["detail"]
    assert result["status"] == "drift"


@pytest.mark.django_db
def test_a_failing_gate_g_check_is_drift_under_its_own_name(mysql_rows, gate, catalog):
    _fresh_runs()
    gate.result = {"checks": [{"name": "9.lineage.labels", "expected": 0, "actual": 4, "pass": False}],
                   "pass": False, "stats": {}}
    result, _ = _check_drift(DriftGraph())
    assert _named(result, "9.lineage.labels")["actual"] == 4
    assert result["status"] == "drift"


@pytest.mark.django_db
def test_a_catalog_that_does_not_build_fails_the_detection_and_gate_g_still_runs(mysql_rows, gate, monkeypatch):
    def collide():
        raise ValueError("label collision: T_D_SEQ")

    monkeypatch.setattr(run, "build_catalog", collide)
    _fresh_runs()
    result, _ = _check_drift(DriftGraph())
    for name in ("samples.missing_in_graph", "samples.not_in_mysql", "samples.source_hash_mismatch"):
        check = _named(result, name)
        assert check["pass"] is False and "label collision" in check["detail"]
    assert len(gate.calls) == 1 and result["status"] == "drift"


@pytest.mark.django_db
@pytest.mark.parametrize("version", ["1.1", None])
def test_drift_check_refuses_a_graph_not_at_the_writers_version(mysql_rows, gate, version, monkeypatch):
    monkeypatch.setattr(sources, "iter_digest_rows", lambda chunk=5000: pytest.fail("MySQL was read"))
    result, driver = _check_drift(DriftGraph(schema_version=version))
    assert (result["status"], result["pass"], result["checks"]) == ("refused", False, [])
    assert writer.SCHEMA_VERSION in result["reason"] and repr(version) in result["reason"]
    assert [c.query for c in driver.calls] == [q.READ_GRAPHMETA]
    assert gate.calls == []


@pytest.mark.django_db
def test_drift_check_reads_only_and_records_nothing_without_a_trigger(mysql_rows, gate, catalog):
    mysql_rows.extend([_row(1, "TIS-220119FLY-1", organ="Liver"), _row(3, "TIS-220119FLY-3")])
    _fresh_runs()
    runs_before = list(GraphSyncRun.objects.values_list("id", "status"))
    result, driver = _check_drift(DriftGraph([_row(1, "TIS-220119FLY-1")]))
    assert result["status"] == "drift"
    assert driver.calls and all(c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)
    assert list(GraphSyncRun.objects.values_list("id", "status")) == runs_before
    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("version, status", [("1.2", "drift"), ("1.1", "refused")])
def test_drift_check_records_its_run_when_given_a_trigger(mysql_rows, gate, catalog, version, status):
    mysql_rows.append(_row(3, "TIS-220119FLY-3"))
    result, _ = _check_drift(DriftGraph(schema_version=version), trigger="command")
    run_row = GraphSyncRun.objects.get(kind="drift")
    assert (run_row.status, result["status"]) == (status, status)
    assert run_row.counts_json["trigger"] == "command"
    assert run_row.drift_json["status"] == status


@pytest.mark.django_db
def test_drift_check_records_a_failed_run_and_raises_when_it_cannot_complete(mysql_rows, gate, catalog):
    def broken(query, params):
        if query == q.READ_GRAPHMETA:
            return [{"props": {"schema_version": "1.2"}}]
        raise OSError("Neo4j went away")

    with pytest.raises(OSError):
        drift.drift_check(FakeDriver(broken), "neo4j", now=T0, trigger="loop")
    assert GraphSyncRun.objects.get(kind="drift").status == "failed"
