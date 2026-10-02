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
import random
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.utils import timezone

from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.graph_sync import labels, projection, run, sources, state, study_links, writer
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun
from nextseek_api.tests import graph_sync_pages as pages
from nextseek_graph import schema

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
        "iter_seek_study_links": lambda: iter([(11, 7)]),
        "internal_assays": lambda: [{"id": 99, "title": "Patient Visit"}],
        "assay_internal_pairs": lambda: [(500, 99)],
        "assay_studies": lambda: [(500, 7), (501, 7), (502, 7)],
        "assay_context_rows": lambda: [],
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
        self.study_duplicates: list[dict] = []
        self.samples_with_assay_edges: list[int] = []
        self.page_ids = pages.ONE_PAGE                     # the Sample ids the paged reads' bounds see
        self.read_budget = None                            # records one read may stream (pages.budgeted)

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        self.calls.append(SimpleNamespace(query=query, params=params, kwargs=kwargs))
        paged = pages.page_answer(query, params, {run.LABEL_EDGES: "child_id"},
                                  lambda template: self.answer(template, params)[0], self.page_ids)
        records, counters = (paged, {}) if paged is not None else self.answer(query, params)
        if result_transformer_ is not None:
            if self.read_budget is not None:
                result_transformer_ = pages.budgeted(result_transformer_, self.read_budget)
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
        if query in (run.LABEL_EDGES, LABEL_EDGES_AT_C6089B4A):
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
                    s["seek_study_id"] = s["id"]
                    moved += 1
            return [{"n": moved}], {}
        if query in (q.WRITE_GRAPHMETA, q.WRITE_GRAPHMETA_WITH_LABEL_MAPS):
            self.graphmeta.update(params)
            return [], {}
        if query == q.READ_GRAPHMETA:
            return ([{"props": dict(self.graphmeta)}] if self.graphmeta else []), {}
        if query == run.ATTRIBUTE_STATE:
            return list(self.attribute_state), {}
        if query == q.STUDY_SEEK_ID_DUPLICATES:
            return list(self.study_duplicates), {}
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
        self.sample_edge_rows = []
        fakes = {
            "sample_ids_with_assay_edges": lambda d, db: iter(list(self.graph.samples_with_assay_edges)),
            "replace_sample_assay_edges": self._replace_sample_assay_edges,
            "write_assays": lambda d, db, rows: {"assays_written": len(rows)},
            "replace_assay_catalog_edges": lambda d, db, accepted, generates: {
                "accepted_by": len(accepted), "accepted_by_written": len(accepted),
                "generates": len(generates), "generates_written": len(generates)},
            "replace_assay_runs": lambda d, db, rows, studies, tables=None: {
                "assay_runs": len(rows), "assay_runs_written": len(rows), "assay_runs_dropped": 0},
            "delete_gone_assays": lambda d, db, ids: {"assays_deleted": 0, "assay_edges_deleted_with_gone_assays": 0},
            "find_ghosts": lambda d, db, ids, uuids: copy.deepcopy(self.ghosts),
            "delete_ghosts": lambda d, db, element_ids: {"ghosts_deleted": len(element_ids)},
            "archive_and_drop_child_of": lambda d, db, path, declared: {
                "child_of_pairs": 0, "child_of_undeclared": 0, "child_of_deleted": 0, "archive_path": None},
            "ensure_constraints_v11": lambda d, db: {"schema_statements": 14},
            "write_sample_types": lambda d, db, rows, archive_path=None: {"sample_types_written": len(rows),
                                                       "graph_only_sample_types": []},
            "write_attributes": lambda d, db, rows: {"attributes_written": len(rows), "attributes_without_type": 0},
            "write_projects": lambda d, db, rows: {"projects_written": len(rows)},
            "write_people_and_memberships": lambda d, db, rows: {"memberships_written": len(rows)},
            "write_investigation_projects": lambda d, db, invs, links, archive_path=None, seek_study_ids=None: {
                "investigations_written": len(invs)},
            "write_samples": self._write_samples,
            "archive_and_drop_undeclared_derived_from": lambda d, db, path, declared: {
                "derived_from_between_samples": 0, "derived_from_undeclared": 0, "derived_from_deleted": 0,
                "derived_from_archive_path": None},
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
        monkeypatch.setattr(study_links, "rebuild_in_study", self._recording("rebuild_in_study", self._rebuild))

    @staticmethod
    def _rebuild(d, db, **kwargs):
        return {"status": "ok", "dry_run": False, "remove": kwargs["remove"], "in_study_added": 1,
                "in_study_removed": 0}

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

    def _replace_sample_assay_edges(self, d, db, rows, chunk=5000):
        rows = list(rows)
        self.sample_edge_rows.extend(rows)
        written = sum(len(r["inputs"]) + len(r["outputs"]) for r in rows)
        return {"assay_edge_samples": len(rows), "assay_edge_samples_missing": 0, "assay_edges_written": written,
                "assay_edges_dropped": 0}

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

def test_full_sync_runs_its_steps_in_order(world, monkeypatch, tmp_path, lock):
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
        "rebuild_in_study", "write_assays", "replace_assay_catalog_edges", "sample_ids_with_assay_edges",
        "replace_sample_assay_edges", "replace_assay_runs", "delete_gone_assays",
        "write_attributes", "write_attribute_counts", "write_sample_type_counts",
        "ensure_index_budget", "ensure_fulltext", "await_indexes", "write_graphmeta"]
    events = graph.events
    # The label step reads every edge after the lineage steps; the Study re-key is read before the SEEK studies.
    assert (events.index("archive_and_drop_undeclared_derived_from") < events.index("label_stream")
            < events.index("write_edge_labels") < events.index("study_read") < events.index("rebuild_in_study"))
    # The assay layer: after the rebuild, nodes before the sample edges, RUN_IN after them, the gone Assays last.
    assert (events.index("rebuild_in_study") < events.index("write_assays")
            < events.index("replace_sample_assay_edges") < events.index("replace_assay_runs")
            < events.index("delete_gone_assays") < events.index("write_graphmeta"))
    # relabel_orphans is left with the id-less nodes only; the graph-only ids go to the deletion rule.
    (relabel,) = writers.of("relabel_orphans")
    assert list(relabel.args[2]) == []


# --- the assay layer (schema 1.3) ----------------------------------------------------------------

def test_the_full_sync_writes_every_samples_assay_edges_from_the_label_stream(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples_with_assay_edges = [12]          # an edge from before, and no role now
    writers = Writers(monkeypatch, graph)
    report = _full(graph, tmp_path, chunk=2)

    # (11, 10) shares SEEK assay 500, mapped to 99; (12, 10) shares nothing
    assert {r["id"]: r for r in writers.sample_edge_rows} == {
        10: {"id": 10, "inputs": [{"assay_id": 99, "seek_assay_ids": [500]}], "outputs": []},
        11: {"id": 11, "inputs": [], "outputs": [{"assay_id": 99, "seek_assay_ids": [500]}]},
        12: {"id": 12, "inputs": [], "outputs": []}}
    assert [len(c.args[2]) for c in writers.of("replace_sample_assay_edges")] == [2, 1]
    assert (report["assay_role_codes"], report["samples_holding_assay_edges_before"]) == (2, 1)
    (runs,) = writers.of("replace_assay_runs")
    assert runs.args[2] == [{"assay_id": 99, "study_id": 7, "seek_assay_ids": [500]}]
    (gone,) = writers.of("delete_gone_assays")
    assert gone.args[2] == [99]


def test_role_codes_keep_a_million_links_at_eight_bytes_each():
    import tracemalloc

    roles = run.RoleCodes({7: (99,)})
    tracemalloc.start()
    try:
        for i in range(1, 500_001):
            roles.add_edge(2 * i, 2 * i - 1, (7,), (7,))
        held, _ = tracemalloc.get_traced_memory()
        assert len(roles) == 1_000_000
        assert held < 12 * 2 ** 20                   # the array: 8 bytes a code and its growth headroom
        tracemalloc.reset_peak()
        samples = sum(1 for _ in roles.by_sample())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert samples == 1_000_000
    assert peak < 96 * 2 ** 20                       # sorting once costs a list of the codes, never a dict per sample
    assert len(roles) == 0                           # by_sample freed the codes


def test_role_codes_answer_by_sample_with_every_role_once():
    roles = run.RoleCodes({5: (99,), 7: (99, 120), 9: (130,)})
    roles.add_edge(2, 1, (5, 7, 13), (5, 7))         # 13 is not mapped
    roles.add_edge(2, 1, (5,), (5,))                  # the same roles again
    roles.add_edge(3, 2, (9,), (9, 5))
    roles.add_edge(4, 4, (5,), (5,))                  # a self-loop is not lineage
    assert list(roles.by_sample()) == [
        (1, {("INPUT_TO", 99): {5, 7}, ("INPUT_TO", 120): {7}}),
        (2, {("OUTPUT_OF", 99): {5, 7}, ("OUTPUT_OF", 120): {7}, ("INPUT_TO", 130): {9}}),
        (3, {("OUTPUT_OF", 130): {9}})]


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
    graph.edges[(11, 10)] = dict(LABELS_11_10, internal_assay_id=98, internal_assay_title="Old title",
                                 internal_assay_titles=["Old title"])
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


def test_a_rename_is_written_without_approval(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    graph.edges[(11, 10)] = dict(LABELS_11_10, internal_assay_title="Old title", internal_assay_titles=["Old title"])
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)
    assert graph.stored((11, 10)) == LABELS_11_10
    assert (report["labels_renamed"], report["labels_refreshed"], report["labels_changed"]) == (1, 1, 0)
    assert report["labels_by_property"]["renamed"] == {"internal_assay_title": 1, "internal_assay_titles": 1}


def test_a_rename_that_became_a_change_before_the_write_is_not_written(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    graph.edges[(11, 10)] = dict(LABELS_11_10, internal_assay_title="Old title", internal_assay_titles=["Old title"])
    real = graph.answer

    def meanwhile(query, params):
        if query == run.LABELS_FOR_PAIRS:            # another writer moved the edge to another assay
            graph.edges[(11, 10)]["internal_assay_id"] = 98
        return real(query, params)

    graph.answer = meanwhile
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)
    assert graph.edges[(11, 10)]["internal_assay_id"] == 98 and report["labels_refreshed"] == 0


def test_relabel_all_classifies_every_edge_and_a_dry_run_writes_nothing(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.samples.update({10, 11, 12})
    graph.graphmeta = {"schema_version": schema.SCHEMA_VERSION, "catalog_hash": "c"}
    graph.edges[(11, 10)] = dict(LABELS_11_10, internal_assay_title="Old title", internal_assay_titles=["Old title"])
    dry = run.relabel_all(graph, "neo4j", dry_run=True)
    assert (dry["status"], dry["labels_renamed"], dry["labels_refreshed"]) == ("dry_run", 1, 0)
    assert lock.timeouts == [] and graph.stored((11, 10))["internal_assay_title"] == "Old title"
    done = run.relabel_all(graph, "neo4j", record=False)
    assert (done["status"], done["labels_refreshed"]) == ("ok", 1)
    assert graph.stored((11, 10)) == LABELS_11_10 and lock.timeouts == [run.FULL_LOCK_TIMEOUT_S]


def test_relabel_all_refuses_a_graph_not_at_the_writers_version(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.graphmeta = {"schema_version": "1.1"}
    with pytest.raises(run.PreflightError, match="schema"):
        run.relabel_all(graph, "neo4j", record=False)
    assert lock.timeouts == []


# --- the label step reads in pages (Neo4j's 120 s transaction limit) ----------------------------

# The label step as it was at c6089b4a, frozen: one read transaction over every DERIVED_FROM between two Sample nodes.
# The paged step must give the same report, role codes and writes on any graph.
LABEL_EDGES_AT_C6089B4A = """
MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample)
RETURN c.id AS child_id, p.id AS parent_id, {stored} AS stored
""".replace("{stored}", run._STORED_LABELS)


def _label_edges_at_c6089b4a(driver, db, label_sources, *, apply_label_changes=False, dry_run=False,
                             internal_by_seek=None, role_sink=None):
    from array import array
    from collections import Counter

    def classify_all(result):
        classes: Counter = Counter()
        by_property = {cls: Counter() for cls in run._REPORTED_CLASSES}
        examples = {cls: [] for cls in run._REPORTED_CLASSES}
        targets, refresh_targets = array("q"), array("q")
        roles = run.RoleCodes(internal_by_seek) if internal_by_seek is not None else None
        edges = legacy = 0
        for record in result:
            edges += 1
            child, parent = record["child_id"], record["parent_id"]
            if not (run._is_packable(child) and run._is_packable(parent)):
                legacy += 1
                continue
            if roles is not None:
                roles.add_edge(child, parent, label_sources.assays.get(child), label_sources.assays.get(parent))
            stored = record["stored"] or {}
            computed = label_sources.edge(child, parent)
            cls = labels.classify(stored, computed)
            classes[cls] += 1
            if cls in by_property:
                diff = labels.differences(stored, computed)
                by_property[cls].update(diff)
                if len(examples[cls]) < run.EXAMPLES:
                    examples[cls].append({"child_id": child, "parent_id": parent,
                                          "stored": {k: stored.get(k) for k in diff},
                                          "computed": {k: computed[k] for k in diff}})
            if cls == labels.NEW or (apply_label_changes and cls != labels.EQUAL):
                targets.append(run.encode_pair(child, parent))
            elif cls in labels.REFRESH_CLASSES:
                refresh_targets.append(run.encode_pair(child, parent))
        return edges, legacy, classes, by_property, examples, targets, refresh_targets, roles

    edges, legacy, classes, by_property, examples, targets, refresh_targets, roles = writer._run(
        driver, db, LABEL_EDGES_AT_C6089B4A, read=True, transformer=classify_all)
    role_codes = len(roles) if roles is not None else 0
    if roles is not None and role_sink is not None:
        role_sink(roles)
    if dry_run:
        targets, refresh_targets = array("q"), array("q")
    written: Counter = Counter()
    for batch in run._sorted_unique_batches(targets, writer.REL_CHUNK):
        pairs = [run.decode_pair(code) for code in batch]
        if apply_label_changes:
            rows = run._approved_rows(driver, db, pairs, label_sources)
        else:
            rows = [{"child_id": c, "parent_id": p, "labels": label_sources.edge(c, p)} for c, p in pairs]
        if rows:
            out = writer.write_edge_labels(driver, db, rows, apply_label_changes=apply_label_changes)
            written.update({key: out.get(key, 0) for key in run._LABEL_COUNT_KEYS})
    for batch in run._sorted_unique_batches(refresh_targets, writer.REL_CHUNK):
        rows = [row for row in run._approved_rows(driver, db, [run.decode_pair(code) for code in batch],
                                                  label_sources)
                if labels.classify(row["stored"], row["labels"]) in labels.REFRESH_CLASSES]
        if rows:
            out = writer.write_edge_label_refreshes(driver, db, rows)
            written.update({key: out.get(key, 0) for key in run._LABEL_COUNT_KEYS})
    report = {"labels_edges": edges, "labels_legacy_id_edges": legacy, "labels_apply_changes": apply_label_changes}
    report.update({f"labels_{cls}": classes.get(cls, 0) for cls in labels.CLASSES})
    report.update({key: written.get(key, 0) for key in run._LABEL_COUNT_KEYS})
    report["labels_by_property"] = {cls: dict(sorted(c.items())) for cls, c in by_property.items()}
    report["labels_examples"] = examples
    report["assay_role_codes"] = role_codes
    report["assay_role_codes_unpackable"] = roles.unpackable if roles is not None else 0
    return report


NAN = float("nan")
LEGACY_EDGES = [(None, 5), ("x-7", 5), (2 ** 31, 5), (-1, 5), (5.5, 5), (NAN, 5), (9, "y"), (9, 2 ** 31), (9, None)]
INTERNAL_BY_SEEK = {500: (99,), 501: (96, 98), 503: (97,)}


def _label_world(shuffled: bool, seed: int = 5):
    """A graph of 300 samples and 1,500 random DERIVED_FROM (self-loops included) whose stored labels fall in every
    class, plus edges with legacy ends; and the label rule's inputs. Stored in child-id order unless ``shuffled``."""
    rng = random.Random(seed)
    sops = {7: "Extraction", 8: "RNA prep"}
    label_sources = run.LabelSources({500: (99, "Patient Visit"), 501: (98, "Seq run"), 502: (None, "Other"),
                                      503: (97, "Prep")}, sops, labels.sop_title_index(sops))
    ids = list(range(1, 301))
    for i in ids:
        label_sources.assays.add(i, rng.sample([500, 501, 502, 503], rng.randint(0, 3)))
        if rng.random() < 0.6:
            label_sources.protocols.add(i, (rng.choice([7, 8]),))
    pairs = set()
    while len(pairs) < 1500:
        pairs.add((rng.choice(ids), rng.choice(ids)))
    rows = []
    for child, parent in sorted(pairs):
        computed = label_sources.edge(child, parent)
        roll = rng.random()
        if roll < 0.25:
            stored = dict(computed)
        elif roll < 0.4:
            stored = {}
        elif roll < 0.5:
            stored = {k: v for k, v in computed.items() if k not in ("internal_assay_ids", "internal_assay_titles")}
        elif roll < 0.6:
            stored = dict(computed, internal_assay_title="Old title", internal_assay_titles=["Old title"])
        elif roll < 0.7:
            stored = dict(computed, internal_assay_id=95, internal_assay_ids=[95])
        elif roll < 0.8:
            stored = dict(computed, protocol_id=None, protocol_title=None)
        else:
            stored = dict(computed, assay_id=502, internal_assay_id=502, internal_assay_title="Other",
                          internal_assay_ids=[502], internal_assay_titles=["Other"])
        rows.append(((child, parent), {k: v for k, v in stored.items() if v is not None}))
    for i, pair in enumerate(LEGACY_EDGES):
        rows.insert(rng.randrange(len(rows) + 1) if shuffled else 151 * i, (pair, {}))
    if shuffled:
        rng.shuffle(rows)
    graph = Graph()
    graph.samples.update(ids)
    for pair, props in rows:
        graph.edges[pair] = props
    graph.page_ids = tuple(ids) + (2 ** 31, -1, 5.5, NAN, None, "x-7")
    return graph, label_sources


def _label_runs(monkeypatch, shuffled: bool, page: int, **kwargs):
    """The frozen single read and the paged step on two copies of one graph: (report, roles, edges) for each."""
    monkeypatch.setattr(writer, "ID_PAGE", page)
    out = []
    for fn in (_label_edges_at_c6089b4a, run.label_edges):
        graph, label_sources = _label_world(shuffled)
        sink: list = []
        report = fn(graph, "neo4j", label_sources, internal_by_seek=INTERNAL_BY_SEEK, role_sink=sink.append,
                    **kwargs)
        (roles,) = sink
        out.append((report, roles.unpackable, list(roles.by_sample()), graph.edges, graph))
    return out


@pytest.mark.parametrize("mode", [{}, {"apply_label_changes": True}, {"dry_run": True}])
def test_the_paged_label_step_equals_the_single_read_byte_for_byte(monkeypatch, mode):
    (old, old_unpackable, old_roles, old_edges, _), (new, new_unpackable, new_roles, new_edges, graph) = \
        _label_runs(monkeypatch, shuffled=False, page=7, **mode)

    assert json.dumps(new, sort_keys=True, default=repr) == json.dumps(old, sort_keys=True, default=repr)
    assert (new_unpackable, new_roles) == (old_unpackable, old_roles)
    assert repr(sorted(new_edges.items(), key=repr)) == repr(sorted(old_edges.items(), key=repr))
    # Every class the step reports was met, more than EXAMPLES times for the cap to merge across pages.
    assert all(new[f"labels_{cls}"] > run.EXAMPLES for cls in run._REPORTED_CLASSES), new
    assert new["labels_legacy_id_edges"] == len(LEGACY_EDGES) and new["assay_role_codes"] > 0
    # One read transaction per page of 7 ids and one for the ids no page holds; each page read after its bounds.
    page_query, rest_query = writer.page_forms(run.LABEL_EDGES)
    reads = [c for c in graph.calls if c.query in (page_query, rest_query)]
    numeric = sum(1 for v in graph.page_ids if pages._number(v))
    assert [c.query for c in reads] == [page_query] * -(-numeric // 7) + [rest_query]
    assert all(c.kwargs.get("routing_") is not None for c in reads)
    assert LABEL_EDGES_AT_C6089B4A not in [c.query for c in graph.calls]


def test_the_paged_label_step_equals_the_single_read_in_any_stream_order(monkeypatch):
    """Stored out of id order, the counts, role codes and writes are still equal; the examples are the first
    EXAMPLES in stream order, so with the cap lifted they are the same set."""
    monkeypatch.setattr(run, "EXAMPLES", 10 ** 6)
    (old, _, old_roles, old_edges, _), (new, _, new_roles, new_edges, _) = _label_runs(
        monkeypatch, shuffled=True, page=11, apply_label_changes=True)

    examples = (old.pop("labels_examples"), new.pop("labels_examples"))
    assert new == old and new_roles == old_roles
    assert repr(sorted(new_edges.items(), key=repr)) == repr(sorted(old_edges.items(), key=repr))
    assert {cls: sorted(map(repr, rows)) for cls, rows in examples[1].items()} == \
        {cls: sorted(map(repr, rows)) for cls, rows in examples[0].items()}


def test_no_label_read_streams_more_than_a_page_of_edges(monkeypatch):
    """Dev's failure in miniature: a read that streams every edge outlives the transaction timeout (here a budget of
    records per read); the paged step keeps each read to a page of child ids."""
    graph, label_sources = _label_world(False)
    graph.read_budget = 300                     # a page of 20,000 ids, scaled to 7: about 40 edges; the graph has 1,509
    monkeypatch.setattr(writer, "ID_PAGE", 7)
    report = run.label_edges(graph, "neo4j", label_sources, internal_by_seek=INTERNAL_BY_SEEK)
    assert report["labels_edges"] == 1500 + len(LEGACY_EDGES)


def test_a_retried_label_page_is_counted_once(monkeypatch):
    from neo4j.exceptions import TransientError

    monkeypatch.setattr(writer.time, "sleep", lambda s: None)
    (old, _, old_roles, _, _), _ = _label_runs(monkeypatch, shuffled=False, page=7)
    graph, label_sources = _label_world(False)
    page_query, _ = writer.page_forms(run.LABEL_EDGES)
    real, failed = graph.execute_query, []

    def flaky(query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        if query == page_query and parameters_["after"] > 40 and not failed:
            failed.append(parameters_["after"])

            def broken(records):
                def stream():
                    for i, record in enumerate(records):
                        if i == 3:
                            raise TransientError("connection lost mid-page")
                        yield record
                return result_transformer_(stream())
            return real(query, parameters_, database_, broken, **kwargs)
        return real(query, parameters_, database_, result_transformer_, **kwargs)

    graph.execute_query = flaky
    sink: list = []
    new = run.label_edges(graph, "neo4j", label_sources, internal_by_seek=INTERNAL_BY_SEEK, role_sink=sink.append)
    assert failed and json.dumps(new, sort_keys=True, default=repr) == json.dumps(old, sort_keys=True, default=repr)
    assert list(sink[0].by_sample()) == old_roles


def test_graphmeta_gets_the_label_maps_hash(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    expected = labels.label_maps_hash(ASSAY_MAP, SOPS)
    assert graph.graphmeta["label_maps_hash"] == expected and graph.graphmeta["schema_version"] == schema.SCHEMA_VERSION
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


# A type deleted in SEEK with its samples (no hook sees either) and recreated under its old title: the old node still
# holds a Sample node MySQL lacks, so its title reads as held under another id until that sample is retired.
_TITLE_CONFLICT = [{"title": "TIS", "graph_id": 9, "mysql_id": 26}]
_GONE_SAMPLE = {"element_id": "4:s:99", "id": 99, "uuid": "TIS-X-99", "type": "TIS", "synced": True,
                "incident_edges": 2}


def _conflicts_then(graph, *answers):
    """Answer the title-conflict read with each of ``answers`` in turn."""
    pending, real = list(answers), graph.answer

    def answer(query, params):
        if query == q.SAMPLE_TYPE_TITLE_CONFLICTS:
            return pending.pop(0), {}
        return real(query, params)

    graph.answer = answer
    return pending


def test_a_title_held_only_by_a_gone_types_samples_is_retired_then_checked_once_more(world, monkeypatch, tmp_path,
                                                                                    lock):
    graph = Graph()
    graph.retire_candidates = [dict(_GONE_SAMPLE)]
    pending = _conflicts_then(graph, _TITLE_CONFLICT, [])
    writers = Writers(monkeypatch, graph, ghosts=dict(NO_GHOSTS, orphan_ids=[99]))
    report = _full(graph, tmp_path)

    assert report["status"] == "ok" and report["problems"] == [] and pending == []
    assert (report["title_conflicts_retried"], report["sample_type_title_conflicts"]) == (_TITLE_CONFLICT, [])
    assert len(writers.of("retire_samples")) == 1 and report["retired_deleted"] == 1
    assert writers.names().index("retire_samples") < writers.names().index("delete_ghosts")
    assert (tmp_path / run.RETIRED_FILE).read_text().splitlines()[1:] == ["99\tTIS-X-99\tTIS\t2"]


def test_a_title_conflict_the_retire_does_not_clear_still_refuses(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.retire_candidates = [dict(_GONE_SAMPLE)]
    pending = _conflicts_then(graph, _TITLE_CONFLICT, _TITLE_CONFLICT)
    writers = Writers(monkeypatch, graph, ghosts=dict(NO_GHOSTS, orphan_ids=[99]))
    with pytest.raises(run.PreflightError, match="SampleType titles are held under other ids"):
        _full(graph, tmp_path)

    assert pending == [] and writers.names() == ["find_ghosts", "retire_samples"]


def test_a_title_conflict_beside_another_problem_refuses_without_retiring(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.retire_candidates = [dict(_GONE_SAMPLE)]
    graph.study_duplicates = [{"seek_study_id": 3, "nodes": 2}]
    pending = _conflicts_then(graph, _TITLE_CONFLICT)
    writers = Writers(monkeypatch, graph, ghosts=dict(NO_GHOSTS, orphan_ids=[99]))
    with pytest.raises(run.PreflightError):
        _full(graph, tmp_path)

    assert pending == [] and writers.names() == ["find_ghosts"]


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

    monkeypatch.setattr(study_links, "rebuild_in_study", boom)
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
    assert report["study_links_preview"]["dry_run"] is True and report["study_links_preview"]["status"] == "ok"
    assert report["study_merge_preview"] == {"counts": {}, "approval_line": "", "merge_other_investigation": [],
                                             "id_collisions": [], "legacy_only": []}
    assert lock.timeouts == [] and not GraphSyncRun.objects.exists()
    assert writers.names() == ["find_ghosts"]
    assert all(c.kwargs.get("routing_") is not None for c in graph.calls)
    assert list(tmp_path.iterdir()) == []


# --- SEEK Study nodes keyed on id ----------------------------------------------------------------

def test_the_rekey_moves_every_node_whose_id_and_title_are_seeks_and_keeps_its_id(world, monkeypatch, tmp_path,
                                                                                   lock):
    graph = Graph()
    graph.studies = [
        {"element_id": "4:st:7", "id": 7, "title": "S", "paper": False},              # a SEEK study batch upload keyed
        {"element_id": "4:st:8", "id": 8, "title": " Paper ", "paper": True},         # SEEK's title, with a DOI: moves
        {"element_id": "4:st:9", "id": 9, "title": "Another study", "paper": False},  # SEEK's title differs: left
        {"element_id": "4:st:50", "id": 50, "title": "Local", "paper": False}]        # no SEEK study 50: not listed
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    (rekey,) = [c for c in graph.calls if c.query == run.REKEY_STUDY]
    assert rekey.params["element_ids"] == ["4:st:7", "4:st:8"]
    assert [(s["id"], s.get("seek_study_id")) for s in graph.studies] == [(7, 7), (8, 8), (9, None), (50, None)]
    assert graph.events.index("study_rekey") < graph.events.index("rebuild_in_study")
    assert report["studies_rekeyed"] == 2 and report["study_ids_left_keyed_by_id"] == [9]


def test_a_graph_that_already_keys_seek_studies_on_seek_study_id_is_not_rekeyed(world, monkeypatch, tmp_path,
                                                                                lock):
    graph = Graph()
    graph.studies = [{"element_id": "4:st:1", "id": None, "seek_study_id": 9, "title": "S9", "paper": False},
                     {"element_id": "4:st:7", "id": 7, "title": "S", "paper": False}]
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    assert run.REKEY_STUDY not in graph.queries() and run.STUDIES_KEYED_BY_ID not in graph.queries()
    assert report["studies_rekeyed"] == 0


def test_the_sample_types_and_investigations_steps_archive_what_they_delete_in_the_run_directory(world, monkeypatch,
                                                                                               tmp_path, lock):
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    _full(graph, tmp_path)
    (types,) = writers.of("write_sample_types")
    (invs,) = writers.of("write_investigation_projects")
    assert types.kwargs == {"archive_path": str(tmp_path / writer.SAMPLE_TYPES_DELETED_FILE)}
    assert invs.kwargs == {"archive_path": str(tmp_path / writer.INVESTIGATIONS_DELETED_FILE),
                           "seek_study_ids": [7, 8, 9]}


def test_the_rekey_statements_keep_id_and_read_an_empty_doi_as_no_paper():
    assert "REMOVE" not in run.REKEY_STUDY and "SET st.seek_study_id = st.id" in run.REKEY_STUDY
    assert "coalesce(st.DOI, '') <> '' OR coalesce(st.PMID, '') <> ''" in run.STUDIES_KEYED_BY_ID


@pytest.mark.parametrize("switch, remove", [(None, False), ("add", False), ("follow", True)])
def test_the_seek_studies_step_rebuilds_in_study_with_the_boxs_switch(world, monkeypatch, tmp_path, lock, switch,
                                                                      remove):
    if switch is None:
        monkeypatch.delenv(study_links.SWITCH_ENV, raising=False)
    else:
        monkeypatch.setenv(study_links.SWITCH_ENV, switch)
    graph = Graph()
    writers = Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)

    (call,) = writers.of("rebuild_in_study")
    assert call.kwargs == {"remove": remove, "run_dir": str(tmp_path), "lock": None, "path": "full"}
    assert report["steps"]["seek_studies"] == {"in_study_added": 1, "in_study_removed": 0}
    assert report["status"] == "ok" and report["dry_run"] is False


@pytest.mark.parametrize("graphmeta", [{}, {"schema_version": "1.1", "catalog_hash": "old"}])
def test_the_seek_studies_step_runs_before_graphmeta_whatever_the_graphs_version(world, monkeypatch, tmp_path, lock,
                                                                                 graphmeta):
    """A fresh install's first sync (no GraphMeta) and an upgrade or rollback sync (another version) run the step."""
    graph = Graph()
    graph.graphmeta = dict(graphmeta)
    writers = Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)
    names = writers.names()
    assert report["status"] == "ok"
    assert names.index("rebuild_in_study") < names.index("write_graphmeta")


def test_a_seek_studies_step_that_does_not_answer_ok_fails_the_run(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    Writers(monkeypatch, graph)
    monkeypatch.setattr(study_links, "rebuild_in_study",
                        lambda d, db, **kw: {"status": "refused", "problems": ["two nodes"]})
    with pytest.raises(RuntimeError, match="refused"):
        _full(graph, tmp_path)


def test_the_preflight_refuses_two_study_nodes_sharing_a_seek_study_id(world, monkeypatch, tmp_path, lock):
    graph = Graph()
    graph.study_duplicates = [{"seek_study_id": 7, "nodes": 2}]
    writers = Writers(monkeypatch, graph)
    with pytest.raises(run.PreflightError, match="seek_study_id"):
        _full(graph, tmp_path)
    assert writers.names() == ["find_ghosts"]
    saved = json.loads((tmp_path / run.REPORT_FILE).read_text())
    assert saved["seek_study_id_duplicates"] == [{"seek_study_id": 7, "nodes": 2}]


def test_an_unmerged_split_is_left_in_place_and_makes_no_duplicate(world, monkeypatch, tmp_path, lock):
    """A box rebuilt before its merge: a legacy node keeps its id alone, the SEEK-keyed node exists, the rekey is a
    no-op (the graph already holds a SEEK-keyed node) and the preflight finds no duplicate."""
    graph = Graph()
    graph.studies = [{"element_id": "4:st:7", "id": 7, "title": "S", "paper": False},
                     {"element_id": "4:st:k7", "id": None, "seek_study_id": 7, "title": "S", "paper": False}]
    Writers(monkeypatch, graph)
    report = _full(graph, tmp_path)
    assert report["status"] == "ok" and report["studies_rekeyed"] == 0
    assert run.REKEY_STUDY not in graph.queries()


# --- catalog_sync --------------------------------------------------------------------------------

def _catalog_graph(version=schema.SCHEMA_VERSION):
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
                               "write_sample_type_counts", "write_assays", "replace_assay_catalog_edges",
                               "delete_gone_assays", "write_graphmeta"]
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


def test_catalog_sync_writes_the_assay_nodes_and_catalog_edges_and_leaves_members_and_run_in_alone(
        world, monkeypatch, lock):
    """catalog_sync is what sync_samples calls for a missing type or a new undeclared key, inside its write unit:
    no member rewrite belongs there, and RUN_IN records the mapping the members were last written from."""
    graph = _catalog_graph()
    writers = Writers(monkeypatch, graph)
    report = run.catalog_sync(graph, "neo4j")

    (nodes,) = writers.of("write_assays")
    assert [n["id"] for n in nodes.args[2]] == [99]
    (deleted,) = writers.of("delete_gone_assays")
    assert deleted.args[2] == [99]
    assert writers.of("replace_assay_runs") == []
    for statement in (q.RUN_IN_PAIRS, q.SAMPLE_ASSAY_EDGE_PAIRS, q.LINEAGE_PAIRS_INCIDENT,
                      q.REPLACE_SAMPLE_ASSAY_EDGES):
        assert statement not in graph.queries()
    assert report["steps"]["assays"]["assays"] == 1


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
