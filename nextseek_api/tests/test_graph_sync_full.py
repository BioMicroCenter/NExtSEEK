"""The schema 1.2 full and catalog syncs (nextseek_api/graph_sync/run.py; the sync design, sections 7.3, 7.4, 9, 11).

No Neo4j and no MySQL: the readers in ``sources`` answer from a small fixed world, the writer functions the steps
under test do not need are recorders, and a fake driver keeps just enough of a graph for the rest (the lineage and
label statements, the deletion rule, the Study re-key, GraphMeta). The run records and the outbox are the real
tables in the SQLite test database.
"""
from __future__ import annotations

import copy
import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.utils import timezone

from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.graph_sync import labels, projection, run, sources, state, writer
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun

pytestmark = pytest.mark.django_db

U_T1, U_D1, U_D2 = "TIS-220119FLY-1", "D.SEQ-220119FLY-2", "D.SEQ-220119FLY-3"

TYPES = [{"id": 26, "title": "TIS", "uuid": "st-26", "description": "Tissue"},
         {"id": 33, "title": "D.SEQ", "uuid": "st-33", "description": "Sequencing"}]


def _attr(attr_id, type_id, title):
    return {"id": attr_id, "sample_type_id": type_id, "title": title, "pos": attr_id, "required": False,
            "is_title": False, "sample_attribute_type_id": 1, "description": None}


ATTRS = [_attr(1, 26, "Organ"), _attr(2, 33, "Parent"), _attr(3, 33, "Protocol")]
ATTR_TYPES = {1: {"id": 1, "title": "String", "base_type": "String", "regexp": None}}


def _sample(sample_id, uuid, type_id, meta):
    return {"id": sample_id, "uuid": uuid, "title": f"s{sample_id}", "sample_type_id": type_id,
            "json_metadata": json.dumps(meta)}


SAMPLES = [
    _sample(10, U_T1, 26, {"UID": U_T1, "Organ": "Lung", "Name": "lung-a"}),
    _sample(11, U_D1, 33, {"UID": U_D1, "Parent": U_T1, "Protocol": "/sops/7"}),
    _sample(12, U_D2, 33, {"UID": U_D2, "Parent": f"{U_T1}; free text parent", "Protocol": "RNA prep"}),
]
PROJECT_LINKS = {10: [2], 11: [2, 16], 12: [16]}
ASSAY_LINKS = {10: [500], 11: [501, 500], 12: [502]}
ASSAY_MAP = {500: (99, "Patient Visit"), 501: (None, "Seq run"), 502: (None, "Other")}
SOPS = {7: "Extraction", 8: "RNA prep"}
SEEK_STUDIES = [{"id": 7, "title": "S", "investigation_id": 3}, {"id": 8, "title": "Paper", "investigation_id": 3},
                {"id": 9, "title": "S9", "investigation_id": 3}]

# What the rule gives the two declared edges: (11, 10) shares assay 500, which maps to internal assay 99, and names
# SOP 7 by URL; (12, 10) shares no assay and names SOP 8 by title.
LABELS_11_10 = {"assay_id": 500, "internal_assay_id": 99, "internal_assay_title": "Patient Visit",
                "internal_assay_ids": [99], "internal_assay_titles": ["Patient Visit"],
                "protocol_id": 7, "protocol_title": "Extraction"}
LABELS_12_10 = {"assay_id": None, "internal_assay_id": None, "internal_assay_title": None,
                "internal_assay_ids": [], "internal_assay_titles": [], "protocol_id": 8, "protocol_title": "RNA prep"}

NO_GHOSTS = {"ghost_element_ids": [], "orphan_ids": [], "unresolved_duplicate_ids": [], "idless_element_ids": [],
             "duplicate_ids": 0, "sample_nodes": 0}


@pytest.fixture
def world(monkeypatch):
    """Install the fixed MySQL world into ``sources``; a test may change the returned state before a run."""
    state_ = {"samples": copy.deepcopy(SAMPLES), "studies": copy.deepcopy(SEEK_STUDIES)}

    def ordered():
        return sorted(state_["samples"], key=lambda r: r["id"])

    def iter_digest_rows(chunk=5000):
        rows = [dict(r, project_ids=sorted(set(PROJECT_LINKS.get(r["id"], ()))),
                     assay_ids=sorted(set(ASSAY_LINKS.get(r["id"], ()))), updated_at=None) for r in ordered()]
        for start in range(0, len(rows), chunk):
            yield rows[start:start + chunk]

    def uuid_to_ids():
        index = {}
        for row in ordered():
            index.setdefault(row["uuid"], []).append(row["id"])
        return index

    def parent_identities(uuids):
        wanted = set(uuids)
        return {r["uuid"]: extract_identity(json.loads(r["json_metadata"]), uid=r["uuid"])
                for r in ordered() if r["uuid"] in wanted}

    def must_not_read():
        raise AssertionError("the full sync reads project and assay links from iter_digest_rows")

    patches = {
        "sample_types": lambda: copy.deepcopy(TYPES), "sample_attributes": lambda: copy.deepcopy(ATTRS),
        "sample_attribute_types": lambda: copy.deepcopy(ATTR_TYPES), "type_context": lambda: {},
        "type_clades": lambda: {}, "deprecated_titles": lambda: set(), "attribute_meanings": lambda: {},
        "iter_digest_rows": iter_digest_rows, "uuid_to_ids": uuid_to_ids, "parent_identities": parent_identities,
        "sample_projects": must_not_read, "iter_samples": lambda *a, **k: must_not_read(),
        "resolved_assay_map": lambda: dict(ASSAY_MAP), "sops_map": lambda: dict(SOPS),
        "studies": lambda: copy.deepcopy(state_["studies"]),
        "projects": lambda: [{"id": 2, "title": "Local"}, {"id": 16, "title": "TCGA"}],
        "memberships": lambda: [{"person_id": 144, "project_id": 2, "has_left": False, "time_left_at": None}],
        "investigations": lambda: [{"id": 3, "title": "TCGA", "description": None}],
        "investigation_projects": lambda: [{"investigation_id": 3, "project_id": 16}],
        "seek_study_links": lambda: [{"sample_id": 11, "study_id": 7, "study_title": "S", "investigation_id": 3}],
    }
    for name, fn in patches.items():
        monkeypatch.setattr(sources, name, fn)
    monkeypatch.delenv("GS_RUN_DIR", raising=False)
    return state_


class Graph:
    """A fake driver that keeps the part of the graph the steps under test read and write."""

    def __init__(self, events=None):
        self.calls = []
        self.events = events if events is not None else []
        self.samples: set[int] = set()
        self.edges: dict[tuple[int, int], dict] = {}      # (child id, parent id) to the edge's properties
        self.retire_candidates: list[dict] = []
        self.studies: list[dict] = []
        self.graphmeta: dict = {}
        self.attribute_state: list[dict] = []

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        self.calls.append(SimpleNamespace(query=query, params=params, kwargs=kwargs))
        records, counters = self.answer(query, params)
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace(**counters)))

    def queries(self):
        return [c.query for c in self.calls]

    def stored(self, pair):
        edge = self.edges[pair]
        return {key: edge.get(key) for key in q.EDGE_LABEL_KEYS}

    def answer(self, query, params):
        if query == q.WRITE_MISSING_LINEAGE:
            matched = created = 0
            for child, parent in params["rows"]:
                if child in self.samples and parent in self.samples:
                    matched += 1
                    if (child, parent) not in self.edges:
                        self.edges[(child, parent)] = {"child_id": child, "parent_id": parent}
                        created += 1
            return [{"matched": matched}], {"relationships_created": created}
        if query == run.LABEL_EDGES:
            self.events.append("label_stream")
            return [{"child_id": c, "parent_id": p, "stored": self.stored((c, p))} for c, p in self.edges], {}
        if query == run.LABELS_FOR_PAIRS:
            return [{"child_id": c, "parent_id": p, "stored": self.stored((c, p))}
                    for c, p in params["rows"] if (c, p) in self.edges], {}
        if query in (q.WRITE_EDGE_LABELS_NEW, q.WRITE_EDGE_LABELS_CHANGED):
            return [self._write_labels(query, params["rows"])], {}
        if query == q.RETIRE_CANDIDATES:
            return [r for r in self.retire_candidates if r["id"] in params["ids"]], {}
        if query in (q.DELETE_RETIRED, q.RELABEL_ORPHANS_BY_ELEMENT_ID, q.RELABEL_ORPHANS):
            return [{"n": len(params.get("element_ids") or params.get("ids") or ())}], {}
        if query == run.SEEK_KEYED_STUDIES:
            return [{"n": sum(1 for s in self.studies if s.get("seek_study_id") is not None)}], {}
        if query == run.STUDIES_KEYED_BY_ID:
            self.events.append("study_read")
            return [{"element_id": s["element_id"], "id": s["id"], "title": s["title"], "paper": s["paper"]}
                    for s in self.studies if s.get("id") is not None and s.get("seek_study_id") is None], {}
        if query == run.REKEY_STUDY:
            self.events.append("study_rekey")
            moved = 0
            for s in self.studies:
                if s["element_id"] in params["element_ids"] and s.get("seek_study_id") is None:
                    s["seek_study_id"], s["id"] = s["id"], None
                    moved += 1
            return [{"n": moved}], {}
        if query in (q.WRITE_GRAPHMETA, q.WRITE_GRAPHMETA_WITH_LABEL_MAPS):
            self.graphmeta.update(params)
            return [], {}
        if query == q.READ_GRAPHMETA:
            return ([{"props": dict(self.graphmeta)}] if self.graphmeta else []), {}
        if query == run.ATTRIBUTE_STATE:
            return list(self.attribute_state), {}
        return [], {}

    def _write_labels(self, query, rows):
        matched = written = pairs = 0
        for row in rows:
            edge = self.edges.get((row["child_id"], row["parent_id"]))
            if edge is None:
                continue
            matched += 1
            pairs += 1
            if query == q.WRITE_EDGE_LABELS_NEW:
                passes = all(edge.get(key) is None for key in q.EDGE_SINGULAR_ASSAY_KEYS)
            else:
                passes = all(edge.get(key) == row["stored"][key] for key in q.EDGE_LABEL_KEYS)
            if not passes:
                continue
            for key in q.EDGE_LABEL_KEYS:
                if row["labels"][key] is None:
                    edge.pop(key, None)
                else:
                    edge[key] = row["labels"][key]
            edge.pop("assay_title", None)
            written += 1
        return {"matched": matched, "written": written, "pairs": pairs}


class Writers:
    """Replaces the writer functions the steps under test do not need with recorders; wraps the rest (the lineage,
    label, retire, orphan and GraphMeta functions) so they run for real against the fake graph. Every call is
    appended to ``events`` by name."""

    REAL = ("retire_samples", "relabel_orphans", "write_missing_lineage", "write_edge_labels", "write_graphmeta")

    def __init__(self, monkeypatch, graph: Graph, ghosts=None):
        self.graph = graph
        self.calls = []
        self.ghosts = ghosts if ghosts is not None else NO_GHOSTS
        fakes = {
            "find_ghosts": lambda d, db, ids, uuids: copy.deepcopy(self.ghosts),
            "delete_ghosts": lambda d, db, element_ids: {"ghosts_deleted": len(element_ids)},
            "archive_and_drop_child_of": lambda d, db, path, declared: {
                "child_of_pairs": 0, "child_of_undeclared": 0, "child_of_deleted": 0, "archive_path": None},
            "ensure_constraints_v11": lambda d, db: {"schema_statements": 14},
            "write_sample_types": lambda d, db, rows: {"sample_types_written": len(rows),
                                                       "graph_only_sample_types": []},
            "write_attributes": lambda d, db, rows: {"attributes_written": len(rows), "attributes_without_type": 0},
            "write_projects": lambda d, db, rows: {"projects_written": len(rows)},
            "write_people_and_memberships": lambda d, db, rows: {"memberships_written": len(rows)},
            "write_investigation_projects": lambda d, db, invs, links: {"investigations_written": len(invs)},
            "write_samples": self._write_samples,
            "archive_and_drop_undeclared_derived_from": lambda d, db, path, declared: {
                "derived_from_between_samples": 0, "derived_from_undeclared": 0, "derived_from_deleted": 0,
                "derived_from_archive_path": None},
            "write_seek_studies": lambda d, db, links: {"in_study_written": len(links), "in_study_dropped": 0},
            "write_attribute_counts": lambda d, db, counts: {"attribute_counts_set": len(counts)},
            "write_sample_type_counts": lambda d, db: {"sample_type_counts_set": 2},
            "ensure_index_budget": lambda d, db, census, bench_keys=frozenset(): [],
            "ensure_fulltext": lambda d, db: {"fulltext_index": "sample_search_text"},
            "await_indexes": lambda d, db: {"indexes_online": 20},
        }
        for name, fn in fakes.items():
            monkeypatch.setattr(writer, name, self._recording(name, fn))
        for name in self.REAL:
            monkeypatch.setattr(writer, name, self._recording(name, getattr(writer, name)))

    def _recording(self, name, fn):
        def call(*args, **kwargs):
            self.calls.append(SimpleNamespace(name=name, args=args, kwargs=kwargs))
            self.graph.events.append(name)
            return fn(*args, **kwargs)
        return call

    def _write_samples(self, d, db, projections, chunk=5000):
        self.graph.samples.update(p.id for p in projections)
        return {"samples_written": len(projections), "of_type": len(projections), "untyped": 0, "in_project": 0,
                "in_project_expected": 0, "in_project_missing": 0, "cast_failures": 0}

    def names(self):
        return [c.name for c in self.calls]

    def of(self, name):
        return [c for c in self.calls if c.name == name]


@pytest.fixture
def lock(monkeypatch):
    """Record the graph-write lock: its timeout, and where it was taken and released among the run's events."""
    record = SimpleNamespace(acquired=True, timeouts=[], events=None)

    @contextmanager
    def graph_write_lock(timeout_s):
        record.timeouts.append(timeout_s)
        if record.events is not None:
            record.events.append("lock")
        try:
            yield record.acquired
        finally:
            if record.events is not None:
                record.events.append("unlock")

    monkeypatch.setattr(state, "graph_write_lock", graph_write_lock)
    return record


def _full(graph, tmp_path, **kwargs):
    return run.full_sync(graph, "neo4j", run_dir=str(tmp_path), **kwargs)


def _projections(writers):
    return {p.id: p for call in writers.of("write_samples") for p in call.args[2]}


def _one_run(kind="full"):
    (record,) = GraphSyncRun.objects.filter(kind=kind)
    return record


# --- interfaces ----------------------------------------------------------------------------------

def test_full_sync_and_catalog_sync_keep_their_signatures():
    params = list(inspect.signature(run.full_sync).parameters.values())
    assert [(p.name, p.default) for p in params[:6]] == [
        ("driver", inspect.Parameter.empty), ("db", inspect.Parameter.empty), ("chunk", writer.SAMPLE_CHUNK),
        ("dry_run", False), ("run_dir", None), ("bench_keys", frozenset())]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is not inspect.Parameter.empty
               for p in params[6:])
    assert "apply_label_changes" in {p.name for p in params[6:]}
    cparams = list(inspect.signature(run.catalog_sync).parameters.values())
    assert [(p.name, p.default) for p in cparams[:3]] == [
        ("driver", inspect.Parameter.empty), ("db", inspect.Parameter.empty), ("dry_run", False)]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is not inspect.Parameter.empty
               for p in cparams[3:])


# --- the order -----------------------------------------------------------------------------------

def test_full_sync_runs_the_schema_1_2_steps_in_order(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    lock.events = graph.events
    writers = Writers(monkeypatch, graph)
    report = _full(graph, tmp_path, chunk=2)

    assert report["status"] == "ok"
    assert writers.names() == [
        "find_ghosts", "delete_ghosts", "retire_samples", "relabel_orphans", "archive_and_drop_child_of",
        "ensure_constraints_v11", "write_sample_types", "write_attributes", "write_projects",
        "write_people_and_memberships", "write_investigation_projects", "write_samples", "write_samples",
        "write_missing_lineage", "archive_and_drop_undeclared_derived_from", "write_edge_labels",
        "write_seek_studies", "write_attributes", "write_attribute_counts", "write_sample_type_counts",
        "ensure_index_budget", "ensure_fulltext", "await_indexes", "write_graphmeta"]
    events = graph.events
    # The label step reads every edge after the lineage steps; the Study re-key is read before the SEEK studies.
    assert (events.index("archive_and_drop_undeclared_derived_from") < events.index("label_stream")
            < events.index("write_edge_labels") < events.index("study_read") < events.index("write_seek_studies"))
    # relabel_orphans is left with the id-less nodes only; the graph-only ids go to the deletion rule.
    (relabel,) = writers.of("relabel_orphans")
    assert list(relabel.args[2]) == []


def test_every_sample_carries_its_source_hash_and_parent_lists(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    _full(graph, tmp_path)

    cat = run.build_catalog()
    written = _projections(writers)
    for row in SAMPLES:
        type_id = row["sample_type_id"]
        expected = projection.source_hash(row, cat.type_titles[type_id], cat.value_types.get(type_id, {}),
                                          PROJECT_LINKS[row["id"]], ASSAY_LINKS[row["id"]])
        assert written[row["id"]].props["source_hash"] == expected
    assert written[10].props["parent_titles"] == [] and written[10].props["parent_title_hashes"] == []
    assert written[11].props["parent_titles"] == ["lung-a"]
    assert written[12].props["parent_titles"] == ["lung-a", "free text parent"]
    assert written[12].props["parent_title_hashes"] == [hash_identity("lung-a"), hash_identity("free text parent")]


# --- the label step ------------------------------------------------------------------------------

def test_a_full_sync_onto_an_empty_graph_labels_every_edge_it_creates(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    assert set(graph.edges) == {(11, 10), (12, 10)}
    assert graph.stored((11, 10)) == LABELS_11_10
    assert graph.stored((12, 10)) == LABELS_12_10
    for (child, parent), edge in graph.edges.items():
        if set(ASSAY_LINKS[child]) & set(ASSAY_LINKS[parent]):
            assert edge.get("internal_assay_id") is not None, (child, parent)
    assert (report["labels_edges"], report["labels_new"], report["labels_written"]) == (2, 2, 2)
    assert report["labels_changed"] == report["labels_cleared"] == report["labels_plural_missing"] == 0


def test_a_second_full_sync_finds_every_label_equal_and_writes_none(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    _full(graph, tmp_path / "one")
    report = _full(graph, tmp_path / "two")

    assert report["labels_equal"] == 2 and report["labels_new"] == 0
    assert len(writers.of("write_edge_labels")) == 1


def test_a_stored_label_that_differs_is_reported_and_kept(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    stale = dict(LABELS_11_10, internal_assay_id=98, internal_assay_title="Old title", internal_assay_ids=[98],
                 internal_assay_titles=["Old title"])
    graph.edges[(11, 10)] = dict(stale, assay_title="legacy")
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    assert graph.stored((11, 10)) == stale and graph.edges[(11, 10)]["assay_title"] == "legacy"
    assert graph.stored((12, 10)) == LABELS_12_10          # the edge the run created is still labelled
    assert (report["labels_changed"], report["labels_new"], report["labels_written"]) == (1, 1, 1)
    assert report["labels_by_property"]["changed"] == {
        "internal_assay_id": 1, "internal_assay_title": 1, "internal_assay_ids": 1, "internal_assay_titles": 1}
    (example,) = report["labels_examples"]["changed"]
    assert (example["child_id"], example["parent_id"]) == (11, 10)
    assert example["stored"]["internal_assay_id"] == 98 and example["computed"]["internal_assay_id"] == 99
    saved = json.loads((tmp_path / run.REPORT_FILE).read_text())
    assert saved["labels_changed"] == 1 and saved["steps"]["labels"]["labels_changed"] == 1


def test_a_missing_plural_list_is_reported_and_not_written(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    singular_only = {k: v for k, v in LABELS_11_10.items() if k not in ("internal_assay_ids", "internal_assay_titles")}
    graph.edges[(11, 10)] = dict(singular_only)
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    assert "internal_assay_ids" not in graph.edges[(11, 10)]
    assert report["labels_plural_missing"] == 1
    assert report["labels_by_property"]["plural_missing"] == {"internal_assay_ids": 1, "internal_assay_titles": 1}


def test_a_cleared_label_is_reported_and_kept(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    sheet = dict(LABELS_12_10, assay_id=502, internal_assay_id=502, internal_assay_title="Other",
                 internal_assay_ids=[502], internal_assay_titles=["Other"])
    graph.edges[(12, 10)] = dict(sheet)
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    assert graph.stored((12, 10)) == sheet
    assert report["labels_cleared"] == 1
    assert report["labels_by_property"]["cleared"]["internal_assay_id"] == 1


def test_apply_label_changes_writes_changed_cleared_and_plural_missing_edges(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    graph.edges[(11, 10)] = dict(LABELS_11_10, internal_assay_title="Old title", internal_assay_titles=["Old title"])
    graph.edges[(12, 10)] = dict(LABELS_12_10, assay_id=502, internal_assay_id=502, internal_assay_title="Other")
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path, apply_label_changes=True)

    assert graph.stored((11, 10)) == LABELS_11_10
    assert graph.stored((12, 10)) == LABELS_12_10
    assert report["apply_label_changes"] is True
    assert (report["labels_changed"], report["labels_cleared"], report["labels_written"]) == (1, 1, 2)
    assert report["labels_skipped_changed"] == 0
    # The approved write compares with values read just before it, not with the first pass's.
    assert any(c.query == run.LABELS_FOR_PAIRS for c in graph.calls)
    assert all(c.params.get("rows") for c in graph.calls if c.query == q.WRITE_EDGE_LABELS_CHANGED)


def test_graphmeta_gets_the_label_maps_hash(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    expected = labels.label_maps_hash(ASSAY_MAP, SOPS)
    assert graph.graphmeta["label_maps_hash"] == expected and graph.graphmeta["schema_version"] == "1.2"
    assert report["label_maps_hash"] == expected


# --- the deletion rule ---------------------------------------------------------------------------

def test_graph_only_samples_follow_the_deletion_rule(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.retire_candidates = [
        {"element_id": "4:s:99", "id": 99, "uuid": "TIS-X-99", "type": "TIS", "synced": True, "incident_edges": 3},
        {"element_id": "4:o:98", "id": 98, "uuid": "TIS-X-98", "type": "TIS", "synced": False, "incident_edges": 1}]
    ghosts = dict(NO_GHOSTS, orphan_ids=[98, 99], idless_element_ids=["4:x:1"])
    Writers(monkeypatch, graph, ghosts=ghosts)
    report = _full(graph, tmp_path)

    deleted = [c.params["element_ids"] for c in graph.calls if c.query == q.DELETE_RETIRED]
    orphaned = [c.params["element_ids"] for c in graph.calls if c.query == q.RELABEL_ORPHANS_BY_ELEMENT_ID]
    assert deleted == [["4:s:99"]]
    assert sorted(orphaned) == [["4:o:98"], ["4:x:1"]]
    assert q.RELABEL_ORPHANS not in graph.queries()      # no graph-only id is relabelled with its T_ labels kept
    assert "REMOVE s:$(types)" in q.RELABEL_ORPHANS_BY_ELEMENT_ID
    rows = (tmp_path / run.RETIRED_FILE).read_text().splitlines()
    assert rows[1:] == ["99\tTIS-X-99\tTIS\t3"]
    assert (report["retired_deleted"], report["retired_orphaned"]) == (1, 1)


# --- the lock, the run record and the outbox -----------------------------------------------------

def test_the_lock_is_held_for_the_whole_run(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    lock.events = graph.events
    Writers(monkeypatch, graph)
    _full(graph, tmp_path, lock_timeout_s=5)

    assert lock.timeouts == [5]
    assert graph.events[0] == "lock" and graph.events[-1] == "unlock"
    assert graph.events.count("lock") == 1


def test_the_default_lock_wait_is_the_full_sync_s(world, monkeypatch, tmp_path, lock):
    Writers(monkeypatch, Graph())
    _full(Graph(), tmp_path)
    assert lock.timeouts == [run.FULL_LOCK_TIMEOUT_S]


def test_a_lock_that_is_not_acquired_refuses_before_reading_the_graph(world, monkeypatch, tmp_path, lock):
    lock.acquired = False
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    with pytest.raises(run.PreflightError, match="graph-write lock"):
        _full(graph, tmp_path)
    assert writers.names() == [] and graph.calls == []
    assert _one_run().status == "refused"
    assert json.loads((tmp_path / run.REPORT_FILE).read_text())["status"] == "refused"


def test_a_run_records_ok_with_its_counts(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path, trigger="loop")

    record = _one_run()
    assert record.status == "ok" and record.finished_at is not None
    assert record.counts_json["trigger"] == "loop"
    assert record.counts_json["samples_written"] == 3 and record.counts_json["labels_new"] == 2
    assert record.started_at.isoformat(timespec="seconds") == report["started_at"]
    json.dumps(record.counts_json)


def test_a_refused_run_records_refused(world, monkeypatch, tmp_path, lock):
    Writers(monkeypatch, Graph(), ghosts=dict(NO_GHOSTS, unresolved_duplicate_ids=[10]))
    with pytest.raises(run.PreflightError, match="unresolved_duplicate_ids"):
        _full(Graph(), tmp_path)
    record = _one_run()
    assert record.status == "refused" and record.counts_json["problems"]


def test_a_run_without_a_run_directory_records_refused(world, monkeypatch, lock):
    Writers(monkeypatch, Graph())
    with pytest.raises(run.PreflightError, match="run directory"):
        run.full_sync(Graph(), "neo4j")
    assert _one_run().status == "refused"


def test_no_record_writes_no_run_row(world, monkeypatch, tmp_path, lock):
    Writers(monkeypatch, Graph())
    _full(Graph(), tmp_path, record=False)
    assert not GraphSyncRun.objects.exists()


def test_outbox_rows_enqueued_before_the_start_are_marked_done_on_success(world, monkeypatch, tmp_path, lock):
    for kind, key in [("samples", "sample:1"), ("catalog", "*"), ("full", "slot:2026-W38"),
                      ("drift", "slot:2026-09-15")]:
        state.enqueue(kind, key)
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    projects = writers._recording("write_projects", lambda d, db, rows: state.enqueue("samples", "sample:2") or {})
    monkeypatch.setattr(writer, "write_projects", projects)

    report = _full(graph, tmp_path)

    done = {(r.kind, r.key): r.done_at is not None for r in GraphSyncOutbox.objects.all()}
    assert done == {("samples", "sample:1"): True, ("catalog", "*"): True, ("full", "slot:2026-W38"): True,
                    ("drift", "slot:2026-09-15"): False,      # a drift check is still owed after a sync
                    ("samples", "sample:2"): False}            # enqueued after the run began reading
    assert report["outbox_marked_done"] == 3


def test_a_failed_run_records_failed_and_marks_no_outbox_row(world, monkeypatch, tmp_path, lock):
    state.enqueue("samples", "sample:1")
    Writers(monkeypatch, Graph())

    def boom(*args, **kwargs):
        raise RuntimeError("neo4j went away")

    monkeypatch.setattr(writer, "write_seek_studies", boom)
    with pytest.raises(RuntimeError):
        _full(Graph(), tmp_path)
    assert _one_run().status == "failed"
    assert GraphSyncOutbox.objects.get().done_at is None


def test_a_refused_run_marks_no_outbox_row(world, monkeypatch, tmp_path, lock):
    state.enqueue("samples", "sample:1")
    Writers(monkeypatch, Graph(), ghosts=dict(NO_GHOSTS, unresolved_duplicate_ids=[10]))
    with pytest.raises(run.PreflightError):
        _full(Graph(), tmp_path)
    assert GraphSyncOutbox.objects.get().done_at is None


def test_a_dry_run_takes_no_lock_records_nothing_and_writes_nothing(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    monkeypatch.chdir(tmp_path)
    report = run.full_sync(graph, "neo4j", dry_run=True)

    assert report["status"] == "dry_run"
    assert lock.timeouts == [] and not GraphSyncRun.objects.exists()
    assert writers.names() == ["find_ghosts"]
    assert all(c.kwargs.get("routing_") is not None for c in graph.calls)
    assert list(tmp_path.iterdir()) == []


# --- SEEK Study nodes keyed on id ----------------------------------------------------------------

def test_seek_study_nodes_keyed_by_id_move_to_seek_study_id(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.studies = [
        {"element_id": "4:st:7", "id": 7, "title": "S", "paper": False},             # a SEEK study batch upload keyed
        {"element_id": "4:st:8", "id": 8, "title": "Paper", "paper": True},          # carries a DOI or PMID
        {"element_id": "4:st:9", "id": 9, "title": "Another study", "paper": False},  # SEEK's title differs
        {"element_id": "4:st:50", "id": 50, "title": "Local", "paper": False}]       # no SEEK study 50
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    (rekey,) = [c for c in graph.calls if c.query == run.REKEY_STUDY]
    assert rekey.params["element_ids"] == ["4:st:7"]
    assert graph.studies[0]["seek_study_id"] == 7 and graph.studies[0]["id"] is None
    assert graph.events.index("study_rekey") < graph.events.index("write_seek_studies")
    assert report["studies_rekeyed"] == 1 and report["study_ids_left_keyed_by_id"] == [8, 9]


def test_a_graph_that_already_keys_seek_studies_on_seek_study_id_is_not_rekeyed(world, monkeypatch, tmp_path,
                                                                                lock):
    graph = Graph()
    graph.studies = [{"element_id": "4:st:1", "id": None, "seek_study_id": 9, "title": "S9", "paper": False},
                     {"element_id": "4:st:7", "id": 7, "title": "S", "paper": False}]
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    assert run.REKEY_STUDY not in graph.queries() and run.STUDIES_KEYED_BY_ID not in graph.queries()
    assert report["studies_rekeyed"] == 0


# --- catalog_sync --------------------------------------------------------------------------------

def _catalog_graph(version="1.2"):
    graph = Graph()
    if version is not None:
        graph.graphmeta = {"schema_version": version, "catalog_hash": "old"}
    graph.attribute_state = [{"key": "26:Organ", "declared": True, "sample_type_id": 26, "title": "Organ",
                              "sample_count": 7}]
    return graph


def test_catalog_sync_holds_the_lock_and_records_a_run(world, monkeypatch, lock):
    graph = _catalog_graph()
    lock.events = graph.events
    writers = Writers(monkeypatch, graph)
    report = run.catalog_sync(graph, "neo4j")

    assert report["status"] == "ok"
    assert lock.timeouts == [run.CATALOG_LOCK_TIMEOUT_S]
    assert graph.events[0] == "lock" and graph.events[-1] == "unlock"
    assert writers.names() == ["write_sample_types", "write_attributes", "write_attribute_counts",
                               "write_sample_type_counts", "write_graphmeta"]
    assert "label_maps_hash" not in writers.of("write_graphmeta")[0].kwargs
    record = _one_run("catalog")
    assert record.status == "ok" and record.counts_json["trigger"] == "command"


@pytest.mark.parametrize("version", ["1.1", None])
def test_catalog_sync_refuses_a_graph_not_at_the_writers_version(world, monkeypatch, lock, version):
    graph = _catalog_graph(version)
    writers = Writers(monkeypatch, graph)
    with pytest.raises(run.PreflightError, match="full sync"):
        run.catalog_sync(graph, "neo4j")
    assert writers.names() == []
    assert _one_run("catalog").status == "refused"


def test_catalog_sync_refuses_when_the_lock_is_not_acquired(world, monkeypatch, lock):
    lock.acquired = False
    graph = _catalog_graph()
    writers = Writers(monkeypatch, graph)
    with pytest.raises(run.PreflightError, match="graph-write lock"):
        run.catalog_sync(graph, "neo4j", lock_timeout_s=2)
    assert writers.names() == [] and lock.timeouts == [2]
    assert _one_run("catalog").status == "refused"


def test_catalog_sync_dry_run_reports_the_version_and_takes_no_lock(world, monkeypatch, lock):
    graph = _catalog_graph("1.1")
    writers = Writers(monkeypatch, graph)
    report = run.catalog_sync(graph, "neo4j", dry_run=True)

    assert report["status"] == "dry_run" and report["graph_schema_version"] == "1.1"
    assert writers.names() == [] and lock.timeouts == [] and not GraphSyncRun.objects.exists()


# --- the label index -----------------------------------------------------------------------------

def test_sample_links_answer_by_sample_id():
    links = run.SampleLinks()
    links.add(10, [500])
    links.add(11, [501, 500, 500])
    links.add(3, [7])            # out of id order: answered all the same
    links.add(12, [])
    assert links.get(11) == (500, 501) and links.get(10) == (500,) and links.get(3) == (7,)
    assert links.get(12) == () and links.get(99) == ()
    assert links.get("11") == () and links.get(None) == () and links.get(True) == ()
    links.add(13, [1 << 40])     # an id outside the packed range still answers
    assert links.get(13) == (1 << 40,)
