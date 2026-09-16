"""graph_sync's ordered runs, gate G and the command (nextseek_api/graph_sync/run.py, verify.py and
nextseek_api/management/commands/graph_sync.py).

No database and no Neo4j: the MySQL readers in ``sources`` are replaced by a small fixed world, the writer's
functions by recorders, and the Neo4j driver by a fake that answers each statement from the same world.
"""
from __future__ import annotations

import copy
import json
import os
from collections import Counter
from datetime import date
from importlib import import_module
from inspect import isgenerator
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from neo4j import RoutingControl
from neo4j.time import Date as Neo4jDate

from nextseek_api.graph_sync import (
    catalog, drift, loop, reconcile, run, sources, state as sync_state, targeted, verify, writer,
)
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.projection import project_sample

command = import_module("nextseek_api.management.commands.graph_sync")


class FakeDriver:
    """Records ``execute_query`` calls; ``responder(query, params)`` gives each call's records."""

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder or (lambda query, params: [])

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        self.calls.append(SimpleNamespace(query=query, params=params, database=database_, kwargs=kwargs))
        records = list(self.responder(query, params))
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace()))


# --- the world: three samples over two types and two projects ------------------------------------

U_T1, U_T2, U_D1 = "TIS-220119FLY-1", "TIS-220119FLY-2", "D.SEQ-220119FLY-3"

TYPES = [{"id": 26, "title": "TIS", "uuid": "st-26", "description": "Tissue"},
         {"id": 33, "title": "D.SEQ", "uuid": "st-33", "description": "Sequencing"}]


def _attr(attr_id, type_id, title, attr_type):
    return {"id": attr_id, "sample_type_id": type_id, "title": title, "pos": attr_id, "required": False,
            "is_title": False, "sample_attribute_type_id": attr_type, "description": None}


ATTRS = [_attr(1, 26, "Organ", 1), _attr(2, 26, "CellCount", 2), _attr(3, 26, "Collected", 3),
         _attr(4, 33, "Parent", 1)]
ATTR_TYPES = {1: {"id": 1, "title": "String", "base_type": "String", "regexp": None},
              2: {"id": 2, "title": "Real number", "base_type": "Float", "regexp": None},
              3: {"id": 3, "title": "Date", "base_type": "Date", "regexp": None}}


def _sample(sample_id, uuid, type_id, meta):
    return {"id": sample_id, "uuid": uuid, "title": f"s{sample_id}", "sample_type_id": type_id,
            "json_metadata": json.dumps(meta)}


SAMPLES = [
    _sample(10, U_T1, 26, {"UID": U_T1, "Organ": "Lung", "CellCount": "5", "Collected": "2024-01-31"}),
    _sample(11, U_D1, 33, {"UID": U_D1, "Parent": U_T1, "Lane": "3"}),      # Lane: undeclared on D.SEQ
    _sample(12, U_T2, 26, {"UID": U_T2, "Organ": "Liver", "CellCount": "lots"}),  # a failed cast
]
PROJECT_LINKS = {10: [2], 11: [2, 16, 16], 12: [16]}
MEMBERSHIPS = [
    {"person_id": 144, "project_id": 2, "has_left": False, "time_left_at": None},
    {"person_id": 300, "project_id": 16, "has_left": False, "time_left_at": None},
    {"person_id": 301, "project_id": 2, "has_left": True, "time_left_at": None},
    {"person_id": 301, "project_id": 16, "has_left": False, "time_left_at": None},
]
ACCOUNTS = {"tcgamember": (16,), "user": (2,)}
GHOSTS = {"ghost_element_ids": ["4:g:1"], "orphan_ids": [99], "unresolved_duplicate_ids": [],
          "idless_element_ids": ["4:x:2"], "duplicate_ids": 1, "sample_nodes": 6}


@pytest.fixture
def world(monkeypatch):
    """Install the fixed MySQL world into ``sources``; tests may change the returned state before a run."""
    state = {"samples": copy.deepcopy(SAMPLES), "types": copy.deepcopy(TYPES), "attrs": copy.deepcopy(ATTRS)}

    def ordered():
        return sorted(state["samples"], key=lambda r: r["id"])

    def iter_samples(chunk=5000, after_id=0):
        rows = [dict(r) for r in ordered() if r["id"] > after_id]
        for start in range(0, len(rows), chunk):
            yield rows[start:start + chunk]

    def uuid_to_ids():
        index = {}
        for row in ordered():
            index.setdefault(row["uuid"], []).append(row["id"])
        return index

    def iter_digest_rows(chunk=5000):
        rows = [dict(r, project_ids=sorted(set(PROJECT_LINKS.get(r["id"], ()))), assay_ids=[], updated_at=None)
                for r in ordered()]
        for start in range(0, len(rows), chunk):
            yield rows[start:start + chunk]

    patches = {
        "sample_types": lambda: copy.deepcopy(state["types"]),
        "sample_attributes": lambda: copy.deepcopy(state["attrs"]),
        "sample_attribute_types": lambda: copy.deepcopy(ATTR_TYPES),
        "type_context": lambda: {}, "type_clades": lambda: {}, "deprecated_titles": lambda: set(),
        "attribute_meanings": lambda: {},
        "iter_samples": iter_samples, "uuid_to_ids": uuid_to_ids,
        "iter_digest_rows": iter_digest_rows, "parent_identities": lambda uuids: {},
        "resolved_assay_map": lambda: {}, "sops_map": lambda: {}, "studies": lambda: [],
        "sample_projects": lambda: {k: sorted(set(v)) for k, v in PROJECT_LINKS.items()},
        "projects": lambda: [{"id": 2, "title": "Local"}, {"id": 16, "title": "TCGA"}],
        "memberships": lambda: copy.deepcopy(MEMBERSHIPS),
        "investigations": lambda: [{"id": 3, "title": "TCGA", "description": None}],
        "investigation_projects": lambda: [{"investigation_id": 3, "project_id": 16}],
        "seek_study_links": lambda: [{"sample_id": 11, "study_id": 7, "study_title": "S", "investigation_id": 3}],
        # gate G check 9: no assay links in this world (the maps and parent identities are stubbed above)
        "sample_assay_ids_for": lambda ids: {},
    }
    for name, fn in patches.items():
        monkeypatch.setattr(sources, name, fn)
    # The run records and the outbox are tested in test_graph_sync_full.py; here they record nothing.
    monkeypatch.setattr(sync_state, "start_run",
                        lambda kind, *, trigger, now=None: sync_state.RunHandle(None, kind, trigger, now))
    monkeypatch.setattr(sync_state, "mark_done_before", lambda ts, *, kinds=None, now=None: 0)
    monkeypatch.delenv("GS_RUN_DIR", raising=False)
    return state


class WriterRecorder:
    """Replaces every writer function ``run`` calls with a recorder returning plausible counts."""

    def __init__(self, monkeypatch, ghosts=None):
        self.calls = []
        self.ghosts = ghosts if ghosts is not None else GHOSTS
        fakes = {
            "find_ghosts": lambda d, db, ids, uuids: copy.deepcopy(self.ghosts),
            "delete_ghosts": lambda d, db, element_ids: {"ghosts_deleted": len(element_ids)},
            "retire_samples": lambda d, db, ids, archive_path: {
                "retire_requested": len(ids), "retired_deleted": 0, "retired_orphaned": len(ids),
                "retire_not_found": 0, "retired_archive_path": None},
            "relabel_orphans": lambda d, db, ids, element_ids=(): {"orphans_relabeled": len(ids) + len(element_ids)},
            "archive_and_drop_child_of": lambda d, db, path, declared: {
                "child_of_pairs": 0, "child_of_undeclared": 0, "child_of_deleted": 0, "archive_path": None},
            "ensure_constraints_v11": lambda d, db: {"schema_statements": 14},
            "write_sample_types": lambda d, db, rows: {"sample_types_written": len(rows),
                                                       "graph_only_sample_types": []},
            "write_attributes": lambda d, db, rows: {"attributes_written": len(rows), "attributes_without_type": 0},
            "write_projects": lambda d, db, rows: {"projects_written": len(rows)},
            "write_people_and_memberships": lambda d, db, rows: {"memberships_written": len(rows),
                                                                 "memberships_dropped": 0},
            "write_investigation_projects": lambda d, db, invs, links: {"investigations_written": len(invs)},
            "write_samples": self._write_samples,
            "write_missing_lineage": lambda d, db, pairs, chunk=10_000: {
                "lineage_pairs": len(pairs), "lineage_created": len(pairs), "lineage_dropped": 0},
            "archive_and_drop_undeclared_derived_from": lambda d, db, path, declared: {
                "derived_from_between_samples": 2, "derived_from_undeclared": 1, "derived_from_deleted": 1,
                "derived_from_archive_path": path},
            "write_seek_studies": lambda d, db, links: {"in_study_written": len(links), "in_study_dropped": 0},
            "write_attribute_counts": lambda d, db, counts: {"attribute_counts_set": len(counts)},
            "write_sample_type_counts": lambda d, db: {"sample_type_counts_set": 2},
            "ensure_index_budget": lambda d, db, census, bench_keys=frozenset(): ["gs_T_TIS_0123456789"],
            "ensure_fulltext": lambda d, db: {"fulltext_index": "sample_search_text"},
            "await_indexes": lambda d, db: {"indexes_online": 20},
            "write_graphmeta": lambda d, db, catalog_hash, label_maps_hash=None: {
                "schema_version": "1.2", "catalog_hash": catalog_hash},
        }
        for name, fn in fakes.items():
            monkeypatch.setattr(writer, name, self._recording(name, fn))

    def _recording(self, name, fn):
        def call(*args, **kwargs):
            args = tuple(list(a) if isgenerator(a) else a for a in args)
            self.calls.append(SimpleNamespace(name=name, args=args, kwargs=kwargs))
            return fn(*args, **kwargs)
        return call

    @staticmethod
    def _write_samples(d, db, projections, chunk=5000):
        links = sum(len(p.props["project_ids"]) for p in projections)
        return {"samples_written": len(projections), "of_type": len(projections), "untyped": 0,
                "in_project": links, "in_project_expected": links, "in_project_missing": 0,
                "cast_failures": sum(len(p.cast_failures) for p in projections)}

    def names(self):
        return [c.name for c in self.calls]

    def of(self, name):
        return [c for c in self.calls if c.name == name]


# --- lineage pairs -------------------------------------------------------------------------------

def test_lineage_pairs_encode_and_decode():
    assert run.decode_pair(run.encode_pair(1_308_453, 389_935)) == (1_308_453, 389_935)
    for bad in ((-1, 2), (1 << 31, 2), (2, 1 << 31)):
        with pytest.raises(ValueError):
            run.encode_pair(*bad)


def test_declared_uuid_pairs_resolve_through_duplicate_uuids():
    pairs = run.DeclaredUuidPairs({run.encode_pair(11, 20)}, {"C": [11], "P": [10, 20]})
    assert ("C", "P") in pairs
    assert ("P", "C") not in pairs
    assert ("C", "missing") not in pairs


def test_declared_id_pairs_answer_from_the_sorted_codes():
    codes = sorted(run.encode_pair(c, p) for c, p in [(11, 10), (243066, 133774), (5, 5), (0, 1)])
    pairs = run.DeclaredIdPairs(codes)
    assert len(pairs) == 4
    for pair in [(11, 10), (243066, 133774), (5, 5), (0, 1)]:
        assert pair in pairs
    for pair in [(10, 11), (243066, 154002), (70, 70), (0, 0), (1 << 40, 1)]:
        assert pair not in pairs
    # an id the graph holds in another form is never declared
    for pair in [("11", 10), (11, None), (True, 10), (11.0, 10), (-1, 10)]:
        assert pair not in pairs
    assert (11, 10) not in run.DeclaredIdPairs([])


# --- full_sync -----------------------------------------------------------------------------------

def test_full_sync_writes_in_the_design_order(world, monkeypatch, tmp_path):
    rec = WriterRecorder(monkeypatch)
    report = run.full_sync(FakeDriver(), "neo4j", chunk=2, run_dir=str(tmp_path))

    assert rec.names() == [
        "find_ghosts", "delete_ghosts", "retire_samples", "relabel_orphans", "archive_and_drop_child_of",
        "ensure_constraints_v11",
        "write_sample_types", "write_attributes", "write_projects", "write_people_and_memberships",
        "write_investigation_projects", "write_samples", "write_samples", "write_missing_lineage",
        "archive_and_drop_undeclared_derived_from", "write_seek_studies", "write_attributes",
        "write_attribute_counts", "write_sample_type_counts", "ensure_index_budget", "ensure_fulltext",
        "await_indexes", "write_graphmeta"]
    assert [len(c.args[2]) for c in rec.of("write_samples")] == [2, 1]
    assert report["status"] == "ok"
    assert report["samples_projected"] == 3 and report["samples_written"] == 3
    assert report["untyped"] == 0 and report["in_project_missing"] == 0 and report["cast_failures"] == 1
    saved = json.loads((tmp_path / run.REPORT_FILE).read_text())
    assert saved["status"] == "ok" and saved["samples_written"] == 3 and saved["label_collisions"] == 0


def test_full_sync_reports_the_undeclared_derived_from_step(world, monkeypatch, tmp_path):
    WriterRecorder(monkeypatch)
    run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path))

    saved = json.loads((tmp_path / run.REPORT_FILE).read_text())
    archive = str(tmp_path / run.DERIVED_FROM_ARCHIVE_FILE)
    expected = {"derived_from_between_samples": 2, "derived_from_undeclared": 1, "derived_from_deleted": 1,
                "derived_from_archive_path": archive}
    assert saved["steps"]["lineage_undeclared"] == expected
    assert {k: saved[k] for k in expected} == expected
    assert "lineage_undeclared" in saved["timings_s"]
    # CHILD_OF's archive path is not overwritten by the DERIVED_FROM step's
    assert saved["archive_path"] is None


def test_full_sync_archives_and_deletes_undeclared_derived_from_with_the_real_writer(world, monkeypatch, tmp_path):
    real = writer.archive_and_drop_undeclared_derived_from
    WriterRecorder(monkeypatch)
    monkeypatch.setattr(writer, "archive_and_drop_undeclared_derived_from", real)
    edges = [{"child_id": 11, "parent_id": 10, "child_uuid": U_D1, "parent_uuid": U_T1, "props": {},
              "element_id": "e-declared"},
             {"child_id": 12, "parent_id": 10, "child_uuid": U_T2, "parent_uuid": U_T1,
              "props": {"child_id": 12}, "element_id": "e-stale"},
             {"child_id": 10, "parent_id": 11, "child_uuid": U_T1, "parent_uuid": U_D1, "props": {},
              "element_id": "e-reversed"}]
    order = []

    def graph(query, params):
        if query == q.DERIVED_FROM_BETWEEN_SAMPLES:
            order.append("stream")
            return edges
        if query == q.DELETE_UNDECLARED_DERIVED_FROM:
            order.append(("delete", sorted(params["element_ids"]),
                          (tmp_path / run.DERIVED_FROM_ARCHIVE_FILE).exists()))
            return [{"deleted": len(params["element_ids"])}]
        return []

    report = run.full_sync(FakeDriver(graph), "neo4j", run_dir=str(tmp_path))

    assert order == ["stream", ("delete", ["e-reversed", "e-stale"], True)]
    rows = (tmp_path / run.DERIVED_FROM_ARCHIVE_FILE).read_text(encoding="utf-8").splitlines()
    assert rows[1:] == [f"12\t10\t{U_T2}\t{U_T1}\t" + '{"child_id": 12}', f"10\t11\t{U_T1}\t{U_D1}\t{{}}"]
    assert report["derived_from_undeclared"] == 2 and report["derived_from_deleted"] == 2
    assert json.loads((tmp_path / run.REPORT_FILE).read_text())["derived_from_between_samples"] == 3


def test_full_sync_hands_the_preflight_findings_to_the_writer(world, monkeypatch, tmp_path):
    rec = WriterRecorder(monkeypatch)
    run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path))

    (find,) = rec.of("find_ghosts")
    assert find.args[2] == {10, 11, 12} and U_T1 in find.args[3]
    assert rec.of("delete_ghosts")[0].args[2] == ["4:g:1"]
    (retire,) = rec.of("retire_samples")
    assert retire.args[2] == [99] and retire.args[3] == str(tmp_path / run.RETIRED_FILE)
    (relabel,) = rec.of("relabel_orphans")
    assert relabel.args[2] == [] and relabel.kwargs["element_ids"] == ["4:x:2"]
    (archive,) = rec.of("archive_and_drop_child_of")
    path, declared = archive.args[2], archive.args[3]
    assert path == str(tmp_path / run.ARCHIVE_FILE)
    assert (U_D1, U_T1) in declared
    assert (U_T1, U_D1) not in declared and (U_T2, U_T1) not in declared
    assert rec.of("write_missing_lineage")[0].args[2] == [(11, 10)]
    (undeclared,) = rec.of("archive_and_drop_undeclared_derived_from")
    df_path, df_declared = undeclared.args[2], undeclared.args[3]
    assert df_path == str(tmp_path / run.DERIVED_FROM_ARCHIVE_FILE)
    assert (11, 10) in df_declared
    assert (10, 11) not in df_declared and (12, 10) not in df_declared and (11, 11) not in df_declared
    assert rec.of("write_seek_studies")[0].args[2][0]["sample_id"] == 11


def test_full_sync_adds_undeclared_attributes_and_the_census_after_the_sample_pass(world, monkeypatch, tmp_path):
    rec = WriterRecorder(monkeypatch)
    report = run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path), bench_keys=frozenset({("TIS", "Organ")}))

    declared, full = rec.of("write_attributes")
    assert [r["key"] for r in declared.args[2]] == ["26:Organ", "26:CellCount", "26:Collected", "33:Parent"]
    (lane,) = [r for r in full.args[2] if r["key"] == "33:Lane"]
    assert lane["declared"] is False and "id" not in lane
    assert not any(r["title"] == "UID" for r in full.args[2])
    assert rec.of("write_attribute_counts")[0].args[2] == {
        "26:Organ": 2, "26:CellCount": 2, "26:Collected": 1, "33:Parent": 1, "33:Lane": 1}

    (budget,) = rec.of("ensure_index_budget")
    census = budget.args[2]
    assert census["26:CellCount"]["cast_failures"] == 1
    assert census["26:Organ"]["max_len"] == len("Liver")
    assert census["26:Collected"]["max_len"] == len("2024-01-31")
    assert census["33:Lane"]["value_type"] == "string" and census["33:Lane"]["declared"] is False
    assert budget.kwargs["bench_keys"] == frozenset({("TIS", "Organ")})

    cat = run.build_catalog()
    assert rec.of("write_graphmeta")[0].args[2] == catalog.catalog_hash(cat.sample_types, full.args[2])
    assert report["undeclared_attribute_keys"] == ["33:Lane"]
    assert report["index_budget"] == 1
    assert "33:Lane" in json.loads((tmp_path / run.CENSUS_FILE).read_text())


def test_census_measures_non_ascii_values_in_bytes():
    assert run._stored_len("abc") == 3
    assert run._stored_len("\u00e9" * 3) == 6
    assert run._stored_len(date(2024, 1, 31)) == 10


def test_full_sync_refuses_before_any_write_on_unresolved_duplicate_ids(world, monkeypatch, tmp_path):
    rec = WriterRecorder(monkeypatch, ghosts=dict(GHOSTS, unresolved_duplicate_ids=[10]))
    with pytest.raises(run.PreflightError, match="unresolved_duplicate_ids"):
        run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path))
    assert rec.names() == ["find_ghosts"]
    saved = json.loads((tmp_path / run.REPORT_FILE).read_text())
    assert saved["status"] == "refused" and saved["problems"]


@pytest.mark.parametrize("bad", [
    {"id": 13, "uuid": "TIS-220119FLY-13", "title": "x", "sample_type_id": 26, "json_metadata": "[1]"},
    _sample(14, "TIS-220119FLY-14", 99, {"Organ": "Lung"}),      # a sample type SEEK does not have
    _sample(15, "TIS-220119FLY-15", 26, {"title": "clash"}),     # a metadata key that is a system property
])
def test_full_sync_refuses_when_a_sample_cannot_be_projected(world, monkeypatch, tmp_path, bad):
    world["samples"].append(bad)
    rec = WriterRecorder(monkeypatch)
    with pytest.raises(run.PreflightError) as exc:
        run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path))
    assert exc.value.report["projection_errors"] == 1
    assert exc.value.report["projection_error_examples"][0]["id"] == bad["id"]
    assert rec.names() == ["find_ghosts"]


def test_full_sync_refuses_when_a_graph_sample_type_holds_a_title_under_another_id(world, monkeypatch, tmp_path):
    rec = WriterRecorder(monkeypatch)
    driver = FakeDriver(lambda query, params: [{"title": "TIS", "graph_id": 5, "mysql_id": 26}]
                        if query == q.SAMPLE_TYPE_TITLE_CONFLICTS else [])
    with pytest.raises(run.PreflightError, match="sample_type_title_conflicts"):
        run.full_sync(driver, "neo4j", run_dir=str(tmp_path))
    assert rec.names() == ["find_ghosts"]


def test_full_sync_refuses_on_a_label_collision(world, monkeypatch, tmp_path):
    world["types"].append({"id": 40, "title": "D_SEQ", "uuid": "st-40", "description": ""})
    rec = WriterRecorder(monkeypatch)
    with pytest.raises(run.PreflightError, match="collide"):
        run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path))
    assert rec.names() == []


def test_full_sync_needs_a_run_directory(world, monkeypatch):
    rec = WriterRecorder(monkeypatch)
    with pytest.raises(run.PreflightError, match="run directory"):
        run.full_sync(FakeDriver(), "neo4j")
    assert rec.names() == []


def test_full_sync_defaults_its_run_directory_under_gs_run_dir(world, monkeypatch, tmp_path):
    WriterRecorder(monkeypatch)
    monkeypatch.setenv("GS_RUN_DIR", str(tmp_path))
    report = run.full_sync(FakeDriver(), "neo4j")
    assert report["run_dir"].startswith(os.path.join(str(tmp_path), "graph_sync-"))
    assert os.path.exists(os.path.join(report["run_dir"], run.REPORT_FILE))


def test_full_sync_records_a_failure_part_way(world, monkeypatch, tmp_path):
    WriterRecorder(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("neo4j went away")

    monkeypatch.setattr(writer, "write_seek_studies", boom)
    with pytest.raises(RuntimeError):
        run.full_sync(FakeDriver(), "neo4j", run_dir=str(tmp_path))
    saved = json.loads((tmp_path / run.REPORT_FILE).read_text())
    assert saved["status"] == "failed" and "neo4j went away" in saved["error"]
    assert saved["samples_written"] == 3


def test_dry_run_reads_but_writes_nothing(world, monkeypatch, tmp_path):
    rec = WriterRecorder(monkeypatch)
    child_of = [{"child_uuid": U_D1, "parent_uuid": U_T1}, {"child_uuid": U_T2, "parent_uuid": U_T1}]
    driver = FakeDriver(lambda query, params: child_of if query == q.CHILD_OF_PAIRS else [])
    monkeypatch.chdir(tmp_path)

    report = run.full_sync(driver, "neo4j", dry_run=True)

    assert rec.names() == ["find_ghosts"]
    assert driver.calls and all(c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)
    assert report["status"] == "dry_run"
    assert report["samples_projected"] == 3 and report["label_collisions"] == 0 and report["ghosts"] == 1
    assert report["child_of_pairs"] == 2 and report["child_of_undeclared"] == 1
    assert report["undeclared_attribute_keys"] == ["33:Lane"] and report["lineage_declared_pairs"] == 1
    assert report["index_budget"] == 2  # CellCount (float) and Collected (date) have values
    assert list(tmp_path.iterdir()) == []


# --- catalog_sync --------------------------------------------------------------------------------

def _attribute_state(query, params):
    if query == q.READ_GRAPHMETA:
        return [{"props": {"schema_version": writer.SCHEMA_VERSION}}]
    if query != run.ATTRIBUTE_STATE:
        return []
    return [{"key": "26:Organ", "declared": True, "sample_type_id": 26, "title": "Organ", "sample_count": 7},
            {"key": "33:Lane", "declared": False, "sample_type_id": 33, "title": "Lane", "sample_count": 5},
            {"key": "40:Gone", "declared": False, "sample_type_id": 40, "title": "Gone", "sample_count": 2}]


def test_catalog_sync_keeps_undeclared_attributes_and_counts(world, monkeypatch):
    rec = WriterRecorder(monkeypatch)
    report = run.catalog_sync(FakeDriver(_attribute_state), "neo4j")

    assert rec.names() == ["write_sample_types", "write_attributes", "write_attribute_counts",
                           "write_sample_type_counts", "write_graphmeta"]
    keys = [r["key"] for r in rec.of("write_attributes")[0].args[2]]
    assert keys == ["26:Organ", "26:CellCount", "26:Collected", "33:Parent", "33:Lane"]
    assert rec.of("write_attribute_counts")[0].args[2] == {
        "26:Organ": 7, "26:CellCount": 0, "26:Collected": 0, "33:Parent": 0, "33:Lane": 5}
    assert report["undeclared_attributes_kept"] == 1 and report["status"] == "ok"
    assert rec.of("write_graphmeta")[0].args[2] == report["catalog_hash"]


def test_catalog_sync_dry_run_writes_nothing(world, monkeypatch):
    rec = WriterRecorder(monkeypatch)
    report = run.catalog_sync(FakeDriver(_attribute_state), "neo4j", dry_run=True)
    assert rec.names() == []
    assert report["status"] == "dry_run" and report["undeclared_attributes_kept"] == 1


# --- gate G --------------------------------------------------------------------------------------

def _graph_nodes():
    """The Sample nodes a correct full sync writes for the world, as the driver returns them."""
    cat = run.build_catalog()
    nodes = {}
    for row in SAMPLES:
        type_id = row["sample_type_id"]
        proj = project_sample(row, cat.type_titles[type_id], cat.value_types.get(type_id, {}),
                              PROJECT_LINKS[row["id"]], parent_lists=([], []))
        props = {k: Neo4jDate(v.year, v.month, v.day) if isinstance(v, date) else v for k, v in proj.props.items()}
        props["synced_at"] = "2026-09-14T00:00:00Z"
        nodes[row["id"]] = {"props": props, "label": proj.label}
    return nodes


class GraphWorld:
    """Answers verify's statements for the graph a correct full sync writes; tests break one part at a time."""

    def __init__(self, nodes, edges=((11, 10),)):
        self.nodes = nodes
        self.edges = list(edges)
        self.catalog = [{"id": 26, "title": "TIS", "label": "T_TIS", "titles": ["Organ", "CellCount", "Collected"]},
                        {"id": 33, "title": "D.SEQ", "label": "T_D_SEQ", "titles": ["Parent", "Lane"]}]
        self.constraints = list(verify.EXPECTED_CONSTRAINTS)
        self.indexes = [{"name": n, "state": "ONLINE", "populationPercent": 100.0}
                        for n in verify.EXPECTED_INDEXES + verify.EXPECTED_CONSTRAINTS]
        self.graphmeta = [{"schema_version": "1.2"}]

    def __call__(self, query, params):
        nodes = self.nodes
        if query == verify.LINEAGE_PAIRS:
            return [{"child": c, "parent": p} for c, p in self.edges]
        if query == verify.LINEAGE_LABELS:
            return [{"child": c, "parent": p, "stored": {k: None for k in q.EDGE_LABEL_KEYS}} for c, p in self.edges]
        if query == verify.LINEAGE_ON_ORPHANS:
            return [{"n": 0}]
        if query == verify.PROJECT_ID_GROUPS:
            groups = Counter(tuple(n["props"]["project_ids"]) for n in nodes.values())
            return [{"project_ids": list(k), "n": v} for k, v in groups.items()]
        if query == verify.ACCOUNT_SCOPE_COUNT:
            wanted = set(params["projects"])
            return [{"n": sum(1 for n in nodes.values() if wanted & set(n["props"]["project_ids"]))}]
        if query == verify.GRAPH_CATALOG:
            return self.catalog
        for entry in self.catalog:
            if query == verify.TYPE_KEYS.format(label=entry["label"]):
                keys = {k for n in nodes.values() if n["label"] == entry["label"] for k in n["props"]}
                return [{"keys": sorted(keys - set(params["system"]))}]
        if query == verify.SAMPLED_NODES:
            return [{"id": i, "props": nodes[i]["props"], "type_labels": [nodes[i]["label"]]}
                    for i in params["ids"] if i in nodes]
        if query in (verify.SAMPLE_COUNT, verify.OF_TYPE_COUNT):
            return [{"n": len(nodes)}]
        if query == verify.TYPE_LABEL_AUDIT:
            return [{"samples": len(nodes), "not_one_type_label": 0, "not_one_of_type": 0, "label_differs": 0,
                     "label_sets": [[label] for label in sorted({n["label"] for n in nodes.values()})]}]
        if query == verify.GRAPH_ATTRIBUTE_IDS:
            return [{"id": a["id"], "title": a["title"], "sample_type_id": a["sample_type_id"]} for a in ATTRS]
        if query == verify.CONSTRAINT_NAMES:
            return [{"name": n} for n in self.constraints]
        if query == q.INDEX_STATES:
            return self.indexes
        if query in (verify.LABEL_COLLISIONS, verify.SAMPLE_TYPES_WITHOUT_ID_OR_LABEL):
            return [{"n": 0}]
        if query == verify.GRAPHMETA:
            return self.graphmeta
        if query == verify.T_LABEL_WITHOUT_SAMPLE:
            return [{"n": 0}]
        raise AssertionError(f"unexpected statement: {query}")


@pytest.fixture
def mysql_scope(monkeypatch):
    """The SQL EXISTS count and graph_search's scope resolver, answered from the world."""
    def sql_count(project_ids):
        wanted = set(project_ids)
        return sum(1 for pids in PROJECT_LINKS.values() if wanted & set(pids))

    monkeypatch.setattr(verify, "_sql_scope_count", sql_count)
    monkeypatch.setattr(verify, "_account_scope", lambda login: ACCOUNTS.get(login))


def _gate(graph, **kwargs):
    kwargs.setdefault("sample_size", 10)
    kwargs.setdefault("seed", 7)
    return verify.gate_g(FakeDriver(graph), "neo4j", **kwargs)


def _named(result, name):
    (check,) = [c for c in result["checks"] if c["name"] == name]
    return check


def test_gate_g_passes_on_the_graph_a_correct_sync_writes(world, mysql_scope):
    result = _gate(GraphWorld(_graph_nodes()))
    assert [c for c in result["checks"] if not c["pass"]] == []
    assert result["pass"] is True
    assert {c["name"].split(".")[0] for c in result["checks"]} == {str(i) for i in range(1, 12)}
    assert all({"name", "expected", "actual", "pass"} <= set(c) for c in result["checks"])
    assert result["stats"]["seed"] == 7 and result["stats"]["sampled_ids"] == [10, 11, 12]
    assert result["stats"]["metadata_hash_mysql"] == result["stats"]["metadata_hash_graph"]


def test_gate_g_only_reads(world, mysql_scope):
    driver = FakeDriver(GraphWorld(_graph_nodes()))
    verify.gate_g(driver, "neo4j", sample_size=10, seed=1)
    assert driver.calls and all(c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)


def test_gate_g_draws_a_reproducible_random_sample(world, mysql_scope):
    first = _gate(GraphWorld(_graph_nodes()), sample_size=2, seed=3)["stats"]["sampled_ids"]
    again = _gate(GraphWorld(_graph_nodes()), sample_size=2, seed=3)["stats"]["sampled_ids"]
    assert first == again and len(first) == 2


def test_gate_g_fails_a_missing_declared_edge(world, mysql_scope):
    result = _gate(GraphWorld(_graph_nodes(), edges=[]))
    check = _named(result, "1.lineage.declared_pairs_missing")
    assert (check["actual"], check["pass"], check["detail"]) == (1, False, [[11, 10]])
    assert result["pass"] is False


def test_gate_g_fails_an_undeclared_edge_between_samples(world, mysql_scope):
    result = _gate(GraphWorld(_graph_nodes(), edges=[(11, 10), (12, 10)]))
    assert _named(result, "1.lineage.undeclared_pairs_between_samples")["actual"] == 1
    assert result["pass"] is False


def test_gate_g_fails_a_key_the_catalog_does_not_list(world, mysql_scope):
    nodes = _graph_nodes()
    nodes[12]["props"]["Bogus"] = "x"
    result = _gate(GraphWorld(nodes))
    assert _named(result, "3.catalog.sampled_samples_with_unlisted_keys")["actual"] == 1
    assert _named(result, "3.catalog.types_with_unlisted_keys")["detail"] == {"T_TIS": ["Bogus"]}
    assert _named(result, "7.metadata.sampled_mismatched")["detail"] == [{"id": 12, "keys": ["Bogus"]}]


def test_gate_g_fails_project_ids_that_differ_from_projects_samples(world, mysql_scope):
    nodes = _graph_nodes()
    nodes[11]["props"]["project_ids"] = [2]
    result = _gate(GraphWorld(nodes))
    assert _named(result, "2.scope.projects_with_count_mismatch")["detail"] == [
        {"project_id": 16, "mysql": 2, "graph": 1}]
    assert _named(result, "2.scope.sampled_project_ids_mismatch")["actual"] == 1
    assert _named(result, "6.scope.person_scopes_mismatched")["actual"] >= 1
    tcga = _named(result, "6.scope.account.tcgamember")
    assert (tcga["expected"], tcga["actual"], tcga["pass"]) == (2, 1, False)


@pytest.mark.parametrize("key, wrong", [("Collected", "2024-01-31"), ("CellCount", 5)])
def test_gate_g_fails_a_value_stored_with_the_wrong_type(world, mysql_scope, key, wrong):
    nodes = _graph_nodes()
    nodes[10]["props"][key] = wrong  # a date kept as its string; a float stored as an int
    result = _gate(GraphWorld(nodes))
    assert _named(result, "7.metadata.sampled_mismatched")["detail"] == [{"id": 10, "keys": [key]}]


def test_gate_g_fails_a_missing_sample_and_a_missing_account(world, mysql_scope, monkeypatch):
    nodes = _graph_nodes()
    del nodes[12]
    monkeypatch.setattr(verify, "_account_scope", lambda login: None if login == "tcgamember" else (2,))
    result = _gate(GraphWorld(nodes))
    assert _named(result, "4.samples.graph_count")["actual"] == 2
    assert _named(result, "7.metadata.sampled_missing_in_graph")["detail"] == [12]
    assert _named(result, "6.scope.account.tcgamember")["pass"] is False
    assert _named(result, "6.scope.account.user")["pass"] is True


def test_gate_g_fails_on_schema_state(world, mysql_scope):
    graph = GraphWorld(_graph_nodes())
    graph.constraints.remove("sample_id_unique")
    graph.indexes[0] = dict(graph.indexes[0], state="POPULATING")
    graph.graphmeta = [{"schema_version": "1.0"}]
    result = _gate(graph)
    assert _named(result, "8.schema.constraints_missing")["detail"] == ["sample_id_unique"]
    assert _named(result, "8.schema.indexes_not_online")["actual"] == 1
    assert _named(result, "8.graphmeta.schema_version")["actual"] == "1.0"
    assert result["pass"] is False


def test_gate_g_reports_a_catalog_that_does_not_build(world, mysql_scope):
    world["types"].append({"id": 40, "title": "D_SEQ", "uuid": "st-40", "description": ""})
    result = _gate(GraphWorld(_graph_nodes_before_collision()))
    assert _named(result, "8.catalog.builds")["pass"] is False
    assert _named(result, "8.catalog.label_collisions")["actual"] == 1
    assert _named(result, "7.metadata.sampled_mismatched")["pass"] is False


def _graph_nodes_before_collision():
    nodes = {}
    for row in SAMPLES:
        nodes[row["id"]] = {"props": {"id": row["id"], "project_ids": sorted(set(PROJECT_LINKS[row["id"]]))},
                            "label": "T_TIS" if row["sample_type_id"] == 26 else "T_D_SEQ"}
    return nodes


def test_expected_schema_names_come_from_the_writer():
    assert "sample_id_unique" in verify.EXPECTED_CONSTRAINTS
    assert "sample_uuid" in verify.EXPECTED_INDEXES and q.FULLTEXT_INDEX in verify.EXPECTED_INDEXES
    assert not set(verify.EXPECTED_CONSTRAINTS) & set(verify.EXPECTED_INDEXES)


# --- the command ---------------------------------------------------------------------------------

class FakeGraphDatabase:
    """Stands in for ``neo4j.GraphDatabase``; records every driver it is asked for."""

    def __init__(self):
        self.uris = []

    def driver(self, uri, auth=None):
        self.uris.append(uri)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def graphdb(monkeypatch, settings):
    fake = FakeGraphDatabase()
    monkeypatch.setattr(command, "GraphDatabase", fake)
    settings.NEO4J_DATABASE = {"NAME": "neo4j", "URI": "neo4j://gs-v11-neo4j", "AUTH": ("neo4j", "x")}
    return fake


def _gate_result(passed):
    return {"checks": [{"name": "4.samples.graph_count", "expected": 3, "actual": 3 if passed else 2,
                        "pass": passed}], "pass": passed, "stats": {}}


@pytest.mark.parametrize("uri", ["neo4j://neo4j", "neo4j://neo4j:7687", "bolt://NEO4J:7687"])
@pytest.mark.parametrize("mode", ["--full", "--catalog"])
def test_refuses_the_live_graph_without_the_flag(graphdb, settings, uri, mode):
    settings.NEO4J_DATABASE = {"NAME": "neo4j", "URI": uri, "AUTH": ("neo4j", "x")}
    with pytest.raises(CommandError) as exc:
        call_command("graph_sync", mode, stdout=StringIO(), stderr=StringIO())
    assert exc.value.returncode == 2
    assert "--i-mean-the-live-graph" in str(exc.value)
    assert graphdb.uris == []


def test_the_flag_allows_the_live_graph(graphdb, settings, monkeypatch):
    settings.NEO4J_DATABASE = {"NAME": "neo4j", "URI": "neo4j://neo4j:7687", "AUTH": ("neo4j", "x")}
    monkeypatch.setattr(verify, "gate_g", lambda driver, db, **kw: _gate_result(True))
    call_command("graph_sync", "--verify", "--i-mean-the-live-graph", stdout=StringIO(), stderr=StringIO())
    assert graphdb.uris == ["neo4j://neo4j:7687"]


def test_refuses_without_a_neo4j_uri(graphdb, settings):
    settings.NEO4J_DATABASE = {}
    with pytest.raises(CommandError) as exc:
        call_command("graph_sync", "--verify", stdout=StringIO(), stderr=StringIO())
    assert exc.value.returncode == 2 and graphdb.uris == []


@pytest.mark.parametrize("args", [("--full", "--verify"), ("--full", "--catalog"), (),
                                  ("--full", "--chunk", "0"), ("--full", "--chunk", "x")])
def test_rejects_bad_arguments_before_connecting(graphdb, args):
    with pytest.raises(CommandError):
        call_command("graph_sync", *args, stdout=StringIO(), stderr=StringIO())
    assert graphdb.uris == []


def test_dry_run_full_calls_full_sync_dry_and_prints_counts(graphdb, monkeypatch):
    seen = {}

    def fake_full_sync(driver, db, **kwargs):
        seen.update(kwargs, db=db)
        return {"status": "dry_run", "samples_projected": 1_084_754, "label_collisions": 0, "ghosts": 79,
                "ghost_element_ids": ["4:g:1"] * 79}

    monkeypatch.setattr(run, "full_sync", fake_full_sync)
    out = StringIO()
    call_command("graph_sync", "--full", "--dry-run", "--chunk", "250", stdout=out, stderr=StringIO())
    assert seen["dry_run"] is True and seen["chunk"] == 250 and seen["run_dir"] is None
    assert seen["bench_keys"] == frozenset() and seen["db"] == "neo4j"
    text = out.getvalue()
    assert "samples_projected: 1084754" in text and "ghosts: 79" in text and "ghost_element_ids: <79 items>" in text

    out = StringIO()
    call_command("graph_sync", "--full", "--dry-run", "--json", stdout=out, stderr=StringIO())
    assert json.loads(out.getvalue())["samples_projected"] == 1_084_754


def test_full_passes_run_dir_and_bench_keys(graphdb, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(run, "full_sync", lambda driver, db, **kw: seen.update(kw) or {"status": "ok"})
    keys = tmp_path / "bench.json"
    keys.write_text(json.dumps(["26:Organ", ["TIS", "CellCount"]]))
    call_command("graph_sync", "--full", "--run-dir", str(tmp_path), "--bench-keys", str(keys),
                 stdout=StringIO(), stderr=StringIO())
    assert seen["dry_run"] is False and seen["run_dir"] == str(tmp_path)
    assert seen["bench_keys"] == frozenset({"26:Organ", ("TIS", "CellCount")})


def test_bad_bench_keys_are_an_error(graphdb, tmp_path):
    keys = tmp_path / "bench.json"
    keys.write_text(json.dumps({"26:Organ": 1}))
    with pytest.raises(CommandError, match="--bench-keys"):
        call_command("graph_sync", "--full", "--bench-keys", str(keys), stdout=StringIO(), stderr=StringIO())
    assert graphdb.uris == []


def test_a_preflight_refusal_exits_2_and_prints_the_report(graphdb, monkeypatch):
    def refuse(driver, db, **kwargs):
        raise run.PreflightError(["1 sample ids sit on more than one node"], {"problems": ["dup"], "ghosts": 79})

    monkeypatch.setattr(run, "full_sync", refuse)
    out = StringIO()
    with pytest.raises(CommandError) as exc:
        call_command("graph_sync", "--full", "--json", stdout=out, stderr=StringIO())
    assert exc.value.returncode == 2
    assert json.loads(out.getvalue()) == {"problems": ["dup"], "ghosts": 79}


def test_catalog_dry_run_calls_catalog_sync_dry(graphdb, monkeypatch):
    seen = {}
    monkeypatch.setattr(run, "catalog_sync", lambda driver, db, **kw: seen.update(kw) or {"status": "dry_run"})
    call_command("graph_sync", "--catalog", "--dry-run", stdout=StringIO(), stderr=StringIO())
    assert seen == {"dry_run": True, "record": True, "trigger": "command"}


def test_verify_json_prints_the_gate_and_exits_1_when_it_fails(graphdb, monkeypatch):
    monkeypatch.setattr(verify, "gate_g", lambda driver, db, **kw: _gate_result(False))
    out = StringIO()
    with pytest.raises(CommandError) as exc:
        call_command("graph_sync", "--verify", "--json", stdout=out, stderr=StringIO())
    assert exc.value.returncode == 1
    assert json.loads(out.getvalue()) == _gate_result(False)


def test_verify_passes_with_exit_0_and_saves_to_the_run_dir(graphdb, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(verify, "gate_g", lambda driver, db, **kw: seen.update(kw) or _gate_result(True))
    out = StringIO()
    call_command("graph_sync", "--verify", "--seed", "11", "--run-dir", str(tmp_path), stdout=out,
                 stderr=StringIO())
    assert seen["seed"] == 11
    assert "PASS  4.samples.graph_count" in out.getvalue() and "gate G: PASS" in out.getvalue()
    assert json.loads((tmp_path / "gate_g.json").read_text())["pass"] is True


# --- the loop, and the reconcile, drift and samples modes -----------------------------------------

LIVE = {"NAME": "neo4j", "URI": "neo4j://neo4j:7687", "AUTH": ("neo4j", "x")}


def _drift_result(status, checks=()):
    return {"status": status, "checks": list(checks), "pass": status == drift.OK, "stats": {}}


@pytest.fixture
def modes(monkeypatch):
    """Every mode's worker, replaced by a recorder; ``seen`` holds the last call's keyword arguments."""
    seen = {}

    def answer(name, result):
        def call(driver, db, *args, **kwargs):
            seen.clear()
            seen.update(kwargs, mode=name, db=db, args=args)
            return result
        return call

    monkeypatch.setattr(verify, "gate_g", lambda driver, db, **kw: _gate_result(True))
    monkeypatch.setattr(drift, "drift_check", answer("drift", _drift_result(drift.OK)))
    monkeypatch.setattr(reconcile, "reconcile", answer("reconcile", {"status": reconcile.OK}))
    monkeypatch.setattr(targeted, "sync_samples", answer("samples", {"status": targeted.OK}))
    monkeypatch.setattr(run, "full_sync", answer("full", {"status": "ok"}))
    monkeypatch.setattr(loop, "run_pass", answer("once", {"counts": {"done": 2}, "drained": []}))
    monkeypatch.setattr(loop, "run_forever", answer("loop", None))
    return seen


@pytest.mark.parametrize("mode", ["--verify", "--drift", "--loop"])
def test_the_read_only_and_loop_modes_accept_the_live_graph(graphdb, settings, modes, mode):
    """The loop runs inside the app container against the live graph, and reading one is never a mistake."""
    settings.NEO4J_DATABASE = dict(LIVE)
    call_command("graph_sync", mode, stdout=StringIO(), stderr=StringIO())
    assert graphdb.uris == ["neo4j://neo4j:7687"]


@pytest.mark.django_db
@pytest.mark.parametrize("args", [("--once",), ("--reconcile",), ("--samples", "11")])
def test_the_writing_modes_still_need_the_live_flag_by_hand(graphdb, settings, modes, args):
    settings.NEO4J_DATABASE = dict(LIVE)
    with pytest.raises(CommandError) as exc:
        call_command("graph_sync", *args, stdout=StringIO(), stderr=StringIO())
    assert exc.value.returncode == 2 and graphdb.uris == []


# --- --drift --------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, code", [(drift.OK, 0), (drift.DRIFT, 1), (drift.REFUSED, 2)])
def test_drift_exits_by_its_result_and_saves_it(graphdb, monkeypatch, tmp_path, status, code):
    result = _drift_result(status, [{"name": "samples.not_in_mysql", "expected": 0, "actual": 1,
                                     "pass": status == drift.OK}])
    if status == drift.REFUSED:
        result["reason"] = "the graph is at schema version '1.1', not 1.2"
    seen = {}
    monkeypatch.setattr(drift, "drift_check", lambda driver, db, **kw: seen.update(kw) or result)
    out, args = StringIO(), ("graph_sync", "--drift", "--json", "--run-dir", str(tmp_path), "--seed", "3")

    if code == 0:
        call_command(*args, stdout=out, stderr=StringIO())
    else:
        with pytest.raises(CommandError) as exc:
            call_command(*args, stdout=out, stderr=StringIO())
        assert exc.value.returncode == code
        if status == drift.REFUSED:
            assert result["reason"] in str(exc.value)

    assert json.loads(out.getvalue()) == result          # --json prints the result whatever the exit status
    assert json.loads((tmp_path / "drift.json").read_text())["status"] == status
    assert seen["seed"] == 3 and seen["trigger"] == "command"


def test_drift_that_cannot_complete_exits_3(graphdb, monkeypatch):
    def boom(driver, db, **kwargs):
        raise RuntimeError("neo4j went away")

    monkeypatch.setattr(drift, "drift_check", boom)
    with pytest.raises(CommandError) as exc:
        call_command("graph_sync", "--drift", stdout=StringIO(), stderr=StringIO())
    assert exc.value.returncode == 3 and "neo4j went away" in str(exc.value)


def test_no_record_keeps_the_drift_run_out_of_the_run_records(graphdb, modes):
    call_command("graph_sync", "--drift", "--no-record", stdout=StringIO(), stderr=StringIO())
    assert modes["trigger"] is None


# --- --reconcile ----------------------------------------------------------------------------------

def test_reconcile_passes_its_options_and_a_run_directory_under_the_run_root(graphdb, modes, tmp_path):
    call_command("graph_sync", "--reconcile", "--chunk", "250", "--apply-label-changes", "--no-record",
                 "--run-root", str(tmp_path), "--trigger", "loop", stdout=StringIO(), stderr=StringIO())
    assert modes["chunk"] == 250 and modes["apply_label_changes"] is True
    assert modes["record"] is False and modes["trigger"] == "loop" and modes["dry_run"] is False
    assert modes["run_dir"].startswith(os.path.join(str(tmp_path), "reconcile-"))


def test_reconcile_uses_the_run_dir_it_is_given(graphdb, modes, tmp_path):
    call_command("graph_sync", "--reconcile", "--run-dir", str(tmp_path), stdout=StringIO(), stderr=StringIO())
    assert modes["run_dir"] == str(tmp_path)


@pytest.mark.parametrize("status, code", [(reconcile.OK, 0), (reconcile.DRY_RUN, 0), (reconcile.GUARD_TRIPPED, 0),
                                          (reconcile.LOCK_TIMEOUT, 1), (reconcile.NOT_AT_VERSION, 2),
                                          (reconcile.REFUSED, 2)])
def test_reconcile_exits_by_its_status(graphdb, monkeypatch, status, code):
    """A lock timeout is exit 1, not a refusal: the run may have written its earlier steps, and the loop's child
    must be retried rather than marked done."""
    monkeypatch.setattr(reconcile, "reconcile", lambda driver, db, **kw: {"status": status})
    out = StringIO()
    if code == 0:
        call_command("graph_sync", "--reconcile", "--json", stdout=out, stderr=StringIO())
    else:
        with pytest.raises(CommandError) as exc:
            call_command("graph_sync", "--reconcile", "--json", stdout=out, stderr=StringIO())
        assert exc.value.returncode == code
    assert json.loads(out.getvalue())["status"] == status


# --- --samples ------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_samples_syncs_the_ids_it_is_given_and_records_the_run(graphdb, modes, tmp_path):
    call_command("graph_sync", "--samples", "11, 12,11", "--run-root", str(tmp_path), "--chunk", "250",
                 stdout=StringIO(), stderr=StringIO())
    assert modes["args"] == ([11, 12],) and modes["chunk"] == 250
    assert modes["apply_label_changes"] is False
    assert modes["run_dir"].startswith(os.path.join(str(tmp_path), "samples-"))
    (record,) = sync_state.GraphSyncRun.objects.filter(kind="samples")
    assert (record.status, record.counts_json["trigger"]) == ("ok", "command")


@pytest.mark.django_db
@pytest.mark.parametrize("status, code, recorded", [(targeted.OK, 0, "ok"), (targeted.LOCK_TIMEOUT, 2, "refused"),
                                                    (targeted.NOT_AT_VERSION, 2, "refused")])
def test_samples_exits_by_its_status(graphdb, monkeypatch, status, code, recorded):
    monkeypatch.setattr(targeted, "sync_samples", lambda driver, db, ids, **kw: {"status": status, "requested": 1})
    if code == 0:
        call_command("graph_sync", "--samples", "11", stdout=StringIO(), stderr=StringIO())
    else:
        with pytest.raises(CommandError) as exc:
            call_command("graph_sync", "--samples", "11", stdout=StringIO(), stderr=StringIO())
        assert exc.value.returncode == code
    (record,) = sync_state.GraphSyncRun.objects.filter(kind="samples")
    assert record.status == recorded


@pytest.mark.django_db
def test_a_sample_sync_that_raises_records_the_failure(graphdb, monkeypatch):
    def boom(driver, db, ids, **kwargs):
        raise RuntimeError("neo4j went away")

    monkeypatch.setattr(targeted, "sync_samples", boom)
    with pytest.raises(RuntimeError):
        call_command("graph_sync", "--samples", "11", stdout=StringIO(), stderr=StringIO())
    (record,) = sync_state.GraphSyncRun.objects.filter(kind="samples")
    assert record.status == "failed"


@pytest.mark.parametrize("bad", ["", "x", "1,-2", "1,,2", "1,2.5"])
def test_a_bad_sample_id_list_is_an_error_before_connecting(graphdb, bad):
    with pytest.raises(CommandError):
        call_command("graph_sync", "--samples", bad, stdout=StringIO(), stderr=StringIO())
    assert graphdb.uris == []


# --- --loop and --once ----------------------------------------------------------------------------

def test_loop_runs_for_ever_with_its_interval_and_run_root(graphdb, modes, tmp_path):
    call_command("graph_sync", "--loop", "--interval", "30", "--run-root", str(tmp_path),
                 stdout=StringIO(), stderr=StringIO())
    assert modes["mode"] == "loop" and modes["interval_s"] == 30
    opts = modes["opts"]
    assert (opts.run_root, opts.record, opts.apply_label_changes) == (str(tmp_path), True, False)
    assert modes["args"][0].startswith(f"{loop.TRIGGER}:")


def test_the_loop_takes_its_label_approval_from_the_environment(graphdb, modes, monkeypatch):
    monkeypatch.setenv(loop.LABEL_CHANGES_ENV, "apply")
    call_command("graph_sync", "--loop", stdout=StringIO(), stderr=StringIO())
    assert modes["opts"].apply_label_changes is True


def test_once_makes_one_pass_and_prints_its_report(graphdb, modes, tmp_path):
    out = StringIO()
    call_command("graph_sync", "--once", "--json", "--run-root", str(tmp_path), "--no-record",
                 stdout=out, stderr=StringIO())
    assert modes["mode"] == "once" and modes["opts"].record is False
    assert json.loads(out.getvalue())["counts"] == {"done": 2}


@pytest.mark.parametrize("mode", ["--verify", "--drift", "--catalog", "--loop", "--once"])
def test_apply_label_changes_is_refused_where_it_would_do_nothing(graphdb, mode):
    with pytest.raises(CommandError, match="--apply-label-changes"):
        call_command("graph_sync", mode, "--apply-label-changes", stdout=StringIO(), stderr=StringIO())
    assert graphdb.uris == []


def test_apply_label_changes_and_the_run_record_reach_the_full_sync(graphdb, modes):
    call_command("graph_sync", "--full", "--apply-label-changes", "--no-record", "--trigger", "loop",
                 stdout=StringIO(), stderr=StringIO())
    assert modes["apply_label_changes"] is True and modes["record"] is False and modes["trigger"] == "loop"
