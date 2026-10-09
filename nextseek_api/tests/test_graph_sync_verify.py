"""Gate G's checks 9 to 11 (nextseek_api/graph_sync/verify.py; the sync design, section 13 and CI-9).

No database and no Neo4j: the MySQL readers in ``sources`` are replaced by a small fixed world, and the Neo4j driver by
a fake that answers every gate G statement from the graph a correct schema 1.2 sync writes for it. Each test breaks one
part of that graph.
"""
from __future__ import annotations

import copy
import random
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from neo4j import RoutingControl

from nextseek_api.batch_upload.identity import hash_identity
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import labels, run, sources, study_links, verify, writer
from nextseek_api.graph_sync.projection import project_sample
from nextseek_api.tests import graph_sync_pages as pages
from nextseek_api.tests.graph_sync_study_fakes import StudyGraph
from nextseek_graph import schema


class FakeDriver:
    """Records ``execute_query`` calls; ``responder(query, params)`` gives each call's records."""

    def __init__(self, responder):
        self.calls = []
        self.responder = pages.paged(responder)   # the paged reads answered from the template's rows

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        self.calls.append(SimpleNamespace(query=query, params=params, database=database_, kwargs=kwargs))
        records = list(self.responder(query, params))
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace()))


# --- the world: two lineage edges over two types ---------------------------------------------------

U_T1, U_T2 = "TIS-220119FLY-1", "TIS-220119FLY-2"
U_D1, U_D2 = "D.SEQ-220119FLY-3", "D.SEQ-220119FLY-4"

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
    _sample(10, U_T1, 26, {"UID": U_T1, "Organ": "Lung"}),
    _sample(11, U_D1, 33, {"UID": U_D1, "Parent": U_T1, "Protocol": "/sops/5"}),
    _sample(12, U_T2, 26, {"UID": U_T2, "Organ": "Liver"}),
    _sample(13, U_D2, 33, {"UID": U_D2, "Parent": f"{U_T2}; Liver biopsy"}),
]
PROJECT_LINKS = {10: [2], 11: [2], 12: [16], 13: [16]}
# 10 and 11 share SEEK assay 7 (mapped to internal assay 99); 12 and 13 share assay 8, which has no mapping.
ASSAY_LINKS = {10: [7], 11: [7], 12: [8, 9], 13: [8]}
ASSAY_MAP = {7: (99, "Patient Visit"), 8: (None, "RNA-seq run"), 9: (None, "Other")}
SOPS = {5: "Dissection SOP", 6: "Staining SOP"}
IDENTITIES = {U_T1: "Lung block 1", U_T2: "Liver block 2"}

# What batch upload's rule gives each declared edge, written out by hand.
LABELS = {
    (11, 10): {"assay_id": 7, "internal_assay_id": 99, "internal_assay_title": "Patient Visit",
               "internal_assay_ids": [99], "internal_assay_titles": ["Patient Visit"],
               "protocol_id": 5, "protocol_title": "Dissection SOP"},
    (13, 12): {"assay_id": 8, "internal_assay_id": 8, "internal_assay_title": "RNA-seq run",
               "internal_assay_ids": [8], "internal_assay_titles": ["RNA-seq run"],
               "protocol_id": None, "protocol_title": None},
}
# What batch upload's parent-title rule gives each node: a UID parent by its stored identity, a name as itself.
PARENT_LISTS = {
    10: ([], []),
    11: (["Lung block 1"], [hash_identity("Lung block 1")]),
    12: ([], []),
    13: (["Liver block 2", "Liver biopsy"], [hash_identity("Liver block 2"), hash_identity("Liver biopsy")]),
}


@pytest.fixture
def world(monkeypatch):
    """Install the fixed MySQL world into ``sources``; tests may change the returned state before a run."""
    state = {"assay_links": copy.deepcopy(ASSAY_LINKS), "identities": dict(IDENTITIES),
             "samples": copy.deepcopy(SAMPLES), "assay_requests": [], "identity_requests": [],
             "recent": [], "recent_requests": [], "pairs": [(7, 99)]}

    def recent_sample_ids(since, limit):
        state["recent_requests"].append((since, limit))
        return sorted(state["recent"], reverse=True)[:limit]

    def iter_samples(chunk=5000, after_id=0):
        rows = [dict(r) for r in sorted(state["samples"], key=lambda r: r["id"]) if r["id"] > after_id]
        for start in range(0, len(rows), chunk):
            yield rows[start:start + chunk]

    def uuid_to_ids():
        index = {}
        for row in sorted(state["samples"], key=lambda r: r["id"]):
            index.setdefault(row["uuid"], []).append(row["id"])
        return index

    def sample_assay_ids_for(ids):
        ids = set(ids)
        state["assay_requests"].append(ids)
        return {sid: sorted(set(a)) for sid, a in state["assay_links"].items() if sid in ids and a}

    def parent_identities(uuids):
        uuids = set(uuids)
        state["identity_requests"].append(uuids)
        return {u: state["identities"][u] for u in uuids if u in state["identities"]}

    patches = {
        "sample_types": lambda: copy.deepcopy(TYPES),
        "sample_attributes": lambda: copy.deepcopy(ATTRS),
        "sample_attribute_types": lambda: copy.deepcopy(ATTR_TYPES),
        "type_context": lambda: {}, "type_clades": lambda: {}, "deprecated_titles": lambda: set(),
        "attribute_meanings": lambda: {},
        "iter_samples": iter_samples, "uuid_to_ids": uuid_to_ids,
        "sample_projects": lambda: {k: sorted(set(v)) for k, v in PROJECT_LINKS.items()},
        # one member of project 2, so check 6 compares a scope (an empty list fails 6.scope.people_compared)
        "memberships": lambda: [{"person_id": 1, "project_id": 2, "has_left": False, "time_left_at": None}],
        "resolved_assay_map": lambda: dict(ASSAY_MAP),
        "sops_map": lambda: dict(SOPS),
        "sample_assay_ids_for": sample_assay_ids_for,
        "parent_identities": parent_identities,
        "recent_sample_ids": recent_sample_ids,
        "studies": lambda: [],
        "investigations": lambda: [],
        "iter_seek_study_links": lambda: iter(()),
        "projects": lambda: [{"id": 2, "title": "Local"}, {"id": 16, "title": "TCGA"}],
        "investigation_projects": lambda: [],
        "internal_assays": lambda: [{"id": 99, "title": "Patient Visit"}],
        "assay_internal_pairs": lambda: list(state["pairs"]),
        "assay_studies": lambda: [(7, 40), (8, 40), (9, 40)],
        "assay_context_rows": lambda: [{"id": 1, "internal_assay_id": 99, "assay_name": "Patient Visit",
                                        "required_parent_sample_types": "TIS", "children_sample_types": "D.SEQ"}],
    }
    for name, fn in patches.items():
        monkeypatch.setattr(sources, name, fn)
    monkeypatch.setattr(verify, "_sql_scope_count",
                        lambda project_ids: sum(1 for pids in PROJECT_LINKS.values() if set(project_ids) & set(pids)))
    return state


def _graph_nodes():
    """The Sample nodes a correct 1.2 sync writes for the world, as the driver returns them."""
    cat = run.build_catalog()
    nodes = {}
    for row in SAMPLES:
        type_id = row["sample_type_id"]
        proj = project_sample(row, cat.type_titles[type_id], cat.value_types.get(type_id, {}),
                              PROJECT_LINKS[row["id"]], assay_ids=ASSAY_LINKS[row["id"]],
                              parent_lists=PARENT_LISTS[row["id"]])
        props = dict(proj.props, synced_at="2026-09-15T00:00:00Z")
        nodes[row["id"]] = {"props": props, "label": proj.label}
    return nodes


class GateWorld:
    """Answers every gate G statement for the graph a correct 1.2 sync writes; tests break one part at a time."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.edges = {pair: dict(stored) for pair, stored in LABELS.items()}   # (child, parent) to stored labels
        self.type_title_differs = 0   # Samples whose type is not their SampleType's title
        self.doubled = []             # (child, parent) pairs whose DERIVED_FROM edge is there twice
        # The census: what db.labels() and db.relationshipTypes() list, and what something still carries.
        self.labels_listed = sorted(verify.EXPECTED_LABELS | {"T_TIS", "T_D_SEQ"})
        self.types_listed = sorted(verify.EXPECTED_RELATIONSHIP_TYPES)
        self.carried = set(self.labels_listed) | set(self.types_listed)
        self.t_labelled = []   # nodes carrying a T_ label but not :Sample, as {"id", "labels"}
        # Family 14 and check 2's edges: the small tables as a correct sync writes them for the world.
        self.projects = [{"id": 2, "title": "Local"}, {"id": 16, "title": "TCGA"}]
        self.investigations = []          # {"id", "title", "project_ids", "held"}
        self.members = [{"person_id": 1, "project_id": 2, "has_left": False}]
        self.in_project_extra = 0
        self.catalog = [{"id": 26, "title": "TIS", "label": "T_TIS", "titles": ["Organ"]},
                        {"id": 33, "title": "D.SEQ", "label": "T_D_SEQ", "titles": ["Parent", "Protocol"]}]
        # Family 13: the assay layer a correct 1.3 sync writes for the world (SEEK assay 7 maps to 99; 11 came out
        # of 10 inside it; 12 and 13 share only the unmapped 8). ``unsampled_edges`` are edges on samples the
        # sampled check does not read: the exhaustive count sees them.
        self.assay_ids = [99]
        self.runs = [{"assay_id": 99, "study_id": 40, "seek_assay_ids": [7]}]
        self.catalog_edges = [
            {"type": "ACCEPTED_BY", "code": "TIS", "assay_id": 99, "required": True, "group_index": 0},
            {"type": "GENERATES", "code": "D.SEQ", "assay_id": 99, "required": None, "group_index": 0}]
        self.sample_edges = {10: [("INPUT_TO", 99, [7])], 11: [("OUTPUT_OF", 99, [7])]}
        self.unsampled_edges = 0

    def __call__(self, query, params):
        nodes = self.nodes
        if query == verify.LINEAGE_PAIRS:
            return [{"child": c, "parent": p} for c, p in [*self.edges, *self.doubled]]
        if query == verify.LINEAGE_LABELS:
            return [{"child": c, "parent": p, "stored": {k: stored.get(k) for k in q.EDGE_LABEL_KEYS}}
                    for (c, p), stored in self.edges.items()]
        if query == verify.LINEAGE_ON_ORPHANS:
            return [{"n": 0}]
        if query == verify.PROJECT_ID_GROUPS:
            groups = Counter(tuple(n["props"]["project_ids"]) for n in nodes.values())
            return [{"project_ids": list(k), "n": v} for k, v in groups.items()]
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
        if query == verify.TYPE_TITLE_DIFFERS:
            return [{"n": self.type_title_differs}]
        if query == verify.TYPE_LABEL_AUDIT:
            return [{"samples": len(nodes), "not_one_type_label": 0, "not_one_of_type": 0, "label_differs": 0,
                     "label_sets": [[label] for label in sorted({n["label"] for n in nodes.values()})]}]
        if query == verify.GRAPH_ATTRIBUTE_IDS:
            return [{"id": a["id"], "title": a["title"], "sample_type_id": a["sample_type_id"]} for a in ATTRS]
        if query == verify.CONSTRAINT_NAMES:
            return [{"name": n} for n in verify.EXPECTED_CONSTRAINTS]
        if query == q.INDEX_STATES:
            return [{"name": n, "state": "ONLINE", "populationPercent": 100.0}
                    for n in verify.EXPECTED_INDEXES + verify.EXPECTED_CONSTRAINTS]
        if query in (verify.LABEL_COLLISIONS, verify.SAMPLE_TYPES_WITHOUT_ID_OR_LABEL):
            return [{"n": 0}]
        if query == verify.GRAPHMETA:
            return [{"schema_version": schema.SCHEMA_VERSION}]
        if query == verify.LABELS_LISTED:
            return [{"names": list(self.labels_listed)}]
        if query == verify.RELATIONSHIP_TYPES_LISTED:
            return [{"names": list(self.types_listed)}]
        if query in (verify.LABEL_CARRIED, verify.RELATIONSHIP_TYPE_CARRIED):
            return [{"found": 1}] if params["name"] in self.carried else []
        if query == verify.T_LABEL_WITHOUT_SAMPLE:
            return [{"n": len(self.t_labelled)}]
        if query == verify.T_LABEL_WITHOUT_SAMPLE_EXAMPLES:
            return self.t_labelled[:params["limit"]]
        if query in (q.STUDY_NODES, q.STUDY_SEEK_ID_DUPLICATES, q.SAMPLE_STUDIES_PAGE, q.SEEK_STUDY_NODES_GONE):
            return []
        if query == q.IN_PROJECT_DEGREES:
            degrees = Counter(p for n in nodes.values() for p in set(n["props"]["project_ids"]))
            return [{"id": p["id"], "n": degrees.get(p["id"], 0)} for p in self.projects]
        if query == q.IN_PROJECT_EXTRA:
            return [{"n": self.in_project_extra}]
        if query == q.GRAPH_PROJECTS:
            return [dict(p) for p in self.projects]
        if query == q.GRAPH_INVESTIGATIONS:
            return [dict(i) for i in self.investigations]
        if query == q.GRAPH_MEMBER_OF:
            return [dict(m) for m in self.members]
        if query == q.ORPHAN_IN_STUDY:
            return [{"n": 0}]
        if query == verify.ASSAY_IDS:
            return [{"id": i} for i in self.assay_ids]
        if query == verify.RUN_IN_ROWS:
            return [dict(r) for r in self.runs]
        if query == verify.CATALOG_EDGE_ROWS:
            return [dict(r) for r in self.catalog_edges]
        if query == verify.SAMPLED_ASSAY_EDGES:
            return [{"id": i, "type": t, "assay_id": a, "seek_assay_ids": s}
                    for i in params["ids"] for t, a, s in self.sample_edges.get(i, [])]
        if query == verify.SAMPLE_ASSAY_EDGE_COUNT:
            return [{"n": sum(len(e) for e in self.sample_edges.values()) + self.unsampled_edges}]
        raise AssertionError(f"unexpected statement: {query}")


def _gate(graph, **kwargs):
    kwargs.setdefault("sample_size", 10)
    kwargs.setdefault("seed", 7)
    kwargs.setdefault("accounts", ())
    return verify.gate_g(FakeDriver(graph), "neo4j", **kwargs)


def _named(result, name):
    (check,) = [c for c in result["checks"] if c["name"] == name]
    return check


# --- the whole gate ------------------------------------------------------------------------------

def test_gate_g_passes_on_the_graph_a_correct_sync_writes(world):
    result = _gate(GateWorld(_graph_nodes()))
    assert [c for c in result["checks"] if not c["pass"]] == []
    assert result["pass"] is True
    assert {c["name"].split(".")[0] for c in result["checks"]} == {str(i) for i in range(1, 15)}
    for name in ("13.assays.ids", "13.assays.run_in", "13.assays.catalog_edges", "13.assays.sampled_sample_edges",
                 "13.assays.sample_edge_count"):
        check = _named(result, name)
        assert check["pass"] is True
    assert result["stats"]["assays"]["sampled_with_edges"] == 2
    assert result["stats"]["assays"]["sample_edges_expected"] == 2
    for name in ("9.lineage.labels", "10.samples.no_t_label_without_sample", "11.samples.parent_lists"):
        check = _named(result, name)
        assert (check["expected"], check["actual"], check["pass"]) == (0, 0, True)
    assert result["stats"]["lineage_labels"]["edges_compared"] == 2
    assert result["stats"]["lineage_labels"]["classes"]["equal"] == 2
    assert result["stats"]["parent_lists_compared"] == 4


def test_gate_g_still_only_reads(world):
    driver = FakeDriver(GateWorld(_graph_nodes()))
    verify.gate_g(driver, "neo4j", sample_size=10, seed=1, accounts=())
    assert driver.calls and all(c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)


def test_check_9_reads_the_assays_of_the_lineage_endpoints_only(world):
    world["samples"].append(_sample(14, "TIS-220119FLY-9", 26, {"UID": "TIS-220119FLY-9", "Organ": "Skin"}))
    graph = GateWorld(_graph_nodes())
    _gate(graph)
    assert world["assay_requests"] == [{10, 11, 12, 13}]


# --- check 9: DERIVED_FROM labels --------------------------------------------------------------------

def test_check_9_fails_a_declared_edge_whose_endpoints_share_an_assay_and_that_has_no_label(world):
    graph = GateWorld(_graph_nodes())
    graph.edges[(11, 10)] = {}
    result = _gate(graph)
    check = _named(result, "9.lineage.labels")
    assert (check["actual"], check["pass"]) == (1, False)
    assert check["detail"] == [{"pair": [11, 10], "rule": {"assay_id": 7, "internal_assay_id": 99,
                                                           "internal_assay_title": "Patient Visit"}}]
    assert result["pass"] is False
    assert result["stats"]["lineage_labels"]["classes"]["new"] == 1


def test_check_9_counts_an_edge_with_only_the_legacy_assay_title_as_unlabelled(world):
    graph = GateWorld(_graph_nodes())
    graph.edges[(13, 12)] = {"assay_title": "RNA-seq run"}   # the legacy upload's one property
    assert _named(_gate(graph), "9.lineage.labels")["actual"] == 1


def test_check_9_passes_an_unlabelled_edge_whose_endpoints_share_no_assay(world):
    world["assay_links"][13] = [10_000]   # 12 holds 8 and 9: the two share none
    graph = GateWorld(_graph_nodes())
    graph.edges[(13, 12)] = {}
    result = _gate(graph)
    assert _named(result, "9.lineage.labels")["pass"] is True
    assert result["stats"]["lineage_labels"]["new_without_assay"] == 1
    assert result["pass"] is True


def test_check_9_reports_without_failing_a_label_that_differs_from_the_rule(world):
    graph = GateWorld(_graph_nodes())
    graph.edges[(13, 12)]["internal_assay_title"] = "RNA-seq run (old name)"      # renamed: the next sync writes it
    graph.edges[(11, 10)].update(protocol_id=6, protocol_title="Staining SOP")    # changed: the child says SOP 5
    result = _gate(graph)
    assert _named(result, "9.lineage.labels")["pass"] is True
    differ = _named(result, "9.lineage.labels_differ_from_rule")
    assert (differ["actual"], differ["pass"]) == (1, True)
    assert differ["detail"]["changed"] == 1 and differ["detail"]["cleared"] == 0
    assert differ["detail"]["by_property"] == {"protocol_id": 1, "protocol_title": 1}
    pending = _named(result, "9.lineage.labels_refresh_pending")
    assert (pending["actual"], pending["pass"]) == (1, True)
    assert pending["detail"]["examples"] == [{"pair": [13, 12], "class": "renamed",
                                              "stored": {"internal_assay_title": "RNA-seq run (old name)"},
                                              "rule": {"internal_assay_title": "RNA-seq run"}}]
    assert result["stats"]["lineage_labels"]["classes"]["renamed"] == 1
    assert result["pass"] is True


def test_check_9_reports_a_label_the_rule_would_clear(world):
    graph = GateWorld(_graph_nodes())
    graph.edges[(13, 12)].update(protocol_id=6, protocol_title="Staining SOP")   # the child names no protocol
    result = _gate(graph)
    differ = _named(result, "9.lineage.labels_differ_from_rule")
    assert differ["detail"]["cleared"] == 1 and differ["pass"] is True
    assert result["stats"]["lineage_labels"]["classes"]["cleared"] == 1


def test_check_9_reports_missing_plural_lists_without_failing(world):
    graph = GateWorld(_graph_nodes())
    for key in ("internal_assay_ids", "internal_assay_titles"):
        del graph.edges[(11, 10)][key]
    result = _gate(graph)
    plural = _named(result, "9.lineage.labels_plural_missing")
    assert (plural["actual"], plural["pass"]) == (1, True)
    assert _named(result, "9.lineage.labels")["pass"] is True
    assert _named(result, "9.lineage.labels_differ_from_rule")["actual"] == 0
    assert result["pass"] is True


def test_check_9_leaves_an_undeclared_edge_to_check_1(world):
    graph = GateWorld(_graph_nodes())
    graph.edges[(12, 10)] = {}
    result = _gate(graph)
    assert _named(result, "1.lineage.undeclared_pairs_between_samples")["actual"] == 1
    assert _named(result, "9.lineage.labels")["actual"] == 0
    assert result["stats"]["lineage_labels"]["edges_compared"] == 2


def test_check_9_skips_an_edge_whose_ends_are_not_sample_ids(world):
    graph = GateWorld(_graph_nodes())
    graph.edges[("TIS-220119FLY-1", None)] = {}
    result = _gate(graph)
    assert _named(result, "9.lineage.labels")["actual"] == 0
    assert result["stats"]["lineage_labels"]["edges_compared"] == 2


def test_check_9_takes_the_protocol_from_the_childs_stored_protocol(world):
    world["samples"][1] = _sample(11, U_D1, 33, {"UID": U_D1, "Parent": U_T1, "Protocol": "Staining SOP"})
    graph = GateWorld(_graph_nodes())
    result = _gate(graph)
    differ = _named(result, "9.lineage.labels_differ_from_rule")
    assert differ["detail"]["by_property"] == {"protocol_id": 1, "protocol_title": 1}
    assert differ["detail"]["examples"][0]["rule"] == {"protocol_id": 6, "protocol_title": "Staining SOP"}


# --- check 10: a T_ label on a node that is not a Sample ---------------------------------------------

def test_check_10_fails_a_node_with_a_type_label_and_no_sample_label(world):
    graph = GateWorld(_graph_nodes())
    graph.t_labelled = [{"id": 99, "labels": ["T_TIS", "OrphanSample"]}]
    result = _gate(graph)
    check = _named(result, "10.samples.no_t_label_without_sample")
    assert (check["actual"], check["pass"]) == (1, False)
    assert check["detail"] == [{"id": 99, "labels": ["OrphanSample", "T_TIS"]}]
    assert result["pass"] is False


def test_check_10_matches_every_node_without_sample_that_carries_a_type_label():
    for statement in (verify.T_LABEL_WITHOUT_SAMPLE, verify.T_LABEL_WITHOUT_SAMPLE_EXAMPLES):
        assert "NOT n:Sample" in statement and "STARTS WITH 'T_'" in statement
    assert "LIMIT $limit" in verify.T_LABEL_WITHOUT_SAMPLE_EXAMPLES


# --- check 11: the parent lists ------------------------------------------------------------------

def test_check_11_fails_a_sampled_node_whose_parent_lists_differ_from_the_rule(world):
    nodes = _graph_nodes()
    nodes[13]["props"]["parent_titles"] = ["Liver biopsy", "Liver block 2"]   # out of order
    result = _gate(GateWorld(nodes))
    check = _named(result, "11.samples.parent_lists")
    assert (check["actual"], check["pass"]) == (1, False)
    assert check["detail"] == [{"id": 13, "keys": ["parent_titles"]}]
    assert result["pass"] is False


def test_check_11_fails_a_node_written_without_the_parent_lists(world):
    nodes = _graph_nodes()
    for key in ("parent_titles", "parent_title_hashes"):
        del nodes[11]["props"][key]
    result = _gate(GateWorld(nodes))
    assert _named(result, "11.samples.parent_lists")["detail"] == [
        {"id": 11, "keys": ["parent_titles", "parent_title_hashes"]}]


def test_check_11_resolves_uid_parents_through_their_stored_identity(world):
    world["identities"].pop(U_T1)   # the parent's metadata yields no identity: the token is dropped
    nodes = _graph_nodes()
    nodes[11]["props"]["parent_titles"] = []
    nodes[11]["props"]["parent_title_hashes"] = []
    result = _gate(GateWorld(nodes))
    assert _named(result, "11.samples.parent_lists")["pass"] is True
    assert world["identity_requests"] == [{U_T1, U_T2}]   # the UID tokens only, never a name


def test_check_11_ignores_a_sampled_sample_missing_from_the_graph(world):
    nodes = _graph_nodes()
    del nodes[13]
    result = _gate(GateWorld(nodes))
    assert _named(result, "7.metadata.sampled_missing_in_graph")["detail"] == [13]
    assert _named(result, "11.samples.parent_lists")["actual"] == 0
    assert result["stats"]["parent_lists_compared"] == 3


# --- what the sampled checks read beside the random draw ------------------

def test_every_small_type_and_project_is_compared_beside_the_random_draw(world):
    result = _gate(GateWorld(_graph_nodes()), sample_size=1)
    assert result["stats"]["sampled_ids"] == [10, 11, 12, 13]
    assert result["stats"]["sample_strata"] == {"random": 1, "per_type": 4, "per_project": 4, "recent": 0,
                                                "recent_ids_read": 0, "compared": 4}
    assert result["stats"]["parent_lists_compared"] == 4


def test_every_recent_sample_is_compared(world, monkeypatch):
    monkeypatch.setattr(verify, "STRATUM_SIZE", 0)
    world["recent"] = [13]
    result = _gate(GateWorld(_graph_nodes()), sample_size=1, seed=7)
    assert 13 in result["stats"]["sampled_ids"]
    assert result["stats"]["sample_strata"]["recent"] == 1
    ((since, limit),) = world["recent_requests"]
    assert limit == verify.RECENT_CAP
    assert timedelta(days=verify.RECENT_DAYS - 1) < datetime.now(timezone.utc).replace(tzinfo=None) - since


def test_the_strata_leave_a_seeds_random_draw_as_it_was(world):
    with_strata = _gate(GateWorld(_graph_nodes()), sample_size=2, seed=3)["stats"]
    assert with_strata["sample_strata"]["random"] == 2
    again = _gate(GateWorld(_graph_nodes()), sample_size=2, seed=3)["stats"]
    assert with_strata["sampled_ids"] == again["sampled_ids"]


def test_check_7_fails_a_sampled_node_whose_uuid_type_title_or_search_text_differs(world):
    nodes = _graph_nodes()
    nodes[11]["props"]["search_text"] = "a stale line"
    nodes[12]["props"]["type"] = "D.SEQ"
    result = _gate(GateWorld(nodes))
    check = _named(result, "7.metadata.sampled_identity_mismatched")
    assert (check["actual"], check["pass"]) == (2, False)
    assert check["detail"] == [{"id": 11, "keys": ["search_text"]}, {"id": 12, "keys": ["type"]}]
    assert _named(result, "7.metadata.sampled_mismatched")["pass"] is True


def test_check_4_fails_samples_whose_type_is_not_their_sample_types_title(world):
    graph = GateWorld(_graph_nodes())
    graph.type_title_differs = 3
    check = _named(_gate(graph), "4.samples.type_differs_from_sample_type")
    assert (check["expected"], check["actual"], check["pass"]) == (0, 3, False)


# --- no check passes on empty input ---------------------------------------

def test_check_6_fails_when_no_membership_is_read_while_mysql_holds_samples(world, monkeypatch):
    monkeypatch.setattr(sources, "memberships", lambda: [])
    check = _named(_gate(GateWorld(_graph_nodes())), "6.scope.people_compared")
    assert (check["actual"], check["pass"]) == (0, False)


def test_check_6_compares_at_least_one_person(world):
    check = _named(_gate(GateWorld(_graph_nodes())), "6.scope.people_compared")
    assert (check["actual"], check["pass"]) == (1, True)


def test_check_9_fails_when_endpoints_carry_assays_and_the_assay_map_reads_empty(world, monkeypatch):
    monkeypatch.setattr(sources, "resolved_assay_map", lambda: {})
    check = _named(_gate(GateWorld(_graph_nodes())), "9.lineage.assay_map_read")
    assert check["pass"] is False


def test_check_9_needs_no_assay_map_when_no_endpoint_carries_an_assay(world, monkeypatch):
    monkeypatch.setattr(sources, "resolved_assay_map", lambda: {})
    world["assay_links"].clear()
    assert _named(_gate(GateWorld(_graph_nodes())), "9.lineage.assay_map_read")["pass"] is True


def test_check_1_fails_a_doubled_declared_edge(world):
    graph = GateWorld(_graph_nodes())
    graph.doubled = [(11, 10)]
    result = _gate(graph)
    check = _named(result, "1.lineage.duplicate_edges")
    assert (check["actual"], check["pass"], check["detail"]) == (1, False, [[11, 10]])
    assert _named(result, "1.lineage.undeclared_pairs_between_samples")["pass"] is True


# --- checks 1 and 9 read a page of child ids at a time (Neo4j's 120 s transaction limit) -------

LEGACY_LINEAGE = [(None, 5), ("x-7", 5), (5, "y"), (2 ** 31, 5)]


def _lineage_world(seed=3):
    """80 samples and 300 random DERIVED_FROM (self-loops, undeclared pairs and doubled edges among them) whose
    stored labels fall in every class, plus edges with legacy ends; and the MySQL side checks 1 and 9 compare."""
    rng = random.Random(seed)
    assay_map = {7: (99, "Patient Visit"), 8: (None, "RNA-seq run"), 9: (98, "Other")}
    ids = list(range(1, 81))
    assays = {i: tuple(sorted(rng.sample([7, 8, 9], rng.randint(0, 2)))) for i in ids}
    protocols = {i: (5, "Dissection SOP") for i in ids if rng.random() < 0.5}
    pairs = set()
    while len(pairs) < 300:
        pairs.add((rng.choice(ids), rng.choice(ids)))
    declared = {run.encode_pair(c, p) for c, p in pairs if rng.random() < 0.8}
    rows = []
    for child, parent in sorted(pairs) + sorted(rng.sample(sorted(pairs), 15)):    # 15 pairs carried twice
        rule = labels.edge_labels(assays[child], assays[parent], assay_map, protocols.get(child))
        roll = rng.random()
        if roll < 0.3:
            stored = dict(rule)
        elif roll < 0.5:
            stored = {}
        elif roll < 0.6:
            stored = dict(rule, internal_assay_title="Old title", internal_assay_titles=["Old title"])
        elif roll < 0.8:
            stored = dict(rule, internal_assay_id=97, internal_assay_ids=[97])
        else:
            stored = dict(rule, protocol_id=None, protocol_title=None)
        rows.append({"child": child, "parent": parent, "stored": {k: stored.get(k) for k in q.EDGE_LABEL_KEYS}})
    legacy = [{"child": c, "parent": p, "stored": {k: None for k in q.EDGE_LABEL_KEYS}} for c, p in LEGACY_LINEAGE]
    # stored in child-id order, as pages read them, so even the capped examples match; null and text ids last
    rows = sorted(rows + [r for r in legacy if isinstance(r["child"], int)], key=lambda r: r["child"])
    rows += [r for r in legacy if not isinstance(r["child"], int)]

    def respond(query, params):
        if query == verify.LINEAGE_PAIRS:
            return [{"child": r["child"], "parent": r["parent"]} for r in rows]
        if query == verify.LINEAGE_LABELS:
            return rows
        if query == verify.LINEAGE_ON_ORPHANS:
            return [{"n": 0}]
        raise AssertionError(f"unexpected statement: {query}")

    mysql = SimpleNamespace(lineage=declared, protocols=protocols)
    return respond, mysql, assays, assay_map, tuple(ids) + (2 ** 31,), len(rows)


def _checks_1_and_9(driver, mysql, assays, assay_map):
    checks, stats = [], {}
    verify._check_lineage(driver, "neo4j", mysql, checks, stats)
    verify._check_labels(driver, "neo4j", mysql, assays, assay_map, checks, stats)
    return checks, stats


def test_checks_1_and_9_read_a_page_of_child_ids_at_a_time(monkeypatch):
    """On dev check 9's one read took most of the 120 s limit; pages of three child ids give the checks and stats
    one page gives, a doubled pair's edges always in one page."""
    respond, mysql, assays, assay_map, ids, edges = _lineage_world()
    whole = _checks_1_and_9(FakeDriver(respond), mysql, assays, assay_map)
    monkeypatch.setattr(writer, "ID_PAGE", 3)
    driver = FakeDriver(pages.paged(respond, ids=ids, budget=60, on=(verify.LINEAGE_PAIRS, verify.LINEAGE_LABELS)))
    paged = _checks_1_and_9(driver, mysql, assays, assay_map)

    assert json.dumps(paged, sort_keys=True) == json.dumps(whole, sort_keys=True)
    named = {c["name"]: c for c in paged[0]}
    assert named["1.lineage.duplicate_edges"]["actual"]
    assert named["1.lineage.undeclared_pairs_between_samples"]["actual"]
    assert sum(1 for n in paged[1]["lineage_labels"]["classes"].values() if n) >= 5   # new, equal, changed, ...
    assert edges > 60
    for template in (verify.LINEAGE_PAIRS, verify.LINEAGE_LABELS):
        page, rest = writer.page_forms(template)
        assert [c.query for c in driver.calls if c.query in (page, rest)] == [page] * 27 + [rest]


# --- the census: no label or relationship type the contract does not name -----------------

def test_the_census_passes_the_contracts_names_and_every_type_label(world):
    result = _gate(GateWorld(_graph_nodes()))
    for name in ("8.schema.unknown_labels", "8.schema.unknown_relationship_types"):
        assert _named(result, name)["pass"] is True


def test_the_census_fails_a_stray_label_and_a_stray_relationship_type(world):
    graph = GateWorld(_graph_nodes())
    graph.labels_listed.append("Legacy")
    graph.types_listed.append("CHILD_OF")
    graph.carried |= {"Legacy", "CHILD_OF"}
    result = _gate(graph)
    labels_check = _named(result, "8.schema.unknown_labels")
    types_check = _named(result, "8.schema.unknown_relationship_types")
    assert (labels_check["actual"], labels_check["detail"]) == (1, ["Legacy"])
    assert (types_check["actual"], types_check["detail"]) == (1, ["CHILD_OF"])


def test_the_census_passes_the_assay_layer(world):
    graph = GateWorld(_graph_nodes())
    assay_layer = {"INPUT_TO", "OUTPUT_OF", "RUN_IN", "ACCEPTED_BY", "GENERATES"}
    graph.labels_listed = sorted(set(graph.labels_listed) | {"Assay"})
    graph.types_listed = sorted(set(graph.types_listed) | assay_layer)
    graph.carried |= {"Assay"} | assay_layer
    result = _gate(graph)
    for name in ("8.schema.unknown_labels", "8.schema.unknown_relationship_types"):
        assert _named(result, name)["pass"] is True


def test_the_census_ignores_a_listed_name_nothing_carries(world):
    graph = GateWorld(_graph_nodes())
    graph.types_listed.append("CHILD_OF")          # the token outlived its last edge
    assert _named(_gate(graph), "8.schema.unknown_relationship_types")["pass"] is True


def _crowded(world, monkeypatch, n=30):
    """A world with n more samples of type 26, all in project 2: far more than STRATUM_SIZE under one type and project."""
    for sample_id in range(100, 100 + n):
        uid = f"TIS-2601{sample_id:02d}LAU-{sample_id}"
        world["samples"].append(_sample(sample_id, f"00000000-0000-0000-0000-{sample_id:012d}", 26,
                                        {"UID": uid, "Organ": "Lung"}))
    links = {**PROJECT_LINKS, **{i: [2] for i in range(100, 100 + n)}}
    monkeypatch.setattr(sources, "sample_projects", lambda: {k: sorted(set(v)) for k, v in links.items()})


def test_the_main_random_draw_does_not_depend_on_the_strata(world, monkeypatch):
    """The strata use their own generator: the same seed draws the same random samples with or without them."""
    _crowded(world, monkeypatch)
    monkeypatch.setattr(verify, "STRATUM_SIZE", 0)
    bare = _gate(GateWorld(_graph_nodes()), sample_size=3, seed=5)["stats"]["sampled_ids"]
    monkeypatch.setattr(verify, "STRATUM_SIZE", 5)
    full = _gate(GateWorld(_graph_nodes()), sample_size=3, seed=5)["stats"]["sampled_ids"]
    assert len(bare) == 3 and set(bare) <= set(full)


def test_a_stratum_fuller_than_its_size_is_drawn_to_its_size_and_reproducibly(world, monkeypatch):
    _crowded(world, monkeypatch)
    monkeypatch.setattr(verify, "STRATUM_SIZE", 5)

    def scan(seed):
        return verify._scan_mysql(1000, 1, random.Random(0), strata_rng=random.Random(seed))

    side = scan(1)
    # type 26 holds 32 samples and project 2 holds 32; type 33 and project 16 hold 2 each
    assert side.strata["per_type"] == 5 + 2 and side.strata["per_project"] == 5 + 2
    assert [r["id"] for r in scan(1).sampled] == [r["id"] for r in side.sampled]
    first_five = {10, 12, 100, 101, 102}
    assert any({r["id"] for r in scan(seed).sampled if r["sample_type_id"] == 26} != first_five
               for seed in range(1, 6))


def test_the_carried_probes_match_a_dynamic_name_and_stop_at_the_first_hit():
    """A WHERE on labels(n) or type(r) scans every node or relationship; a dynamic label or type does not."""
    for statement, pattern in ((verify.LABEL_CARRIED, "(n:$($name))"), (verify.RELATIONSHIP_TYPE_CARRIED, "[r:$($name)]")):
        assert statement.lstrip().startswith("CYPHER 25")
        assert pattern in statement and "LIMIT 1" in statement
        assert "WHERE" not in statement and "labels(" not in statement and "type(" not in statement


# --- family 12: studies --------------------------------------------------------------------------------------------

FAMILY_12 = ("switch", "seek_study_id_duplicates", "split_pairs", "merge_candidates", "id_collisions",
             "nodes_differ_from_seek", "nodes_not_in_seek", "nodes_not_in_seek_empty", "seek_studies_without_node",
             "in_study_missing",
             "in_study_extra", "no_seek_study_kept", "orphan_in_study", "paper_samples")


@pytest.fixture
def seek_side(monkeypatch):
    side = SimpleNamespace(studies=[], investigations=[{"id": 101, "title": "Alder Investigation",
                                                        "description": None}], links=[])
    monkeypatch.setattr(sources, "studies", lambda: [dict(s) for s in side.studies])
    monkeypatch.setattr(sources, "investigations", lambda: [dict(i) for i in side.investigations])
    monkeypatch.setattr(sources, "iter_seek_study_links", lambda: iter(sorted(side.links)))
    monkeypatch.setattr(sources, "investigation_projects", lambda: [])
    monkeypatch.setattr(sources, "projects", lambda: [])
    return side


def _family(graph, follow, monkeypatch):
    if follow:
        monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    else:
        monkeypatch.delenv(study_links.SWITCH_ENV, raising=False)
    checks, stats = [], {}
    verify._check_studies(graph, "neo4j", checks, stats)
    return {c["name"]: c for c in checks}


def _study_world(side):
    """A split (1), a SEEK-keyed node whose title is not SEEK's (2), one SEEK lacks that a sample still holds (77), a
    collision (8), a paper (9); samples missing a link, holding a stale one, kept with no SEEK study, and on the paper;
    an orphan's link."""
    g = StudyGraph()
    inv = g.add_investigation(101, "Alder Investigation")
    side.studies = [{"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101},
                    {"id": 2, "title": "Birch Study", "description": None, "investigation_id": 101},
                    {"id": 8, "title": "Hazel Study", "description": None, "investigation_id": 101}]
    l1 = g.add_study(id=1, title="Alder Unpublished", DOI="", investigation=inv)
    k1 = g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=inv)
    k2 = g.add_study(seek_study_id=2, title="Old Birch title", investigation=inv)
    gone = g.add_study(seek_study_id=77, title="Gone", investigation=inv)
    g.add_study(id=8, title="An unrelated paper", DOI="10.9999/p8", investigation=inv)
    g.add_study(seek_study_id=8, title="Hazel Study", investigation=inv)
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=inv)
    for sid in (1001, 1002, 1003, 1004, 1005):
        g.add_sample(sid)
    g.link(1001, l1)
    g.link(1002, k1)
    g.link(1003, k2)             # SEEK: 1 -> a missing link and a stale one
    g.link(1004, gone)           # SEEK: none -> kept, and it holds the node of a study SEEK lacks
    g.link(1005, paper)          # SEEK: 2 -> a paper sample
    g.link(g.add_sample(1006, label="OrphanSample"), k1)
    side.links = [(1001, 1), (1002, 1), (1003, 1), (1005, 2)]
    return g


def test_with_the_switch_off_only_the_duplicate_check_can_fail(seek_side, monkeypatch):
    checks = _family(_study_world(seek_side), False, monkeypatch)
    assert set(checks) == {f"12.studies.{name}" for name in FAMILY_12}
    assert all(c["pass"] for c in checks.values())
    assert checks["12.studies.switch"]["actual"] == "add"
    actual = {name: checks[f"12.studies.{name}"]["actual"] for name in FAMILY_12[1:]}
    assert actual == {"seek_study_id_duplicates": 0, "split_pairs": 1, "merge_candidates": 1, "id_collisions": 1,
                      "nodes_differ_from_seek": 1, "nodes_not_in_seek": 1, "nodes_not_in_seek_empty": 0,
                      "seek_studies_without_node": 0,
                      "in_study_missing": 1, "in_study_extra": 1, "no_seek_study_kept": 1, "orphan_in_study": 1,
                      "paper_samples": 2}
    assert checks["12.studies.paper_samples"]["detail"]["withheld_links"] == 2
    assert checks["12.studies.paper_samples"]["detail"]["investigation_unknown"] == 0


def test_an_empty_node_of_a_study_seek_lacks_fails_the_gate_whatever_the_switch(seek_side, monkeypatch):
    """The small tables delete such a node every run, so one left behind is a failure on every box; a node that also
    carries an `id`, or holds a link, is only reported."""
    for follow in (False, True):
        g = StudyGraph()
        inv = g.add_investigation(101, "Alder Investigation")
        seek_side.studies = [{"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101}]
        g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=inv)
        empty = g.add_study(seek_study_id=77, title="Gone", investigation=inv)
        g.other_rels[empty] = ["RUN_IN"]
        g.add_study(seek_study_id=78, id=78, title="A 1.2-era node", investigation=inv)
        checks = _family(g, follow, monkeypatch)
        empty_check = checks["12.studies.nodes_not_in_seek_empty"]
        assert (empty_check["pass"], empty_check["actual"], empty_check["detail"]) == (False, 1, [77])
        assert checks["12.studies.nodes_not_in_seek"]["actual"] == 1
        assert checks["12.studies.nodes_not_in_seek"]["pass"]


def test_with_the_switch_on_the_checks_that_expect_0_fail(seek_side, monkeypatch):
    checks = _family(_study_world(seek_side), True, monkeypatch)
    assert checks["12.studies.switch"]["actual"] == "follow"
    assert sorted(name for name, c in checks.items() if not c["pass"]) == [
        "12.studies.in_study_extra", "12.studies.in_study_missing", "12.studies.merge_candidates",
        "12.studies.nodes_differ_from_seek", "12.studies.split_pairs"]


@pytest.mark.parametrize("follow", [False, True])
def test_a_duplicate_seek_study_id_fails_whatever_the_switch(seek_side, monkeypatch, follow):
    g = _study_world(seek_side)
    g.add_study(seek_study_id=2, title="Birch again")
    assert not _family(g, follow, monkeypatch)["12.studies.seek_study_id_duplicates"]["pass"]


def test_a_graph_that_follows_seek_passes_with_the_switch_on(seek_side, monkeypatch):
    g = StudyGraph()
    inv = g.add_investigation(101, "Alder Investigation")
    seek_side.studies = [{"id": 1, "title": "Alder Unpublished", "description": "About", "investigation_id": 101}]
    merged = g.add_study(id=1, seek_study_id=1, title="Alder Unpublished", description="About", investigation=inv)
    g.add_sample(1001)
    g.link(1001, merged)
    seek_side.links = [(1001, 1)]
    checks = _family(g, True, monkeypatch)
    assert all(c["pass"] for c in checks.values())


def test_a_study_with_no_investigation_does_not_differ(seek_side, monkeypatch):
    g = StudyGraph()
    seek_side.studies = [{"id": 3, "title": "Cedar", "description": None, "investigation_id": None}]
    g.add_study(seek_study_id=3, title="Cedar")
    assert _family(g, True, monkeypatch)["12.studies.nodes_differ_from_seek"]["actual"] == 0


def test_family_12_only_reads(seek_side, monkeypatch):
    g = _study_world(seek_side)
    _family(g, True, monkeypatch)
    assert g.calls and all(c.read for c in g.calls)


def test_a_seek_study_with_no_node_fails_only_with_the_switch_on(seek_side, monkeypatch):
    g = _study_world(seek_side)
    seek_side.studies.append({"id": 5, "title": "Elm Study", "description": None, "investigation_id": 101})
    assert _family(g, False, monkeypatch)["12.studies.seek_studies_without_node"]["pass"] is True
    check = _family(g, True, monkeypatch)["12.studies.seek_studies_without_node"]
    assert (check["actual"], check["pass"], check["detail"]) == (1, False, [5])


def test_a_seek_keyed_node_whose_investigation_node_is_missing_differs(seek_side, monkeypatch):
    g = StudyGraph()
    seek_side.studies = [{"id": 5, "title": "Elm Study", "description": None, "investigation_id": 103}]
    g.add_study(seek_study_id=5, title="Elm Study")
    assert _family(g, True, monkeypatch)["12.studies.nodes_differ_from_seek"]["detail"] == [5]


def test_a_paper_samples_missing_link_to_another_investigations_study_counts(seek_side, monkeypatch):
    """Operator ruling SHARED SAMPLES: only the paper's own investigation's studies are excepted."""
    g = StudyGraph()
    alder = g.add_investigation(101, "Alder Investigation")
    birch = g.add_investigation(102, "Birch Investigation")
    seek_side.investigations.append({"id": 102, "title": "Birch Investigation", "description": None})
    seek_side.studies = [{"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101},
                         {"id": 3, "title": "Birch Study", "description": None, "investigation_id": 102}]
    g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=alder)
    g.add_study(seek_study_id=3, title="Birch Study", investigation=birch)
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=alder)
    g.add_sample(1003)
    g.link(1003, paper)
    seek_side.links = [(1003, 1), (1003, 3)]
    before = _family(g, True, monkeypatch)
    missing = before["12.studies.in_study_missing"]
    assert (missing["actual"], missing["pass"]) == (1, False)
    study_links.rebuild_in_study(g, "neo4j", remove=True, run_dir=None)
    after = _family(g, True, monkeypatch)
    assert after["12.studies.in_study_missing"]["pass"] is True
    paper = after["12.studies.paper_samples"]
    assert (paper["actual"], paper["detail"]["withheld_links"]) == (1, 1)


# --- family 14: the small tables, and check 2's IN_PROJECT edges ----------------------------------------------------

def test_the_small_tables_and_the_in_project_edges_pass_on_a_correct_sync(world):
    result = _gate(GateWorld(_graph_nodes()))
    for name in ("2.scope.in_project_edges_differ", "2.scope.in_project_edges_extra", "14.small.projects_differ",
                 "14.small.investigations_differ", "14.small.investigations_not_in_seek",
                 "14.small.member_of_differs"):
        check = _named(result, name)
        assert (check["actual"], check["pass"]) == (0, True), name


def test_a_dropped_or_a_stray_in_project_edge_fails_check_2(world):
    graph = GateWorld(_graph_nodes())
    graph.projects.append({"id": 5, "title": "Elm"})                  # a node no sample reaches: 0 on both sides
    graph.nodes[13]["props"] = dict(graph.nodes[13]["props"], project_ids=[16])
    real = graph.__call__

    def dropped(query, params):
        if query == q.IN_PROJECT_DEGREES:
            return [{"id": 2, "n": 2}, {"id": 16, "n": 1}, {"id": 5, "n": 0}]       # 16 lost an edge
        return real(query, params)

    graph.in_project_extra = 1
    result = verify.gate_g(FakeDriver(dropped), "neo4j", sample_size=10, seed=7, accounts=())
    differ = _named(result, "2.scope.in_project_edges_differ")
    assert (differ["actual"], differ["pass"], differ["detail"]) == (1, False, [{"project_id": 16, "mysql": 2,
                                                                                "graph": 1}])
    assert _named(result, "2.scope.in_project_edges_extra")["pass"] is False


@pytest.mark.parametrize("change, name", [
    (lambda g: g.projects.pop(), "14.small.projects_differ"),                                     # a node missing
    (lambda g: g.projects[0].update(title="Local (old)"), "14.small.projects_differ"),           # a changed title
    (lambda g: g.investigations.append({"id": 7, "title": "Gone", "project_ids": [], "held": False}),
     "14.small.investigations_not_in_seek"),
    (lambda g: g.members.append({"person_id": 9, "project_id": 16, "has_left": False}), "14.small.member_of_differs"),
    (lambda g: g.members[0].update(has_left=True), "14.small.member_of_differs"),
], ids=["project missing", "project title", "investigation seek lacks", "stray membership", "has_left differs"])
def test_family_14_fails_what_differs_from_seek(world, change, name):
    graph = GateWorld(_graph_nodes())
    change(graph)
    check = _named(_gate(graph), name)
    assert (check["actual"], check["pass"]) == (1, False)


def test_an_investigation_seek_lacks_that_a_study_holds_is_reported_not_failed(world):
    graph = GateWorld(_graph_nodes())
    graph.investigations.append({"id": 7, "title": "A paper's", "project_ids": [], "held": True})
    result = _gate(graph)
    assert _named(result, "14.small.investigations_not_in_seek")["pass"] is True
    held = _named(result, "14.small.investigations_not_in_seek_held")
    assert (held["actual"], held["pass"], held["detail"]) == (1, True, [7])


def test_an_investigation_held_only_by_a_gone_seek_studys_node_fails(world, monkeypatch):
    """A Study node whose SEEK study is gone does not hold its Investigation, so gate G reads the held column
    with SEEK's study ids, and an Investigation only such a node links to fails as one SEEK lacks."""
    monkeypatch.setattr(sources, "studies", lambda: [{"id": 42, "title": "Live", "description": None,
                                                     "investigation_id": None}])
    graph = GateWorld(_graph_nodes())
    holders = {7: [41], 8: [42], 9: [None]}        # an Investigation SEEK lacks: the seek_study_id of each holder
    real = graph.__call__

    def answer(query, params):
        if query == q.GRAPH_INVESTIGATIONS:
            return [{"id": i, "title": "x", "project_ids": [],
                     "held": any(k is None or k in params["study_ids"] for k in keys)} for i, keys in holders.items()]
        return real(query, params)

    result = verify.gate_g(FakeDriver(answer), "neo4j", sample_size=10, seed=7, accounts=())
    gone = _named(result, "14.small.investigations_not_in_seek")
    assert (gone["actual"], gone["pass"], gone["detail"]) == (1, False, [7])
    assert _named(result, "14.small.investigations_not_in_seek_held")["detail"] == [8, 9]


def test_a_seek_row_naming_a_project_seek_lacks_is_counted_not_compared(world, monkeypatch):
    """No writer can link a project SEEK no longer has (the statements MATCH the Project node), so such a membership or
    investigation link is left out of the comparison and counted in the stats, never drift."""
    monkeypatch.setattr(sources, "memberships", lambda: [
        {"person_id": 1, "project_id": 2, "has_left": False, "time_left_at": None},
        {"person_id": 1, "project_id": 99, "has_left": False, "time_left_at": None}])
    monkeypatch.setattr(sources, "investigations", lambda: [{"id": 3, "title": "TCGA", "description": None}])
    monkeypatch.setattr(sources, "investigation_projects", lambda: [{"investigation_id": 3, "project_id": 16},
                                                                    {"investigation_id": 3, "project_id": 99}])
    graph = GateWorld(_graph_nodes())
    graph.investigations.append({"id": 3, "title": "TCGA", "project_ids": [16], "held": False})
    result = _gate(graph)
    for name in ("14.small.member_of_differs", "14.small.investigations_differ"):
        check = _named(result, name)
        assert (check["actual"], check["pass"]) == (0, True), name
    small = result["stats"]["small"]
    assert (small["memberships_project_not_in_seek"], small["investigation_links_project_not_in_seek"]) == (1, 1)
    graph.members.clear()                                           # a membership SEEK has still differs
    assert _named(_gate(graph), "14.small.member_of_differs")["actual"] == 1


def test_an_investigation_whose_project_links_differ_fails(world, monkeypatch):
    monkeypatch.setattr(sources, "investigations", lambda: [{"id": 3, "title": "TCGA", "description": None}])
    monkeypatch.setattr(sources, "investigation_projects", lambda: [{"investigation_id": 3, "project_id": 16},
                                                                    {"investigation_id": 3, "project_id": 16}])
    graph = GateWorld(_graph_nodes())
    graph.investigations.append({"id": 3, "title": "TCGA", "project_ids": [16], "held": False})
    assert _named(_gate(graph), "14.small.investigations_differ")["pass"] is True     # a repeated MySQL row counts once
    graph.investigations[0]["project_ids"] = []
    check = _named(_gate(graph), "14.small.investigations_differ")
    assert (check["actual"], check["detail"]) == (1, [3])


# --- check 13: the assay layer (schema 1.3) ------------------------------------------------------

def test_check_13_fails_a_stale_extra_sample_edge(world):
    graph = GateWorld(_graph_nodes())
    graph.sample_edges[12] = [("OUTPUT_OF", 99, [8])]       # 12 and 13 share only the unmapped SEEK assay 8
    result = _gate(graph)
    check = _named(result, "13.assays.sampled_sample_edges")
    assert (check["actual"], check["pass"]) == (1, False)
    assert check["detail"] == [{"id": 12, "missing": [], "extra": [("OUTPUT_OF", 99, (8,))]}]


def test_check_13_fails_a_seek_id_too_many_and_a_role_missing(world):
    graph = GateWorld(_graph_nodes())
    graph.sample_edges[10] = [("INPUT_TO", 99, [7, 9])]
    del graph.sample_edges[11]
    check = _named(_gate(graph), "13.assays.sampled_sample_edges")
    assert check["actual"] == 2
    assert check["detail"] == [
        {"id": 10, "missing": [("INPUT_TO", 99, (7,))], "extra": [("INPUT_TO", 99, (7, 9))]},
        {"id": 11, "missing": [("OUTPUT_OF", 99, (7,))], "extra": []}]


def test_check_13_fails_a_missing_assay_a_moved_run_in_and_a_missing_catalog_edge(world):
    graph = GateWorld(_graph_nodes())
    graph.assay_ids = []
    graph.runs = [{"assay_id": 99, "study_id": 41, "seek_assay_ids": [7]}]
    graph.catalog_edges = graph.catalog_edges[:1]
    result = _gate(graph)
    assert _named(result, "13.assays.ids")["detail"] == {"missing_in_graph": [99], "not_in_mysql": []}
    assert _named(result, "13.assays.run_in")["actual"] == 2
    assert _named(result, "13.assays.catalog_edges")["actual"] == 1
    assert result["pass"] is False


def test_the_assay_constraint_and_index_are_expected():
    assert "assay_id_unique" in verify.EXPECTED_CONSTRAINTS
    assert "assay_title" in verify.EXPECTED_INDEXES


def test_check_13_counts_every_sample_edge_so_a_dropped_one_fails_outside_the_sample(world):
    """The 1.3 plan's A2: an edge left unwritten on a sample the random draw missed still fails gate G, through the
    exhaustive count of INPUT_TO and OUTPUT_OF against the role rule over every declared pair."""
    graph = GateWorld(_graph_nodes())
    graph.unsampled_edges = -1
    result = _gate(graph)
    check = _named(result, "13.assays.sample_edge_count")
    assert (check["expected"], check["actual"], check["pass"]) == (2, 1, False)
    assert _named(result, "13.assays.sampled_sample_edges")["pass"] is True


def test_a_shared_pair_passes_check_13(world):
    """The studies tool's share mode: 10 and 11 are members of SEEK assay 7 and of its clone 9, both mapped to 99
    (here in one study); each holds one edge per role carrying both ids, and RUN_IN one row carrying both."""
    world["assay_links"][10], world["assay_links"][11] = [7, 9], [7, 9]
    world["pairs"].append((9, 99))
    graph = GateWorld(_graph_nodes())
    graph.runs = [{"assay_id": 99, "study_id": 40, "seek_assay_ids": [7, 9]}]
    graph.sample_edges = {10: [("INPUT_TO", 99, [7, 9])], 11: [("OUTPUT_OF", 99, [7, 9])]}
    result = _gate(graph)
    for name in ("13.assays.run_in", "13.assays.sampled_sample_edges", "13.assays.sample_edge_count"):
        assert _named(result, name)["pass"] is True, name
