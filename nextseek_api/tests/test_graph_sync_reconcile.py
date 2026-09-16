"""The nightly targeted sync (nextseek_api/graph_sync/reconcile.py; the sync design, section 10.3).

No MySQL and no Neo4j: every step the reconcile drives is replaced by a recorder, so each test says what the run did,
in what order and with which arguments. The one statement the reconcile sends itself is the GraphMeta read, which a
fake driver answers and which fails the test for any other statement. The run records and the outbox use the dmac
tables in the SQLite test database.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

import pytest

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import drift, reconcile, run, sources, state, targeted, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun

DB = "neo4j"
T0 = datetime(2026, 9, 15, 2, 0, tzinfo=dt_timezone.utc)
CAT = run.Catalog(sample_types=[], attributes=[], type_titles={26: "TIS"}, value_types={26: {"Organ": "string"}})


class FakeDriver:
    """Answers the GraphMeta read and fails on every other statement."""

    def __init__(self, schema_version="1.2"):
        self.version = schema_version
        self.calls = []

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        self.calls.append(query)
        if query != q.READ_GRAPHMETA:
            raise AssertionError(f"the reconcile sent a statement of its own: {query.strip()[:120]}")
        records = [{"props": {"schema_version": self.version, "catalog_hash": "cat-0"}}] if self.version else []
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace()))


def _detection(changed=(), missing=(), extra=(), new_uuids=(), mysql_samples=100, graph_samples=None,
               max_id=None, max_updated_at=None) -> dict:
    """What ``drift.detect_sample_drift`` returns, in its uncapped form (``cap=None``)."""
    changed, missing, extra, new = list(changed), list(missing), list(extra), list(new_uuids)
    return {"mysql_samples": mysql_samples,
            "graph_samples": mysql_samples if graph_samples is None else graph_samples,
            "changed": len(changed), "changed_ids": changed, "changed_without_hash": 0,
            "missing_in_graph": len(missing), "missing_in_graph_ids": missing,
            "not_in_mysql": len(extra), "not_in_mysql_ids": extra,
            "new_uuids": len(new), "new_uuid_list": new,
            "untyped": 0, "max_id": max_id, "max_updated_at": max_updated_at, "id_cap": None}


def _answer(preset, default):
    """What a fake step returns: the preset, the next of a preset list, or the default; an exception is raised."""
    value = preset.pop(0) if isinstance(preset, list) and preset else preset
    if isinstance(value, BaseException):
        raise value
    return default if value is None or isinstance(value, list) else value


@pytest.fixture
def steps(monkeypatch):
    """Every step the reconcile drives, recorded in ``calls`` in the order they ran.

    A test sets what a step returns (or raises) through the field named after it, and reads the arguments it was
    called with from its recorded call.
    """
    rec = SimpleNamespace(calls=[], detection=_detection(), naming=[],
                          catalog=None, small=None, relabel=None, sync=None, retire=None)

    def catalog_sync(driver, db, dry_run=False, *, record=True, trigger="command", **kwargs):
        rec.calls.append(SimpleNamespace(name="catalog", dry_run=dry_run, record=record, trigger=trigger))
        return _answer(rec.catalog, {"mode": "catalog", "status": "ok", "sample_types": 3})

    def build_catalog():
        rec.calls.append(SimpleNamespace(name="build_catalog"))
        return CAT

    def sync_small_tables(driver, db, **kwargs):
        rec.calls.append(SimpleNamespace(name="small_tables"))
        return _answer(rec.small, {"status": "ok", "projects_written": 2})

    def relabel_for_maps(driver, db, *, apply_label_changes=False, **kwargs):
        rec.calls.append(SimpleNamespace(name="relabel", apply_label_changes=apply_label_changes))
        return _answer(rec.relabel, {"status": "ok", "maps_changed": False})

    def detect_sample_drift(driver, db, *, chunk=None, cap=None, cat=None):
        rec.calls.append(SimpleNamespace(name="detect", chunk=chunk, cap=cap, cat=cat))
        return json.loads(json.dumps(rec.detection))

    def sync_samples(driver, db, ids, *, run_dir=None, apply_label_changes=False, chunk=None, **kwargs):
        rec.calls.append(SimpleNamespace(name="sync_samples", ids=list(ids), run_dir=run_dir, chunk=chunk,
                                         apply_label_changes=apply_label_changes))
        return _answer(rec.sync, {"status": "ok", "requested": len(list(ids)), "samples_written": len(list(ids))})

    def retire_samples(driver, db, ids, *, run_dir=None, **kwargs):
        rec.calls.append(SimpleNamespace(name="retire", ids=list(ids), run_dir=run_dir))
        return _answer(rec.retire, {"status": "ok", "retired_deleted": len(list(ids))})

    def samples_naming(uuids, chunk=5000):
        rec.calls.append(SimpleNamespace(name="samples_naming", uuids=list(uuids), chunk=chunk))
        return list(rec.naming)

    monkeypatch.setattr(run, "catalog_sync", catalog_sync)
    monkeypatch.setattr(run, "build_catalog", build_catalog)
    monkeypatch.setattr(targeted, "sync_small_tables", sync_small_tables)
    monkeypatch.setattr(targeted, "relabel_for_maps", relabel_for_maps)
    monkeypatch.setattr(targeted, "sync_samples", sync_samples)
    monkeypatch.setattr(targeted, "retire_samples", retire_samples)
    monkeypatch.setattr(drift, "detect_sample_drift", detect_sample_drift)
    monkeypatch.setattr(sources, "samples_naming", samples_naming)
    return rec


def _names(steps) -> list[str]:
    return [c.name for c in steps.calls]


def _one(steps, name):
    (call,) = [c for c in steps.calls if c.name == name]
    return call


def _reconcile(tmp_path, driver=None, **kwargs):
    kwargs.setdefault("run_dir", str(tmp_path / "reconcile-1"))
    kwargs.setdefault("record", False)
    return reconcile.reconcile(driver if driver is not None else FakeDriver(), DB, **kwargs)


# --- the order of the run ------------------------------------------------------------------------

def test_runs_every_step_in_the_designs_order(steps, tmp_path):
    steps.detection = _detection(changed=[11], missing=[12], extra=[9], new_uuids=["TIS-220119FLY-1"])
    steps.naming = [7]
    result = _reconcile(tmp_path)

    assert result["status"] == "ok"
    assert _names(steps) == ["catalog", "small_tables", "relabel", "build_catalog", "detect",
                             "sync_samples", "retire", "samples_naming", "sync_samples"]
    assert result["mode"] == "reconcile"
    assert result["schema_version"] == writer.SCHEMA_VERSION == "1.2"
    assert set(result["steps"]) == {"catalog", "small_tables", "relabel", "samples", "retire", "new_parents"}
    assert result["timings_s"]["detection"] >= 0


def test_syncs_the_changed_and_missing_ids_and_retires_the_extra_ones(steps, tmp_path):
    steps.detection = _detection(changed=[11, 5], missing=[12], extra=[9, 3])
    run_dir = str(tmp_path / "nightly")
    result = _reconcile(tmp_path, run_dir=run_dir, chunk=7)

    sync = _one(steps, "sync_samples")
    assert (sync.ids, sync.run_dir, sync.chunk) == ([5, 11, 12], run_dir, 7)
    retire = _one(steps, "retire")
    assert (retire.ids, retire.run_dir) == ([3, 9], run_dir)
    assert (result["samples_to_sync"], result["samples_to_retire"]) == (3, 2)
    assert result["steps"]["samples"]["samples_written"] == 3
    assert result["steps"]["retire"]["retired_deleted"] == 2


def test_nothing_differs_so_no_sample_is_written(steps, tmp_path):
    result = _reconcile(tmp_path)

    assert result["status"] == "ok"
    assert [n for n in _names(steps) if n in ("sync_samples", "retire", "samples_naming")] == []
    assert (result["samples_to_sync"], result["samples_to_retire"]) == (0, 0)
    assert result["new_parent_pass"] == {"status": "none", "new_uuids": 0, "cap": reconcile.NEW_UUID_CAP}


def test_the_detection_keeps_every_id_and_uses_the_catalog_the_catalog_sync_left(steps, tmp_path):
    _reconcile(tmp_path, chunk=9)

    detect = _one(steps, "detect")
    assert (detect.chunk, detect.cap) == (9, None)
    assert detect.cat is CAT
    names = _names(steps)
    assert names.index("catalog") < names.index("build_catalog") < names.index("detect")


def test_passes_the_operators_label_approval_to_the_steps_that_write_labels(steps, tmp_path):
    steps.detection = _detection(changed=[11])
    _reconcile(tmp_path, apply_label_changes=True)

    assert _one(steps, "relabel").apply_label_changes is True
    assert _one(steps, "sync_samples").apply_label_changes is True


def test_without_the_approval_no_step_writes_a_label_change(steps, tmp_path):
    steps.detection = _detection(changed=[11])
    _reconcile(tmp_path)

    assert _one(steps, "relabel").apply_label_changes is False
    assert _one(steps, "sync_samples").apply_label_changes is False


# --- the 20 percent guard ------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_guard_stops_before_any_sample_write_and_schedules_a_full_sync(steps, tmp_path):
    steps.detection = _detection(changed=[1, 2], extra=[3], new_uuids=["TIS-220119FLY-1"],
                                 mysql_samples=10, graph_samples=10)
    result = _reconcile(tmp_path)

    assert result["status"] == "guard_tripped"
    assert result["guard_tripped"] is True
    assert (result["differing"], result["differing_fraction"]) == (3, 0.3)
    assert [n for n in _names(steps) if n in ("sync_samples", "retire", "samples_naming")] == []
    assert result["full_sync_enqueued"] is True
    row = GraphSyncOutbox.objects.get(kind="full")
    assert row.key.startswith("slot:") and row.key.endswith(reconcile.GUARD_SLOT_SUFFIX)
    assert row.done_at is None


def test_the_guard_does_not_trip_at_the_fraction_itself(steps, tmp_path):
    steps.detection = _detection(changed=[1, 2], mysql_samples=10, graph_samples=10)
    result = _reconcile(tmp_path)

    assert result["status"] == "ok"
    assert result["guard_tripped"] is False
    assert _one(steps, "sync_samples").ids == [1, 2]


def test_the_guard_fraction_is_the_callers(steps, tmp_path):
    steps.detection = _detection(changed=[1], mysql_samples=10, graph_samples=10)
    assert _reconcile(tmp_path, guard_fraction=0.05)["status"] == "guard_tripped"
    assert _reconcile(tmp_path, guard_fraction=0.5)["status"] == "ok"


def test_a_graph_holding_nodes_mysql_lost_trips_the_guard(steps, tmp_path):
    steps.detection = _detection(extra=[1, 2, 3], mysql_samples=0, graph_samples=3)
    result = _reconcile(tmp_path)

    assert result["status"] == "guard_tripped"
    assert result["differing_fraction"] == 1.0


def test_the_guard_slot_key_is_a_dated_full_sync_slot():
    key = reconcile.guard_slot_key(T0)
    assert key == "slot:2026-09-15-guard"
    state.check_item("full", key)   # raises unless the drain can parse it


# --- the new-parent pass -------------------------------------------------------------------------

def test_the_new_parent_pass_syncs_the_old_rows_naming_a_new_uuid(steps, tmp_path):
    steps.detection = _detection(changed=[11], new_uuids=["TIS-220119FLY-1", "TIS-220119FLY-2"])
    steps.naming = [11, 4, 8]      # 11 was synced already
    result = _reconcile(tmp_path, chunk=3)

    naming = _one(steps, "samples_naming")
    assert (naming.uuids, naming.chunk) == (["TIS-220119FLY-1", "TIS-220119FLY-2"], 3)
    assert [c.ids for c in steps.calls if c.name == "sync_samples"] == [[11], [4, 8]]
    assert result["new_parent_pass"] == {"status": "ok", "new_uuids": 2, "cap": reconcile.NEW_UUID_CAP,
                                         "naming_samples": 3, "to_sync": 2, "ids": [4, 8]}


def test_above_the_new_uuid_cap_the_pass_is_skipped_and_reported(steps, tmp_path):
    steps.detection = _detection(changed=[11], new_uuids=["a-1", "a-2", "a-3"])
    result = _reconcile(tmp_path, new_uuid_cap=2)

    assert [n for n in _names(steps) if n == "samples_naming"] == []
    assert [c.ids for c in steps.calls if c.name == "sync_samples"] == [[11]]
    assert result["status"] == "ok"
    pass_report = result["new_parent_pass"]
    assert (pass_report["status"], pass_report["new_uuids"], pass_report["cap"]) == ("skipped", 3, 2)
    assert "full sync" in pass_report["reason"]


def test_no_old_row_names_a_new_uuid_so_nothing_more_is_synced(steps, tmp_path):
    steps.detection = _detection(changed=[11], new_uuids=["a-1"])
    steps.naming = []
    result = _reconcile(tmp_path)

    assert [c.ids for c in steps.calls if c.name == "sync_samples"] == [[11]]
    assert result["new_parent_pass"]["to_sync"] == 0
    assert "new_parents" not in result["steps"]


# --- dry runs ------------------------------------------------------------------------------------

def test_a_dry_run_writes_nothing_and_reports_what_it_would_do(steps, tmp_path):
    steps.detection = _detection(changed=[11], extra=[9], new_uuids=["a-1"])
    steps.naming = [4]
    run_dir = tmp_path / "dry"
    result = _reconcile(tmp_path, run_dir=str(run_dir), dry_run=True)

    assert result["status"] == "dry_run"
    assert _names(steps) == ["build_catalog", "detect", "samples_naming"]
    assert (result["samples_to_sync"], result["samples_to_retire"]) == (1, 1)
    assert result["new_parent_pass"]["to_sync"] == 1
    assert result["steps"] == {}
    assert not run_dir.exists()


@pytest.mark.django_db
def test_a_dry_run_records_no_run_and_schedules_no_full_sync(steps, tmp_path):
    steps.detection = _detection(changed=[1, 2, 3], mysql_samples=4, graph_samples=4)
    result = _reconcile(tmp_path, dry_run=True, record=True)

    assert (result["status"], result["guard_tripped"]) == ("dry_run", True)
    assert not GraphSyncRun.objects.exists()
    assert not GraphSyncOutbox.objects.exists()


# --- refusals ------------------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("version", ["1.1", None])
def test_refuses_a_graph_not_at_the_writers_version_and_does_nothing(steps, tmp_path, version):
    result = _reconcile(tmp_path, driver=FakeDriver(version), record=True)

    assert result["status"] == "not_at_version"
    assert result["schema_version"] == version
    assert result["writer_version"] == writer.SCHEMA_VERSION
    assert steps.calls == []
    assert GraphSyncRun.objects.get(kind="reconcile").status == "refused"


def test_a_catalog_sync_that_refuses_stops_the_run(steps, tmp_path):
    steps.catalog = run.PreflightError(["the graph-write lock was not acquired within 60 s"], {"mode": "catalog"})
    result = _reconcile(tmp_path)

    assert result["status"] == "refused"
    assert result["problems"] == ["the graph-write lock was not acquired within 60 s"]
    assert result["stopped_at"] == "catalog"
    assert _names(steps) == ["catalog"]


@pytest.mark.parametrize("field, stopped, ran", [
    ("small", "small_tables", ["catalog", "small_tables"]),
    ("relabel", "relabel", ["catalog", "small_tables", "relabel"]),
    ("sync", "samples", ["catalog", "small_tables", "relabel", "build_catalog", "detect", "sync_samples"]),
])
def test_a_step_that_cannot_take_the_lock_stops_the_run(steps, tmp_path, field, stopped, ran):
    steps.detection = _detection(changed=[11], extra=[9], new_uuids=["a-1"])
    setattr(steps, field, {"status": "lock_timeout", "lock_timeout_s": 60})
    result = _reconcile(tmp_path)

    assert result["status"] == "lock_timeout"
    assert result["stopped_at"] == stopped
    assert result["lock_timeout_s"] == 60
    assert _names(steps) == ran


@pytest.mark.parametrize("kwargs", [{"chunk": 0}, {"guard_fraction": -0.1}, {"guard_fraction": 1.5},
                                    {"new_uuid_cap": -1}])
def test_refuses_impossible_arguments_before_reading_anything(steps, tmp_path, kwargs):
    with pytest.raises(ValueError):
        _reconcile(tmp_path, **kwargs)
    assert steps.calls == []


# --- the run record and the report ---------------------------------------------------------------

@pytest.mark.django_db
def test_records_the_run_with_the_watermark_it_saw(steps, tmp_path):
    steps.detection = _detection(changed=[11], max_id=1308453, max_updated_at="2026-09-14T22:10:00")
    result = _reconcile(tmp_path, record=True, trigger="loop")

    row = GraphSyncRun.objects.get(kind="reconcile")
    assert row.status == "ok"
    assert row.watermark_to == "id=1308453 updated_at=2026-09-14T22:10:00"
    assert row.counts_json["trigger"] == "loop"
    assert row.counts_json["changed"] == 1
    assert row.counts_json["samples_to_sync"] == 1
    assert row.counts_json["new_parent_pass"]["status"] == "none"
    assert (result["max_id"], result["max_updated_at"]) == (1308453, "2026-09-14T22:10:00")


@pytest.mark.django_db
def test_the_catalog_sync_inherits_the_reconciles_record_and_trigger(steps, tmp_path):
    _reconcile(tmp_path, record=True, trigger="loop")
    catalog = _one(steps, "catalog")
    assert (catalog.record, catalog.trigger, catalog.dry_run) == (True, "loop", False)


@pytest.mark.django_db
def test_no_record_writes_no_run_row(steps, tmp_path):
    _reconcile(tmp_path, record=False)
    assert not GraphSyncRun.objects.exists()


def test_writes_its_report_into_the_run_dir(steps, tmp_path):
    run_dir = tmp_path / "nightly"
    result = _reconcile(tmp_path, run_dir=str(run_dir))

    saved = json.loads((run_dir / reconcile.REPORT_FILE).read_text(encoding="utf-8"))
    assert saved["status"] == result["status"] == "ok"
    assert saved["mode"] == "reconcile"
    assert saved["run_dir"] == str(run_dir)
    assert saved["started_at"] and saved["finished_at"]


@pytest.mark.django_db
def test_an_error_records_a_failed_run_writes_the_report_and_raises(steps, tmp_path):
    steps.small = OSError("Neo4j went away")
    run_dir = tmp_path / "nightly"
    with pytest.raises(OSError):
        _reconcile(tmp_path, run_dir=str(run_dir), record=True)

    assert GraphSyncRun.objects.get(kind="reconcile").status == "failed"
    saved = json.loads((run_dir / reconcile.REPORT_FILE).read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert "Neo4j went away" in saved["error"]


def test_the_report_caps_the_id_lists_but_not_the_counts(steps, tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "LIST_CAP", 2)
    steps.detection = _detection(changed=[1, 2, 3, 4], new_uuids=["a-1", "a-2", "a-3"])
    result = _reconcile(tmp_path)

    assert result["detection"]["changed"] == 4
    assert result["detection"]["changed_ids"] == [1, 2]
    assert result["detection"]["new_uuid_list"] == ["a-1", "a-2"]
    assert result["detection"]["id_cap"] == 2
    assert _one(steps, "sync_samples").ids == [1, 2, 3, 4]   # every changed id is synced
