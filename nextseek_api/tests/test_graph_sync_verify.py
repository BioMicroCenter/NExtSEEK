"""Gate G's checks 9 to 11 (nextseek_api/graph_sync/verify.py; the sync design, section 13 and CI-9).

No database and no Neo4j: the MySQL readers in ``sources`` are replaced by a small fixed world, and the Neo4j driver by
a fake that answers every gate G statement from the graph a correct schema 1.2 sync writes for it. Each test breaks one
part of that graph.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from types import SimpleNamespace

import pytest
from neo4j import RoutingControl

from nextseek_api.batch_upload.identity import hash_identity
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import run, sources, verify
from nextseek_api.graph_sync.projection import project_sample


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
             "samples": copy.deepcopy(SAMPLES), "assay_requests": [], "identity_requests": []}

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
        "memberships": lambda: [],
        "resolved_assay_map": lambda: dict(ASSAY_MAP),
        "sops_map": lambda: dict(SOPS),
        "sample_assay_ids_for": sample_assay_ids_for,
        "parent_identities": parent_identities,
    }
    for name, fn in patches.items():
        monkeypatch.setattr(sources, name, fn)
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
        self.t_labelled = []   # nodes carrying a T_ label but not :Sample, as {"id", "labels"}
        self.catalog = [{"id": 26, "title": "TIS", "label": "T_TIS", "titles": ["Organ"]},
                        {"id": 33, "title": "D.SEQ", "label": "T_D_SEQ", "titles": ["Parent", "Protocol"]}]

    def __call__(self, query, params):
        nodes = self.nodes
        if query == verify.LINEAGE_PAIRS:
            return [{"child": c, "parent": p} for c, p in self.edges]
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
            return [{"schema_version": "1.2"}]
        if query == verify.T_LABEL_WITHOUT_SAMPLE:
            return [{"n": len(self.t_labelled)}]
        if query == verify.T_LABEL_WITHOUT_SAMPLE_EXAMPLES:
            return self.t_labelled[:params["limit"]]
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

def test_gate_g_passes_with_checks_9_to_11_on_the_graph_a_correct_1_2_sync_writes(world):
    result = _gate(GateWorld(_graph_nodes()))
    assert [c for c in result["checks"] if not c["pass"]] == []
    assert result["pass"] is True
    assert {c["name"].split(".")[0] for c in result["checks"]} == {str(i) for i in range(1, 12)}
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
    graph.edges[(13, 12)]["internal_assay_title"] = "RNA-seq run (old name)"      # changed
    graph.edges[(11, 10)].update(protocol_id=6, protocol_title="Staining SOP")    # changed: the child says SOP 5
    result = _gate(graph)
    assert _named(result, "9.lineage.labels")["pass"] is True
    differ = _named(result, "9.lineage.labels_differ_from_rule")
    assert (differ["actual"], differ["pass"]) == (2, True)
    assert differ["detail"]["changed"] == 2 and differ["detail"]["cleared"] == 0
    assert differ["detail"]["by_property"] == {"internal_assay_title": 1, "protocol_id": 1, "protocol_title": 1}
    example = [e for e in differ["detail"]["examples"] if e["pair"] == [13, 12]][0]
    assert example == {"pair": [13, 12], "class": "changed",
                       "stored": {"internal_assay_title": "RNA-seq run (old name)"},
                       "rule": {"internal_assay_title": "RNA-seq run"}}
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
