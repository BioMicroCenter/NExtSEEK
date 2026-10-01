"""The by-id entry points (nextseek_api/graph_sync/targeted.py; the sync design, section 7.1).

No database and no Neo4j. ``FakeGraph`` answers every statement the entry points send, through the real writer, from
a small in-memory graph, and fails on any statement it does not know; the MySQL readers in ``sources`` are replaced
by a fixed world; the graph-write lock and ``run.catalog_sync`` by recorders.
"""
from __future__ import annotations

import copy
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from neo4j import RoutingControl

from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.graph_sync import catalog, hooks, labels, loop, projection, run, sources, state, study_links, targeted
from nextseek_api.graph_sync import writer
from nextseek_api.graph_sync import cypher as q
from nextseek_api.tests.graph_sync_study_fakes import StudyGraph
from nextseek_graph import schema

DB = "neo4j"
KEYS = q.EDGE_LABEL_KEYS

# --- the MySQL world: four samples over two types -----------------------------------------------

U_T1, U_D1, U_T2, U_D2 = "TIS-220119FLY-1", "D.SEQ-220119FLY-3", "TIS-220119FLY-2", "D.SEQ-220119FLY-4"

TYPES = [{"id": 26, "title": "TIS", "uuid": "st-26", "description": "Tissue"},
         {"id": 33, "title": "D.SEQ", "uuid": "st-33", "description": "Sequencing"}]


def _attr(attr_id, type_id, title):
    return {"id": attr_id, "sample_type_id": type_id, "title": title, "pos": attr_id, "required": False,
            "is_title": False, "sample_attribute_type_id": 1, "description": None}


ATTRS = [_attr(1, 26, "Name"), _attr(2, 26, "Organ"), _attr(3, 33, "Parent"), _attr(4, 33, "Protocol")]
ATTR_TYPES = {1: {"id": 1, "title": "String", "base_type": "String", "regexp": None}}


def _sample(sample_id, uuid, type_id, meta):
    return {"id": sample_id, "uuid": uuid, "title": f"s{sample_id}", "sample_type_id": type_id,
            "json_metadata": json.dumps(meta)}


SAMPLES = [
    _sample(10, U_T1, 26, {"UID": U_T1, "Name": "lung-1", "Organ": "Lung"}),
    _sample(11, U_D1, 33, {"UID": U_D1, "Parent": U_T1, "Protocol": "P.SOP-1", "Lane": "3"}),  # Lane: undeclared
    _sample(12, U_T2, 26, {"UID": U_T2, "Name": "liver-2", "Organ": "Liver"}),
    _sample(13, U_D2, 33, {"UID": U_D2, "Parent": U_D1}),
]
PROJECTS = {10: [2], 11: [2], 12: [16], 13: [2]}
ASSAYS = {10: [5], 11: [5], 12: [6], 13: [5]}
ASSAY_MAP = {5: (99, "Patient Visit"), 6: (None, "Seq run")}
SOPS = {7: "P.SOP-1"}
STUDY_LINKS = [{"sample_id": 10, "study_id": 70, "study_title": "Study seventy", "study_description": None,
                "investigation_id": 3},
               {"sample_id": 11, "study_id": 70, "study_title": "Study seventy", "study_description": None,
                "investigation_id": 3}]
# The assay layer (schema 1.3): SEEK assay 5 maps to internal assay 99, SEEK assay 6 to nothing; both run in study 70.
INTERNAL = [{"id": 99, "title": "Patient Visit"}]
PAIRS = [(5, 99)]
SEEK_STUDIES = [(5, 70), (6, 70)]
STUDIES = [{"id": 70, "title": "Study seventy (renamed)", "description": "About seventy", "investigation_id": 3}]
CONTEXT = [{"id": 1, "internal_assay_id": 99, "assay_name": "Patient Visit", "alternative_assay_names": None,
            "description": "A visit.", "tags": None, "parent_clade_type": None, "child_clade_type": None,
            "required_parent_sample_types": "TIS", "optional_parent_sample_types": None,
            "children_sample_types": "D.SEQ"}]

# The labels the rule gives the two declared edges: 11 -> 10 (11 names U_T1, protocol P.SOP-1 is SOP 7) and
# 13 -> 11 (13 names U_D1, no protocol). Both endpoints share assay 5, mapped to internal assay 99.
LABEL_11_10 = {"assay_id": 5, "internal_assay_id": 99, "internal_assay_title": "Patient Visit",
               "internal_assay_ids": [99], "internal_assay_titles": ["Patient Visit"],
               "protocol_id": 7, "protocol_title": "P.SOP-1"}
LABEL_13_11 = dict(LABEL_11_10, protocol_id=None, protocol_title=None)


def _stored(edge_props):
    return {k: edge_props.get(k) for k in KEYS}


def _as_set(label):
    """A label dict as a Neo4j edge keeps it: a null is no property at all."""
    return {k: v for k, v in label.items() if v is not None}


# --- the fake graph ------------------------------------------------------------------------------

class FakeGraph:
    """An in-memory graph behind ``execute_query``. Each statement the entry points send has a handler that does
    what the Cypher does; any other statement fails the test. ``before_delete`` hooks run when an edge or node delete
    arrives, so a test can check what already happened by then."""

    def __init__(self, version=schema.SCHEMA_VERSION):
        self.meta = None if version is None else {"schema_version": version, "catalog_hash": "cat-0",
                                                  "label_maps_hash": None}
        self.types = {26: "TIS", 33: "D.SEQ"}
        # SampleType titles held under other ids (run's sample_type_title_conflicts): the catalog sync refuses them
        self.title_conflicts: list = []
        self.nodes: dict = {}
        self.edges: dict = {}
        # counted by the full sync that wrote this graph: a sample carries it
        self.attributes = {"33:Lane": dict(catalog.undeclared_attribute(33, "D.SEQ", "Lane"), sample_count=1)}
        self.calls: list = []
        self.before_delete: list = []
        self._next = 0
        self.assay_nodes: dict = {}          # Assay id -> properties
        self.assay_edges: dict = {}          # Sample id -> {(type, Assay id, tuple of SEEK ids)}
        self.runs: set = set()               # (Assay id, study id, tuple of SEEK ids)
        self.catalog_edges: set = set()      # (type, code, Assay id, required, group)
        self.faults: dict = {}               # statement -> the call numbers (from 1) that raise
        self.fault_calls: dict = {}
        self.study = StudyGraph(is_sample=self._is_sample)
        self.handlers = {
            q.READ_GRAPHMETA: self._graphmeta,
            q.WRITE_GRAPHMETA_WITH_LABEL_MAPS: self._write_graphmeta,
            targeted.SAMPLE_TYPES_PRESENT: self._types_present,
            q.SAMPLE_TYPE_TITLE_CONFLICTS: lambda p: [dict(c) for c in self.title_conflicts],
            targeted.TYPES_OF_SAMPLES: self._types_of_samples,
            targeted.SET_SAMPLE_TYPE_COUNTS_FOR: lambda p: [{"n": len([i for i in p["ids"] if i in self.types])}],
            targeted.ATTRIBUTE_KEYS_PRESENT: lambda p: [{"key": k} for k in p["keys"] if k in self.attributes],
            targeted.CREATE_UNDECLARED_ATTRIBUTES: self._create_attributes,
            q.ATTRIBUTES_AT_ZERO: self._attributes_at_zero,
            q.SET_ATTRIBUTE_COUNTS_FROM_TYPE: self._count_attributes,
            targeted.GRAPH_ASSAY_LABELS: self._assay_labels,
            targeted.GRAPH_PROTOCOL_LABELS: self._protocol_labels,
            targeted.EDGES_WITH_PROTOCOLS: self._edges_with_protocols,
            q.WRITE_SAMPLES: self._write_samples,
            q.DERIVED_FROM_ID_FORM: lambda p: [],
            q.WRITE_MISSING_LINEAGE: self._write_missing_lineage,
            q.DERIVED_FROM_OF_CHILDREN: self._edges_of_children,
            q.DELETE_UNDECLARED_DERIVED_FROM: self._delete_edges,
            q.EDGES_INCIDENT: self._edges_incident,
            q.WRITE_EDGE_LABELS_NEW: lambda p: self._write_labels(p, approved=False),
            q.WRITE_EDGE_LABELS_CHANGED: lambda p: self._write_labels(p, approved=True),
            q.RETIRE_CANDIDATES: self._retire_candidates,
            q.DELETE_RETIRED: self._delete_retired,
            q.RELABEL_ORPHANS_BY_ELEMENT_ID: self._orphan,
            q.DELETE_GONE_PROJECTS: lambda p: [],
            q.MERGE_PROJECTS: lambda p: [],
            q.MERGE_INVESTIGATIONS: lambda p: [],
            q.DELETE_INVESTIGATION_IN_PROJECT: lambda p: [],
            q.MERGE_INVESTIGATION_IN_PROJECT: lambda p: [{"linked": len(p["rows"])}],
            q.DELETE_MEMBER_OF: lambda p: [],
            q.DELETE_GONE_PEOPLE: lambda p: [],
            q.MERGE_PEOPLE: lambda p: [],
            q.MERGE_MEMBER_OF: lambda p: [{"linked": len(p["rows"])}],
            q.MERGE_ASSAYS: self._merge_assays,
            q.SAMPLE_LINEAGE_DEGREES: self._degrees,
            q.REPLACE_ASSAY_RUNS: self._replace_runs,
            q.REPLACE_ASSAY_CATALOG_EDGES: self._replace_catalog_edges,
            q.DELETE_GONE_ASSAY_EDGES: self._delete_gone_assay_edges,
            q.DELETE_GONE_ASSAYS: self._delete_gone_assays,
            q.RUN_IN_PAIRS: self._run_pairs,
            q.SAMPLE_ASSAY_EDGE_PAIRS: self._edge_pairs,
            q.SAMPLES_CARRYING_SEEK_ASSAYS: self._samples_carrying,
            q.LINEAGE_PAIRS_INCIDENT: self._lineage_pairs_incident,
            q.REPLACE_SAMPLE_ASSAY_EDGES: self._replace_sample_edges,
        }
        self.handlers.update(self.study.handlers())

    # the driver surface
    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        read = kwargs.get("routing_") == RoutingControl.READ
        self.calls.append(SimpleNamespace(query=query, params=params, read=read, database=database_))
        if query in self.faults:
            self.fault_calls[query] = self.fault_calls.get(query, 0) + 1
            if self.fault_calls[query] in self.faults[query]:
                raise RuntimeError(f"injected failure on call {self.fault_calls[query]} of a statement")
        handler = self.handlers.get(query)
        if handler is None:
            raise AssertionError(f"FakeGraph does not know this statement: {query.strip()[:120]}")
        out = handler(params)
        records, counters = out if isinstance(out, tuple) else (out, {})
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=list(records),
                               summary=SimpleNamespace(counters=SimpleNamespace(**counters)))

    # building the graph
    def add_sample(self, sid, type_id=26, *, synced=True, **props):
        title = self.types.get(type_id, "X")
        node_props = {"id": sid, "uuid": f"u-{sid}", "type": title, **props}
        if synced:
            node_props["synced_at"] = "then"
        self.nodes[sid] = {"labels": {"Sample", "T_" + title.replace(".", "_")}, "type_id": type_id,
                           "props": node_props}

    def add_edge(self, child, parent, **props):
        self._next += 1
        eid = f"e:{self._next}"
        self.edges[eid] = {"child": child, "parent": parent, "props": {"child_id": child, "parent_id": parent, **props}}
        return eid

    # reading it back
    def edge(self, child, parent):
        for e in self.edges.values():
            if e["child"] == child and e["parent"] == parent:
                return e["props"]
        return None

    def writes(self):
        return [c for c in self.calls if not c.read]

    def of(self, query):
        return [c for c in self.calls if c.query == query]

    def first(self, query):
        return next(i for i, c in enumerate(self.calls) if c.query == query)

    # handlers
    def _is_sample(self, sid):
        node = self.nodes.get(sid)
        return node is not None and "Sample" in node["labels"]

    def _between_samples(self):
        return [(eid, e) for eid, e in self.edges.items()
                if self._is_sample(e["child"]) and self._is_sample(e["parent"])]

    def _graphmeta(self, p):
        return [] if self.meta is None else [{"props": dict(self.meta)}]

    def _write_graphmeta(self, p):
        self.meta.update(schema_version=p["schema_version"], catalog_hash=p["catalog_hash"],
                         label_maps_hash=p["label_maps_hash"])
        return []

    def _types_present(self, p):
        return [{"id": i, "title": self.types[i]} for i in p["ids"] if i in self.types]

    def _types_of_samples(self, p):
        found = {self.nodes[i]["type_id"] for i in p["ids"] if self._is_sample(i) and self.nodes[i]["type_id"]}
        return [{"id": t} for t in sorted(found)]

    def _attributes_at_zero(self, p):
        return [{"type_id": a["sample_type_id"], "key": key, "title": a["title"]}
                for key, a in sorted(self.attributes.items())
                if a["sample_type_id"] in p["type_ids"] and not a.get("sample_count")]

    def _count_attributes(self, p):
        raised = 0
        for r in p["rows"]:
            attr = self.attributes.get(r["key"])
            if attr is None:
                continue
            attr["sample_count"] = sum(1 for n in self.nodes.values()
                                       if n["type_id"] == r["type_id"] and n["props"].get(r["title"]) is not None)
            raised += attr["sample_count"] > 0
        return [{"raised": raised}]

    def _create_attributes(self, p):
        for r in p["rows"]:
            self.attributes.setdefault(r["key"], dict(r, sample_count=0))
        return [{"linked": len(p["rows"])}]

    def _write_samples(self, p):
        written = typed = linked = 0
        for r in p["rows"]:
            node = self.nodes.setdefault(r["id"], {"labels": set(), "type_id": None, "props": {}})
            props = dict(r["props"])
            for key in ("parent_titles", "parent_title_hashes"):
                if key not in props and key in node["props"]:
                    props[key] = node["props"][key]
            props["synced_at"] = "now"
            node["props"] = props
            node["labels"] = {label for label in node["labels"] if not label.startswith("T_")} | {"Sample", r["label"]}
            node["type_id"] = r["sample_type_id"] if r["sample_type_id"] in self.types else None
            written += 1
            row_typed = node["type_id"] is not None
            row_linked = sum(1 for pid in props.get("project_ids") or () if pid in self.study.projects)
            if not row_typed or row_linked < len(props.get("project_ids") or ()):
                props["source_hash"] = None          # WRITE_SAMPLES leaves a half-linked node for the nightly
            typed += row_typed
            linked += row_linked
        return [{"written": written, "typed": typed, "linked": linked}]

    def _write_missing_lineage(self, p):
        matched = created = 0
        for child, parent in p["rows"]:
            if self._is_sample(child) and self._is_sample(parent):
                matched += 1
                if self.edge(child, parent) is None:
                    self.add_edge(child, parent)
                    created += 1
        return [{"matched": matched}], {"relationships_created": created}

    def _edge_record(self, eid, e):
        return {"child_id": e["child"], "parent_id": e["parent"], "child_uuid": f"u-{e['child']}",
                "parent_uuid": f"u-{e['parent']}", "props": dict(e["props"]), "element_id": eid}

    def _edges_of_children(self, p):
        return [self._edge_record(eid, e) for sid in p["ids"] for eid, e in self._between_samples()
                if e["child"] == sid]

    def _delete_edges(self, p):
        for hook in self.before_delete:
            hook()
        deleted = 0
        for eid in p["element_ids"]:
            if eid in dict(self._between_samples()):
                del self.edges[eid]
                deleted += 1
        return [{"deleted": deleted}]

    def _edges_incident(self, p):
        found = {}
        for sid in p["ids"]:
            for eid, e in self._between_samples():
                if sid in (e["child"], e["parent"]):
                    found[eid] = {"child_id": e["child"], "parent_id": e["parent"], "element_id": eid,
                                  "stored": _stored(e["props"])}
        return list(found.values())

    def _write_labels(self, p, approved):
        matched = written = pairs = 0
        for r in p["rows"]:
            found = [e for _, e in self._between_samples()
                     if e["child"] == r["child_id"] and e["parent"] == r["parent_id"]]
            pairs += bool(found)
            for e in found:
                matched += 1
                props = e["props"]
                if approved:
                    passes = all(props.get(k) == r["stored"][k] for k in KEYS)
                else:
                    passes = all(props.get(k) is None for k in q.EDGE_SINGULAR_ASSAY_KEYS)
                if not passes:
                    continue
                for k in KEYS:
                    if r["labels"][k] is None:
                        props.pop(k, None)
                    else:
                        props[k] = r["labels"][k]
                props.pop("assay_title", None)
                written += 1
        return [{"matched": matched, "written": written, "pairs": pairs}]

    def _retire_candidates(self, p):
        out = []
        for sid in p["ids"]:
            if self._is_sample(sid):
                node = self.nodes[sid]
                out.append({"element_id": f"n:{sid}", "id": sid, "uuid": node["props"].get("uuid"),
                            "type": node["props"].get("type"), "synced": "synced_at" in node["props"],
                            "incident_edges": sum(1 for e in self.edges.values() if sid in (e["child"], e["parent"]))})
        return out

    def _delete_retired(self, p):
        for hook in self.before_delete:
            hook()
        n = 0
        for eid in p["element_ids"]:
            sid = int(eid.split(":")[1])
            if self._is_sample(sid) and "synced_at" in self.nodes[sid]["props"]:
                del self.nodes[sid]
                self.assay_edges.pop(sid, None)
                self.edges = {k: e for k, e in self.edges.items() if sid not in (e["child"], e["parent"])}
                n += 1
        return [{"n": n}]

    def _orphan(self, p):
        n = 0
        for eid in p["element_ids"]:
            sid = int(eid.split(":")[1])
            node = self.nodes.get(sid)
            if node is not None and "Sample" in node["labels"] and "synced_at" not in node["props"]:
                node["labels"] = {label for label in node["labels"]
                                  if not label.startswith("T_") and label != "Sample"} | {"OrphanSample"}
                node["type_id"] = None
                node["props"]["orphaned_at"] = "now"
                self.assay_edges.pop(sid, None)
                n += 1
        return [{"n": n}]

    def _assay_labels(self, p):
        rows = {(e["props"]["assay_id"], e["props"].get("internal_assay_id"), e["props"].get("internal_assay_title"))
                for _, e in self._between_samples() if e["props"].get("assay_id") is not None}
        return [{"assay_id": a, "internal_assay_id": i, "internal_assay_title": t} for a, i, t in sorted(rows)]

    def _protocol_labels(self, p):
        rows = {(e["props"]["protocol_id"], e["props"].get("protocol_title"))
                for _, e in self._between_samples() if e["props"].get("protocol_id") is not None}
        return [{"protocol_id": i, "protocol_title": t} for i, t in sorted(rows)]

    def _edges_with_protocols(self, p):
        return [{"child_id": e["child"], "parent_id": e["parent"], "element_id": eid, "props": dict(e["props"])}
                for eid, e in self._between_samples() if e["props"].get("protocol_id") in p["ids"]]

    def _degrees(self, p):
        return [{"id": sid, "degree": sum(1 for e in self.edges.values() if sid in (e["child"], e["parent"]))}
                for sid in p["ids"] if self._is_sample(sid)]

    # the assay layer (schema 1.3)
    def _merge_assays(self, p):
        for r in p["rows"]:
            self.assay_nodes[r["id"]] = dict(r)
        return [{"written": len(p["rows"])}]

    def _replace_runs(self, p):
        self.runs = {(r["assay_id"], r["study_id"], tuple(r["seek_assay_ids"])) for r in p["rows"]
                     if r["assay_id"] in self.assay_nodes and self.study.studies_by_seek(r["study_id"])}
        return [{"linked": len(self.runs)}]

    def _replace_catalog_edges(self, p):
        titles = set(self.types.values())
        accepted = {("ACCEPTED_BY", r["code"], r["assay_id"], r["required"], r["group"]) for r in p["accepted"]
                    if r["code"] in titles and r["assay_id"] in self.assay_nodes}
        generates = {("GENERATES", r["code"], r["assay_id"], None, r["group"]) for r in p["generates"]
                     if r["code"] in titles and r["assay_id"] in self.assay_nodes}
        self.catalog_edges = accepted | generates
        return [{"accepted": len(accepted), "generates": len(generates)}]

    def _delete_gone_assay_edges(self, p):
        gone, budget, deleted = set(self.assay_nodes) - set(p["ids"]), p["batch"], 0
        for sid in sorted(self.assay_edges):
            for edge in sorted(self.assay_edges[sid]):
                if deleted < budget and edge[1] in gone:
                    self.assay_edges[sid].discard(edge)
                    deleted += 1
        for run_ in sorted(self.runs):
            if deleted < budget and run_[0] in gone:
                self.runs.discard(run_)
                deleted += 1
        for edge in sorted(self.catalog_edges, key=repr):
            if deleted < budget and edge[2] in gone:
                self.catalog_edges.discard(edge)
                deleted += 1
        return [{"deleted": deleted}]

    def _delete_gone_assays(self, p):
        gone = set(self.assay_nodes) - set(p["ids"])
        for assay_id in gone:
            del self.assay_nodes[assay_id]
        for sid in self.assay_edges:
            self.assay_edges[sid] = {e for e in self.assay_edges[sid] if e[1] not in gone}
        self.runs = {r for r in self.runs if r[0] not in gone}
        self.catalog_edges = {e for e in self.catalog_edges if e[2] not in gone}
        return [{"deleted": len(gone)}]

    def _run_pairs(self, p):
        pairs = {(s, assay_id) for assay_id, _, seek_ids in self.runs for s in seek_ids}
        return [{"seek_assay_id": s, "assay_id": a} for s, a in sorted(pairs)]

    def _edge_pairs(self, p):
        pairs = {(s, e[1]) for sid, edges in self.assay_edges.items() if self._is_sample(sid)
                 for e in edges for s in e[2]}
        return [{"seek_assay_id": s, "assay_id": a} for s, a in sorted(pairs)]

    def _samples_carrying(self, p):
        wanted = set(p["seek_ids"])
        return [{"id": sid} for sid, edges in sorted(self.assay_edges.items())
                if self._is_sample(sid) and any(wanted & set(e[2]) for e in edges)]

    def _lineage_pairs_incident(self, p):
        wanted = set(p["ids"])
        pairs = {(e["child"], e["parent"]) for _, e in self._between_samples() if wanted & {e["child"], e["parent"]}}
        return [{"child_id": c, "parent_id": pa} for c, pa in sorted(pairs)]

    def _replace_sample_edges(self, p):
        samples = inputs = outputs = expected = 0
        for r in p["rows"]:
            if not self._is_sample(r["id"]):
                continue
            samples += 1
            expected += len(r["inputs"]) + len(r["outputs"])
            edges = set()
            for rel, key in (("INPUT_TO", "inputs"), ("OUTPUT_OF", "outputs")):
                for e in r[key]:
                    if e["assay_id"] in self.assay_nodes:
                        edges.add((rel, e["assay_id"], tuple(e["seek_assay_ids"])))
                        inputs += rel == "INPUT_TO"
                        outputs += rel == "OUTPUT_OF"
            self.assay_edges[r["id"]] = edges
        return [{"samples": samples, "inputs": inputs, "outputs": outputs, "expected": expected}]


# --- fixtures ------------------------------------------------------------------------------------

@pytest.fixture
def graph():
    g = FakeGraph()
    for sid, type_id in ((10, 26), (11, 33), (12, 26), (13, 33)):
        g.add_sample(sid, type_id)
    # the Assay node of internal assay 99, which SEEK assay 5 maps to (graph schema 1.3): a sync of these samples
    # writes their INPUT_TO and OUTPUT_OF to it, and a missing one would be a structural gap
    g.assay_nodes[99] = {"id": 99, "title": "Patient Visit", "has_context": True}
    return g


@pytest.fixture
def mysql(monkeypatch):
    """Install the MySQL world into ``sources``; tests may change it before a call. ``reads`` records the by-id
    readers' arguments."""
    world = SimpleNamespace(samples=copy.deepcopy(SAMPLES), projects=copy.deepcopy(PROJECTS),
                            assays=copy.deepcopy(ASSAYS), assay_map=dict(ASSAY_MAP), sops=dict(SOPS),
                            links=copy.deepcopy(STUDY_LINKS), reads=defaultdict(list),
                            internal=copy.deepcopy(INTERNAL), pairs=list(PAIRS), seek_studies=list(SEEK_STUDIES),
                            studies=copy.deepcopy(STUDIES), context=copy.deepcopy(CONTEXT))

    def rows_for(ids):
        wanted = set(ids)
        return [dict(r) for r in sorted(world.samples, key=lambda r: r["id"]) if r["id"] in wanted]

    def by_id(name, fn):
        def call(ids, *args, **kwargs):
            ids = list(ids)
            world.reads[name].append(sorted(ids))
            return fn(ids, *args, **kwargs)
        return call

    def links_for(table):
        return lambda ids: {i: sorted(set(table[i])) for i in sorted(set(ids)) if table.get(i)}

    def uuid_to_ids_for(tokens):
        index = {}
        for r in rows_for([r["id"] for r in world.samples]):
            if r["uuid"] in set(tokens):
                index.setdefault(r["uuid"], []).append(r["id"])
        return index

    def parent_identities(uuids):
        return {r["uuid"]: extract_identity(json.loads(r["json_metadata"]), uid=r["uuid"])
                for r in world.samples if r["uuid"] in set(uuids)}

    def ids_of_type(type_id, chunk=5000):
        ids = sorted(r["id"] for r in world.samples if r["sample_type_id"] == type_id)
        for start in range(0, len(ids), chunk):
            yield ids[start:start + chunk]

    patches = {
        "samples_by_ids": by_id("samples_by_ids", rows_for),
        "sample_projects_for": by_id("sample_projects_for", links_for(world.projects)),
        "sample_assay_ids_for": by_id("sample_assay_ids_for", links_for(world.assays)),
        "uuid_to_ids_for": by_id("uuid_to_ids_for", uuid_to_ids_for),
        "parent_identities": by_id("parent_identities", parent_identities),
        "seek_study_links_for": by_id("seek_study_links_for",
                                      lambda ids: [dict(l) for l in world.links if l["sample_id"] in set(ids)]),
        "resolved_assay_map": lambda: dict(world.assay_map),
        "sops_map": lambda: dict(world.sops),
        "ids_of_type": ids_of_type,
        "sample_types": lambda: copy.deepcopy(TYPES),
        "sample_attributes": lambda: copy.deepcopy(ATTRS),
        "sample_attribute_types": lambda: copy.deepcopy(ATTR_TYPES),
        "type_context": lambda: {}, "type_clades": lambda: {}, "deprecated_titles": lambda: set(),
        "attribute_meanings": lambda: {},
        "projects": lambda: [{"id": 2, "title": "Local"}, {"id": 16, "title": "TCGA"}],
        "investigations": lambda: [{"id": 3, "title": "TCGA", "description": None}],
        "investigation_projects": lambda: [{"investigation_id": 3, "project_id": 16}],
        "memberships": lambda: [{"person_id": 144, "project_id": 2, "has_left": False, "time_left_at": None}],
        "studies": lambda: copy.deepcopy(world.studies),
        "internal_assays": lambda: copy.deepcopy(world.internal),
        "assay_internal_pairs": lambda: list(world.pairs),
        "assay_studies": lambda: list(world.seek_studies),
        "assay_context_rows": lambda: copy.deepcopy(world.context),
        "sample_ids_in_assays": by_id("sample_ids_in_assays",
                                      lambda ids: sorted({sid for sid, found in world.assays.items()
                                                          if set(found) & set(ids)})),
    }
    for name, fn in patches.items():
        monkeypatch.setattr(sources, name, fn)

    def members(assay_ids):
        world.reads["_assay_members"].append(sorted(assay_ids))
        wanted = set(assay_ids)
        return {sid: frozenset(set(a) & wanted) for sid, a in world.assays.items() if set(a) & wanted}

    monkeypatch.setattr(targeted, "_assay_members", members)
    monkeypatch.delenv("GS_RUN_DIR", raising=False)
    return world


@pytest.fixture
def lock(monkeypatch):
    """The graph-write lock: records each timeout asked for and answers from ``outcomes`` (True once it is empty)."""
    rec = SimpleNamespace(timeouts=[], outcomes=[], held=False)

    @contextmanager
    def fake(timeout_s):
        rec.timeouts.append(timeout_s)
        got = rec.outcomes.pop(0) if rec.outcomes else True
        rec.held = got
        try:
            yield got
        finally:
            rec.held = False

    monkeypatch.setattr(state, "graph_write_lock", fake)
    return rec


def _title_refusal(conflicts, *more_problems) -> run.PreflightError:
    problems = [f"{len(conflicts)} SampleType titles are held under other ids in the graph "
                "(sample_type_title_conflicts)", *more_problems]
    return run.PreflightError(problems, {"problems": problems, "sample_type_title_conflicts": list(conflicts),
                                         "graph_schema_version": schema.SCHEMA_VERSION})


@pytest.fixture
def catalog_syncs(monkeypatch, graph, lock):
    """``run.catalog_sync`` replaced by a recorder that writes the MySQL types into the graph, or refuses as the real
    one does while ``graph.title_conflicts`` holds any. Each entry is ``(calls sent before it, whether the lock was
    held)``; ``kwargs`` holds each call's keyword arguments."""
    seen = _Seen()

    def fake(driver, db, dry_run=False, **kwargs):
        seen.append((len(graph.calls), lock.held))
        seen.kwargs.append(kwargs)
        if graph.title_conflicts:
            raise _title_refusal(graph.title_conflicts)
        graph.types.update({t["id"]: t["title"] for t in TYPES})
        return {"mode": "catalog", "status": "ok"}

    monkeypatch.setattr(run, "catalog_sync", fake)
    return seen


class _Seen(list):
    """The catalog syncs a test saw, and the keyword arguments of each."""

    def __init__(self):
        super().__init__()
        self.kwargs = []


@pytest.fixture
def env(graph, mysql, lock, catalog_syncs):
    return SimpleNamespace(graph=graph, mysql=mysql, lock=lock, catalog_syncs=catalog_syncs)


def _row(sid):
    return next(dict(r) for r in SAMPLES if r["id"] == sid)


# --- refusals ------------------------------------------------------------------------------------

ENTRY_POINTS = {
    "sync_samples": lambda d: targeted.sync_samples(d, DB, [11]),
    "sync_samples_of_type": lambda d: targeted.sync_samples_of_type(d, DB, 26),
    "retire_samples": lambda d: targeted.retire_samples(d, DB, [15]),
    "relabel_for_maps": lambda d: targeted.relabel_for_maps(d, DB),
    "sync_small_tables": lambda d: targeted.sync_small_tables(d, DB),
    "sync_assays": lambda d: targeted.sync_assays(d, DB),
    "sync_assay_edges": lambda d: targeted.sync_assay_edges(d, DB, [10]),
}


@pytest.mark.parametrize("version", ["1.1", None])
@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_refuses_a_graph_not_at_the_writer_version_and_writes_nothing(env, name, version):
    env.graph.meta = None if version is None else dict(env.graph.meta, schema_version=version)
    result = ENTRY_POINTS[name](env.graph)
    assert result["status"] == "not_at_version"
    assert result["schema_version"] == version
    assert result["writer_version"] == writer.SCHEMA_VERSION == schema.SCHEMA_VERSION
    assert env.graph.writes() == []
    assert env.lock.timeouts == []
    assert "samples_by_ids" not in env.mysql.reads
    assert env.catalog_syncs == []


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_returns_lock_timeout_without_writing(env, name):
    env.lock.outcomes = [False]
    result = ENTRY_POINTS[name](env.graph)
    assert result["status"] == "lock_timeout"
    assert env.graph.writes() == []
    assert "samples_by_ids" not in env.mysql.reads
    assert env.catalog_syncs == []


def test_waits_for_the_lock_the_given_time(env):
    targeted.sync_samples(env.graph, DB, [11])
    targeted.sync_samples(env.graph, DB, [11], lock_timeout_s=5)
    assert env.lock.timeouts == [targeted.LOCK_WAIT_S, 5]
    assert targeted.LOCK_WAIT_S == 60


def test_an_empty_id_list_reads_and_writes_nothing(env):
    assert targeted.sync_samples(env.graph, DB, [])["status"] == "ok"
    assert env.graph.calls == []
    assert env.lock.timeouts == []


# --- sync_samples: the samples -------------------------------------------------------------------

def test_writes_only_the_given_ids(env, tmp_path):
    before = copy.deepcopy(env.graph.nodes[10])
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert [[r["id"] for r in c.params["rows"]] for c in env.graph.of(q.WRITE_SAMPLES)] == [[11]]
    assert env.mysql.reads["samples_by_ids"][0] == [11]
    assert env.mysql.reads["sample_projects_for"] == [[11]]
    assert env.graph.nodes[10] == before
    assert result["samples_written"] == 1
    assert result["requested"] == 1 and result["found"] == 1


def test_every_write_happens_under_the_lock(env, tmp_path, monkeypatch):
    unlocked = []
    real = env.graph.execute_query

    def watch(query, *args, **kwargs):
        if kwargs.get("routing_") != RoutingControl.READ and not env.lock.held:
            unlocked.append(query)
        return real(query, *args, **kwargs)

    monkeypatch.setattr(env.graph, "execute_query", watch)
    env.graph.add_edge(11, 12)
    targeted.sync_samples(env.graph, DB, [11, 15], run_dir=str(tmp_path))
    assert unlocked == []


def test_projects_the_source_hash_and_the_parent_lists(env, tmp_path):
    targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    props = env.graph.nodes[11]["props"]
    expected = projection.source_hash(_row(11), "D.SEQ", {"Parent": "string", "Protocol": "string"}, [2], [5])
    assert props["source_hash"] == expected
    assert props["parent_titles"] == ["lung-1"]
    assert props["parent_title_hashes"] == [hash_identity("lung-1")]
    assert env.graph.nodes[11]["labels"] == {"Sample", "T_D_SEQ"}


def test_a_sample_that_cannot_be_projected_is_reported_and_keeps_its_lineage(env, tmp_path):
    env.mysql.samples = [dict(r, json_metadata="[1]") if r["id"] == 13 else r for r in env.mysql.samples]
    env.graph.add_edge(13, 11)
    result = targeted.sync_samples(env.graph, DB, [13], run_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["projection_errors"] == 1
    assert result["projection_error_examples"][0]["id"] == 13
    assert env.graph.of(q.WRITE_SAMPLES) == []
    assert env.graph.edge(13, 11) is not None
    assert env.graph.of(q.DELETE_UNDECLARED_DERIVED_FROM) == []


def test_runs_catalog_sync_first_when_a_sample_type_has_no_node(env, tmp_path):
    del env.graph.types[33]
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    assert len(env.catalog_syncs) == 1
    sent_before, held = env.catalog_syncs[0]   # how many calls were sent before it
    assert held
    assert sent_before <= env.graph.first(q.WRITE_SAMPLES)
    assert result["catalog_synced_for_types"] == [33]
    assert env.graph.nodes[11]["type_id"] == 33
    # its run record names the path that ran it, not a command nobody typed
    assert env.catalog_syncs.kwargs == [{"trigger": targeted.TRIGGER}] and targeted.TRIGGER == "by-id"


def test_runs_catalog_sync_first_when_a_sample_type_node_holds_another_title(env, tmp_path):
    env.graph.types[33] = "D.SEQ old"
    targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert len(env.catalog_syncs) == 1
    assert env.catalog_syncs[0][0] <= env.graph.first(q.WRITE_SAMPLES)


# A D.SEQ type recreated in SEEK under its old title: the graph's D.SEQ node is type 34, so SEEK's type 33 has none.
D_SEQ_HELD = {"title": "D.SEQ", "graph_id": 34, "mysql_id": 33}


def test_samples_of_a_type_waiting_for_the_catalog_are_left_out_and_named_and_the_rest_written(env, tmp_path):
    """While a SampleType title is held under another id, the catalog sync is refused and the node of a type that has
    none cannot be written: only the nightly reconcile clears that. The samples of such a type are left out and named;
    every other sample is written."""
    del env.graph.types[33]
    env.graph.title_conflicts = [D_SEQ_HELD]

    result = targeted.sync_samples(env.graph, DB, [10, 11, 12, 13], run_dir=str(tmp_path))

    assert result["status"] == "ok"
    why = "sample type 33 has no current SampleType node ('D.SEQ' is held by type 34 in the graph)"
    assert result["catalog_waiting_samples"] == {11: why, 13: why}
    assert result["catalog_synced_for_types"] == []
    assert sorted(r["id"] for c in env.graph.of(q.WRITE_SAMPLES) for r in c.params["rows"]) == [10, 12]
    assert result["samples_written"] == 2 and result["structural_gaps"] == 0
    # The titles are read first, so the refusal the report names anyway runs no catalog sync and records no run:
    # every sync that needs the catalog would record one while the titles wait.
    assert env.catalog_syncs == []


def test_a_title_conflict_the_catalog_sync_meets_after_the_titles_were_read_still_leaves_the_samples_out(
        env, tmp_path, monkeypatch):
    """SEEK can rename a type between the read of the titles and the catalog sync's own: its refusal for titles
    alone is then the same outcome."""
    del env.graph.types[33]

    def refuse(driver, db, **kwargs):
        raise _title_refusal([D_SEQ_HELD])

    monkeypatch.setattr(run, "catalog_sync", refuse)
    result = targeted.sync_samples(env.graph, DB, [10, 11], run_dir=str(tmp_path))

    assert list(result["catalog_waiting_samples"]) == [11]
    assert sorted(r["id"] for c in env.graph.of(q.WRITE_SAMPLES) for r in c.params["rows"]) == [10]


def test_a_refusal_for_more_than_title_conflicts_still_raises(env, tmp_path, monkeypatch):
    del env.graph.types[33]

    def refuse(driver, db, **kwargs):
        raise _title_refusal([D_SEQ_HELD], "a label collides with another (label_collisions)")

    monkeypatch.setattr(run, "catalog_sync", refuse)
    with pytest.raises(run.PreflightError):
        targeted.sync_samples(env.graph, DB, [10, 11], run_dir=str(tmp_path))
    assert env.graph.of(q.WRITE_SAMPLES) == []


def test_a_catalog_restamp_refused_for_title_conflicts_keeps_the_writes_and_says_refused(env, tmp_path):
    """A sample that fills an attribute for the first time restamps the catalog hash; refused for titles held under
    other ids, its writes stand and the hash waits for the nightly."""
    _declared(env, "26:Organ", "Organ", 0)
    env.graph.title_conflicts = [D_SEQ_HELD]

    result = targeted.sync_samples(env.graph, DB, [10, 12], run_dir=str(tmp_path))

    assert (result["status"], result["attribute_counts_raised"], result["catalog_resynced"]) == ("ok", 1, "refused")
    assert env.graph.attributes["26:Organ"]["sample_count"] == 2
    assert "catalog_waiting_samples" not in result
    assert env.catalog_syncs == []


def test_no_catalog_sync_when_every_type_has_its_node(env, tmp_path):
    targeted.sync_samples(env.graph, DB, [10, 11, 12, 13], run_dir=str(tmp_path))
    assert env.catalog_syncs == []


def test_adds_a_declared_false_attribute_for_an_undeclared_key(env, tmp_path):
    del env.graph.attributes["33:Lane"]
    first = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    created = env.graph.of(targeted.CREATE_UNDECLARED_ATTRIBUTES)
    assert [c.params["rows"] for c in created] == [[catalog.undeclared_attribute(33, "D.SEQ", "Lane")]]
    assert env.graph.attributes["33:Lane"]["declared"] is False
    assert first["undeclared_attributes_created"] == 1
    # the catalog sync that follows restamps GraphMeta.catalog_hash, which graph_search caches the catalog on
    assert len(env.catalog_syncs) == 1
    assert env.catalog_syncs[0][0] > env.graph.first(targeted.CREATE_UNDECLARED_ATTRIBUTES)

    second = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert len(env.graph.of(targeted.CREATE_UNDECLARED_ATTRIBUTES)) == 1
    assert second["undeclared_attributes_created"] == 0
    assert len(env.catalog_syncs) == 1


def test_writes_in_study_for_the_ids_only(env, tmp_path):
    targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert env.mysql.reads["seek_study_links_for"] == [[11]]
    assert env.graph.study.seek_links() == {(11, 70)}


def test_one_sync_links_a_study_to_an_investigation_the_graph_lacked(env, tmp_path):
    """SEEK made the investigation and the study directly, then samples were uploaded into it: after ONE by-id sync
    the Investigation node exists with SEEK's title and its IN_PROJECT, and the Study is linked to it."""
    env.graph.study.add_project(16, "TCGA")                  # the project exists; the investigation does not
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    inv = env.graph.study.investigation_by_id(3)
    assert env.graph.study.investigations[inv]["title"] == "TCGA" and env.graph.study.inv_projects[inv] == {16}
    (node,) = env.graph.study.studies_by_seek(70)
    assert env.graph.study.in_investigation[node] == [inv]
    assert (result["investigations_written"], result["seek_study_investigation_missing"]) == (1, 0)
    assert env.graph.of(q.DELETE_INVESTIGATION_IN_PROJECT) == []     # only the named investigations; no delete


def test_the_by_id_path_reads_seeks_small_tables_once_per_call(env, tmp_path, monkeypatch):
    reads = []
    real = study_links.seek_tables
    monkeypatch.setattr(study_links, "seek_tables", lambda: reads.append(1) or real())
    targeted.sync_samples(env.graph, DB, [10, 11, 12, 13], run_dir=str(tmp_path), chunk=1)
    assert len(reads) == 1


def test_sets_the_sample_counts_of_the_types_it_touched(env, tmp_path):
    env.graph.nodes[11]["type_id"] = 26  # the node still points at its old type
    targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    counted = env.graph.of(targeted.SET_SAMPLE_TYPE_COUNTS_FOR)
    assert [c.params["ids"] for c in counted] == [[26, 33]]
    assert env.graph.first(targeted.TYPES_OF_SAMPLES) < env.graph.first(q.WRITE_SAMPLES)


# --- sync_samples: lineage -----------------------------------------------------------------------

def test_creates_a_missing_declared_edge_and_labels_it_in_the_same_call(env, tmp_path):
    assert env.graph.edge(11, 10) is None
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    edge = env.graph.edge(11, 10)
    assert edge is not None
    assert {k: edge.get(k) for k in KEYS} == LABEL_11_10
    assert result["lineage_created"] == 1
    assert result["labels_new"] == 1 and result["labels_written"] == 1
    assert env.graph.first(q.WRITE_MISSING_LINEAGE) < env.graph.first(q.WRITE_EDGE_LABELS_NEW)


def test_archives_then_deletes_an_undeclared_edge_from_a_given_child(env, tmp_path):
    env.graph.add_edge(11, 12, assay_title="stale")   # 11 declares only U_T1 (sample 10)
    env.graph.add_edge(12, 10)                          # undeclared too, but 12 is not a given child
    archive = tmp_path / targeted.DERIVED_FROM_ARCHIVE_FILE
    archived_first = []
    env.graph.before_delete.append(lambda: archived_first.append(archive.exists()))

    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    assert env.graph.edge(11, 12) is None
    assert env.graph.edge(12, 10) is not None
    assert archived_first == [True]
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert lines[0] == writer.DERIVED_FROM_ARCHIVE_HEADER.rstrip("\n")
    assert lines[1].startswith("11\t12\tu-11\tu-12\t")
    assert result["derived_from_deleted"] == 1
    assert result["derived_from_archive_path"] == str(archive)


def test_labels_unlabelled_incident_edges_both_ways_and_reports_a_differing_label(env, tmp_path):
    differing = {"assay_id": 5, "internal_assay_id": 98, "internal_assay_title": "Old visit"}
    env.graph.add_edge(11, 10, **differing)   # 11 as child, labelled otherwise
    env.graph.add_edge(13, 11)                # 11 as parent, unlabelled

    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    assert {k: env.graph.edge(13, 11).get(k) for k in KEYS} == LABEL_13_11
    assert {k: env.graph.edge(11, 10).get(k) for k in KEYS} == {**dict.fromkeys(KEYS), **differing}
    assert result["labels_edges"] == 2
    assert result["labels_new"] == 1
    assert result["labels_changed"] == 1
    assert result["labels_written"] == 1
    assert result["label_differences"]["changed"] == {
        "internal_assay_id": 1, "internal_assay_title": 1, "internal_assay_ids": 1, "internal_assay_titles": 1,
        "protocol_id": 1, "protocol_title": 1}
    example = result["label_examples"][0]
    assert (example["child_id"], example["parent_id"], example["class"]) == (11, 10, "changed")
    assert example["stored"]["internal_assay_id"] == 98 and example["computed"]["internal_assay_id"] == 99


def test_writes_a_differing_label_only_with_apply_label_changes(env, tmp_path):
    env.graph.add_edge(11, 10, assay_id=5, internal_assay_id=98, internal_assay_title="Old visit",
                       assay_title="legacy")
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path), apply_label_changes=True)

    edge = env.graph.edge(11, 10)
    assert {k: edge.get(k) for k in KEYS} == LABEL_11_10
    assert "assay_title" not in edge
    assert result["labels_changed"] == 1 and result["labels_written"] == 1
    assert env.graph.of(q.WRITE_EDGE_LABELS_NEW) == []
    assert len(env.graph.of(q.WRITE_EDGE_LABELS_CHANGED)) == 1


def test_a_missing_plural_list_is_reported_not_written(env, tmp_path):
    singular = {k: v for k, v in LABEL_11_10.items() if k not in ("internal_assay_ids", "internal_assay_titles")}
    env.graph.add_edge(11, 10, **singular)
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))

    assert "internal_assay_ids" not in env.graph.edge(11, 10)
    assert result["labels_plural_missing"] == 1
    assert result["label_differences"]["plural_missing"] == {"internal_assay_ids": 1, "internal_assay_titles": 1}
    assert env.graph.of(q.WRITE_EDGE_LABELS_NEW) == []


def test_a_rename_is_written_without_approval_and_a_changed_label_is_not(env, tmp_path):
    env.graph.add_edge(11, 10, **_as_set(dict(LABEL_11_10, internal_assay_title="Old name",
                                              internal_assay_titles=["Old name"])))
    env.graph.add_edge(13, 11, **_as_set(dict(LABEL_13_11, internal_assay_id=98)))
    result = targeted.sync_samples(env.graph, DB, [11, 13], run_dir=str(tmp_path))
    assert env.graph.edge(11, 10)["internal_assay_title"] == "Patient Visit"          # renamed: written
    assert env.graph.edge(13, 11)["internal_assay_id"] == 98                           # changed: kept
    assert (result["labels_renamed"], result["labels_changed"], result["labels_refreshed"]) == (1, 1, 1)
    assert {e["class"] for e in result["label_examples"]} == {"renamed", "changed"}


def test_an_equal_label_sends_no_write(env, tmp_path):
    env.graph.add_edge(11, 10, **_as_set(LABEL_11_10))
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert result["labels_equal"] == 1
    assert env.graph.of(q.WRITE_EDGE_LABELS_NEW) == []


# --- sync_samples: the assay edges (schema 1.3) ------------------------------------------------------

U_P, U_C = "TIS-220119FLY-7", "D.SEQ-220119FLY-8"


@pytest.fixture
def with_assay(env):
    """The Assay node of internal assay 99, which SEEK assay 5 maps to, as a graph at 1.3 holds it (the ``graph``
    fixture seeds it)."""
    assert 99 in env.graph.assay_nodes
    return env


def test_sync_samples_writes_the_assay_edges_of_the_sample_and_its_partner(with_assay, tmp_path):
    graph = with_assay.graph
    result = targeted.sync_samples(graph, DB, [11], run_dir=str(tmp_path))

    assert graph.assay_edges[11] == {("OUTPUT_OF", 99, (5,))}
    assert graph.assay_edges[10] == {("INPUT_TO", 99, (5,))}       # the parent the lineage step linked
    assert (result["assay_edge_partners"], result["assay_edges_written"]) == (1, 2)
    # the partners are read before the lineage step, and the edges written after it
    assert (graph.first(q.LINEAGE_PAIRS_INCIDENT) < graph.first(q.WRITE_MISSING_LINEAGE)
            < graph.first(q.REPLACE_SAMPLE_ASSAY_EDGES))


def test_a_two_batch_upload_gives_the_first_batchs_parent_its_edge_when_the_child_arrives(with_assay, tmp_path):
    graph, mysql = with_assay.graph, with_assay.mysql
    mysql.samples.append(_sample(20, U_P, 26, {"UID": U_P, "Name": "parent-7"}))
    mysql.assays[20] = [5]
    targeted.sync_samples(graph, DB, [20], run_dir=str(tmp_path))
    assert graph.assay_edges[20] == set()                          # a root so far

    mysql.samples.append(_sample(21, U_C, 33, {"UID": U_C, "Parent": U_P}))
    mysql.assays[21] = [5]
    result = targeted.sync_samples(graph, DB, [21], run_dir=str(tmp_path))

    assert graph.edge(21, 20) is not None
    assert graph.assay_edges[21] == {("OUTPUT_OF", 99, (5,))}
    assert graph.assay_edges[20] == {("INPUT_TO", 99, (5,))}
    assert result["assay_edge_partners"] == 1


def test_a_parent_edit_that_deletes_an_edge_rewrites_the_old_parent(with_assay, tmp_path):
    graph, mysql = with_assay.graph, with_assay.mysql
    mysql.assays[12] = [5, 6]
    graph.add_edge(11, 12)                                          # 11 declares only U_T1 (sample 10) now
    graph.assay_edges[12] = {("INPUT_TO", 99, (5,))}               # what that edge gave 12
    targeted.sync_samples(graph, DB, [11], run_dir=str(tmp_path))

    assert graph.edge(11, 12) is None
    assert graph.assay_edges[12] == set()
    assert graph.assay_edges[11] == {("OUTPUT_OF", 99, (5,))}
    assert graph.assay_edges[10] == {("INPUT_TO", 99, (5,))}


def _fail_once_after(monkeypatch, gone):
    """``sources.sample_assay_ids_for`` raises once, the first time it is read after ``gone()`` holds (a MySQL
    connection lost after the destructive step, as on 2026-09-30); every other read answers."""
    real, state_ = sources.sample_assay_ids_for, {"raised": False}

    def flaky(ids):
        if gone() and not state_["raised"]:
            state_["raised"] = True
            raise RuntimeError("MySQL server has gone away")
        return real(ids)

    monkeypatch.setattr(sources, "sample_assay_ids_for", flaky)


def _drain(graph, queued, tmp_path):
    """Drain every row ``queued`` holds through the loop's entry point, as the loop would."""
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for n, (kind, key) in enumerate(list(queued)):
        claim = state.Claim(id=n, kind=kind, key=key, payload=None, enqueued_at=now, attempts=1, worker_id="w",
                            lease_expires_at=now)
        assert loop._apply(graph, DB, claim, loop.Options(run_root=str(tmp_path)), str(tmp_path))["status"] == "ok"


def test_a_failure_after_the_lineage_step_hands_the_old_partner_its_own_row(with_assay, tmp_path, monkeypatch):
    """The partners are read before the lineage step deletes an undeclared edge. A failure after that step loses
    them: the retry reads the partners again, and the old parent is no longer one. So the failure enqueues a samples
    row for each partner read before, and that row's drain clears the old parent's INPUT_TO."""
    graph, mysql, queued = with_assay.graph, with_assay.mysql, []
    monkeypatch.setattr(hooks, "enqueue", lambda kind, key, payload=None, **kw: queued.append((kind, key)) or True)
    mysql.assays[12] = [5, 6]
    graph.add_edge(11, 12)                                          # 11 declares only U_T1 (sample 10) now
    graph.assay_edges[12] = {("INPUT_TO", 99, (5,))}               # what that edge gave 12
    _fail_once_after(monkeypatch, lambda: graph.edge(11, 12) is None)

    with pytest.raises(RuntimeError):
        targeted.sync_samples(graph, DB, [11], run_dir=str(tmp_path))
    assert queued == [("samples", "sample:12")]
    targeted.sync_samples(graph, DB, [11], run_dir=str(tmp_path))  # the drain's retry of 11's own row
    assert graph.assay_edges[12] == {("INPUT_TO", 99, (5,))}       # 12 is no partner of 11 any more
    _drain(graph, queued, tmp_path)

    assert graph.edge(11, 12) is None
    assert graph.assay_edges[12] == set()
    assert graph.assay_edges[11] == {("OUTPUT_OF", 99, (5,))}


def test_a_failure_after_the_retire_hands_the_retired_samples_partner_its_own_row(with_assay, tmp_path, monkeypatch):
    graph, queued = with_assay.graph, []
    monkeypatch.setattr(hooks, "enqueue", lambda kind, key, payload=None, **kw: queued.append((kind, key)) or True)
    graph.add_sample(15, 33)
    graph.add_edge(15, 10)
    graph.assay_edges[15] = {("OUTPUT_OF", 99, (5,))}
    graph.assay_edges[10] = {("INPUT_TO", 99, (5,))}
    _fail_once_after(monkeypatch, lambda: 15 not in graph.nodes)

    with pytest.raises(RuntimeError):
        targeted.retire_samples(graph, DB, [15], run_dir=str(tmp_path))
    assert queued == [("samples", "sample:10")]
    targeted.retire_samples(graph, DB, [15], run_dir=str(tmp_path))
    assert graph.assay_edges[10] == {("INPUT_TO", 99, (5,))}       # 15 is gone, so the retry reads no partner
    _drain(graph, queued, tmp_path)

    assert 15 not in graph.nodes
    assert graph.assay_edges[10] == set()


def test_a_sample_gone_from_mysql_has_its_partners_rewritten_after_the_retire(with_assay, tmp_path):
    graph = with_assay.graph
    graph.add_sample(15, 33)
    graph.add_edge(15, 10)
    graph.assay_edges[15] = {("OUTPUT_OF", 99, (5,))}
    graph.assay_edges[10] = {("INPUT_TO", 99, (5,))}
    result = targeted.sync_samples(graph, DB, [15], run_dir=str(tmp_path))

    assert 15 not in graph.nodes and 15 not in graph.assay_edges
    assert graph.assay_edges[10] == set()
    assert (graph.first(q.LINEAGE_PAIRS_INCIDENT) < graph.first(q.DELETE_RETIRED)
            < graph.first(q.REPLACE_SAMPLE_ASSAY_EDGES))
    assert result["assay_edge_partners"] == 1


def test_a_partner_above_the_rewrite_max_gets_its_own_samples_row(with_assay, tmp_path, monkeypatch):
    queued = []
    monkeypatch.setattr(hooks, "enqueue", lambda kind, key, payload=None, **kw: queued.append((kind, key)) or True)
    monkeypatch.setattr(targeted, "PARTNER_REWRITE_MAX", 0)
    result = targeted.sync_samples(with_assay.graph, DB, [11], run_dir=str(tmp_path))

    assert queued == [("samples", "assay_edges:10")]
    assert 10 not in with_assay.graph.assay_edges
    assert with_assay.graph.assay_edges[11] == {("OUTPUT_OF", 99, (5,))}
    assert (result["assay_edge_partners"], result["assay_edge_partners_handed_off"]) == (0, 1)
    assert result["assay_edge_partner_hub_ids"] == [10]


def test_the_row_a_hub_partner_is_handed_off_on_hands_nothing_back(with_assay, tmp_path, monkeypatch):
    """Two lineage partners both above PARTNER_REWRITE_MAX: draining the row the first sync hands the hub off on
    rewrites the hub's own edges and hands nothing back, so the loop settles."""
    graph, queued = with_assay.graph, []
    monkeypatch.setattr(hooks, "enqueue", lambda kind, key, payload=None, **kw: queued.append((kind, key)) or True)
    monkeypatch.setattr(targeted, "PARTNER_REWRITE_MAX", 0)       # every sample with an edge is a hub
    targeted.sync_samples(graph, DB, [11], run_dir=str(tmp_path))
    (handed,) = queued
    state.check_item(*handed)
    assert handed[0] == "samples" and handed[1].endswith(":10")

    queued.clear()
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    claim = state.Claim(id=1, kind=handed[0], key=handed[1], payload=None, enqueued_at=now, attempts=1,
                        worker_id="w", lease_expires_at=now)
    result = loop._apply(graph, DB, claim, loop.Options(run_root=str(tmp_path)), str(tmp_path))

    assert result["status"] == "ok"
    assert queued == []
    assert graph.assay_edges[10] == {("INPUT_TO", 99, (5,))}
    assert graph.assay_edges[11] == {("OUTPUT_OF", 99, (5,))}


def test_a_type_sync_reads_the_mapping_again_for_each_chunk(with_assay, tmp_path, monkeypatch):
    """Each chunk of a type sync is its own write unit, so an assay_map drain's sync_assays can run between two of
    them. A later chunk that rewrote sample edges from the mapping read for the first one would undo the edges that
    sync_assays wrote, and no later sync_assays would mark the pair again (RUN_IN already holds it)."""
    graph, mysql = with_assay.graph, with_assay.mysql
    graph.add_edge(13, 12)
    mysql.assays[12] = mysql.assays[13] = [6]
    real, chunks = targeted._sync_ids, []

    def between_chunks(driver, db, wanted, ctx):
        report = real(driver, db, wanted, ctx)
        chunks.append(wanted)
        if len(chunks) == 1:                                        # SEEK assay 6 is mapped to 99 meanwhile
            mysql.pairs.append((6, 99))
            assert 6 in targeted.sync_assays(graph, DB)["assay_marked_seek_ids"]
        return report

    monkeypatch.setattr(targeted, "_sync_ids", between_chunks)
    targeted.sync_samples_of_type(graph, DB, 26, run_dir=str(tmp_path), chunk=1)

    assert chunks == [[10], [12]]
    assert graph.assay_edges[12] == {("INPUT_TO", 99, (6,))}
    assert graph.assay_edges[13] == {("OUTPUT_OF", 99, (6,))}
    assert targeted.sync_assays(graph, DB)["assay_marks_added"] == 0


def test_a_batch_of_roots_writes_no_assay_edge_and_counts_its_members(with_assay, tmp_path):
    result = targeted.sync_samples(with_assay.graph, DB, [10, 12], run_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert with_assay.graph.assay_edges[10] == set() and with_assay.graph.assay_edges[12] == set()
    assert (result["assay_edges_written"], result["assay_edge_samples"]) == (0, 2)
    # 10 is a member of SEEK assay 5 (mapped to 99) with no lineage inside it; 12's SEEK assay 6 has no mapping,
    # which drift reports, so it is not counted here
    assert result["assay_edge_members_without_role"] == 1


def test_a_shared_pair_gets_one_edge_per_role_carrying_both_seek_ids(with_assay, tmp_path):
    """The studies tool's share mode: 11 and its parent 10 are members of source assay 5 and of its clone 7 (study
    71), both mapped to 99. Their samples row (the share's link unit) gives each one edge per role on the one Assay,
    carrying both SEEK ids, never two edges to it."""
    graph, mysql = with_assay.graph, with_assay.mysql
    mysql.pairs.append((7, 99))
    mysql.seek_studies.append((7, 71))
    mysql.assays[10], mysql.assays[11] = [5, 7], [5, 7]
    targeted.sync_samples(graph, DB, [10, 11], run_dir=str(tmp_path))
    assert graph.assay_edges[11] == {("OUTPUT_OF", 99, (5, 7))}
    assert graph.assay_edges[10] == {("INPUT_TO", 99, (5, 7))}


def test_an_assay_edge_whose_assay_node_is_missing_is_a_structural_gap(env, tmp_path):
    """The 1.3 plan's A2: an edge the role rule gives to an Assay the graph does not hold yet (a mapping saved before
    its assay_map drain ran) is left unwritten and counted as a structural gap, so the row stays open and retries."""
    env.graph.assay_nodes.clear()
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert result["assay_edges_dropped"] == 2
    assert result["structural_gap_parts"]["assay_edges_dropped"] == 2
    assert result["structural_gaps"] >= 2
    assert targeted.UNTRACED_MARK in result["structural_gap_samples"][11]


def test_assay_edges_dropped_counts_only_the_edges_of_samples_that_have_a_node(env):
    """A row whose Sample node is missing is counted in assay_edge_samples_missing, and its edges are not dropped
    edges: that count means an Assay node was missing for a sample the graph holds."""
    rows = [{"id": 999, "inputs": [{"assay_id": 99, "seek_assay_ids": [5]}], "outputs": []},
            {"id": 10, "inputs": [{"assay_id": 404, "seek_assay_ids": [5]}], "outputs": []}]
    assert writer.replace_sample_assay_edges(env.graph, DB, rows) == {
        "assay_edge_samples": 2, "assay_edge_samples_missing": 1, "assay_edges_written": 0, "assay_edges_dropped": 1}


def test_retire_samples_rewrites_the_partners_of_a_retired_sample_after_the_delete(with_assay, tmp_path):
    graph = with_assay.graph
    graph.add_sample(15, 33)
    graph.add_edge(15, 10)
    graph.assay_edges[15] = {("OUTPUT_OF", 99, (5,))}
    graph.assay_edges[10] = {("INPUT_TO", 99, (5,))}
    result = targeted.retire_samples(graph, DB, [15], run_dir=str(tmp_path))

    assert 15 not in graph.nodes and 15 not in graph.assay_edges
    assert graph.assay_edges[10] == set()
    assert (graph.first(q.LINEAGE_PAIRS_INCIDENT) < graph.first(q.DELETE_RETIRED)
            < graph.first(q.REPLACE_SAMPLE_ASSAY_EDGES))
    assert result["assay_edge_partners"] == 1


def test_a_sample_that_becomes_an_orphan_loses_its_assay_edges_and_its_partner_its_role(with_assay, tmp_path):
    graph = with_assay.graph
    graph.add_sample(16, 33, synced=False)
    graph.add_edge(16, 10)
    graph.assay_edges[16] = {("OUTPUT_OF", 99, (5,))}
    graph.assay_edges[10] = {("INPUT_TO", 99, (5,))}
    targeted.retire_samples(graph, DB, [16], run_dir=str(tmp_path))

    assert graph.nodes[16]["labels"] == {"OrphanSample"}
    assert graph.edge(16, 10) is not None                          # its lineage stays
    assert 16 not in graph.assay_edges
    assert graph.assay_edges[10] == set()                          # an OrphanSample end is not lineage for a role


# --- sync_samples and retire_samples: the deletion rule --------------------------------------------

def test_retires_an_id_mysql_no_longer_returns(env, tmp_path):
    env.graph.add_sample(15, 26)                  # graph_sync wrote it: archived, then deleted
    env.graph.add_sample(16, 26, synced=False)    # never synced: becomes an OrphanSample
    env.graph.add_edge(15, 10)
    archive = tmp_path / targeted.RETIRED_FILE
    archived_first = []
    env.graph.before_delete.append(lambda: archived_first.append(archive.exists()))

    result = targeted.sync_samples(env.graph, DB, [11, 15, 16], run_dir=str(tmp_path))

    assert result["missing_in_mysql"] == 2
    assert result["retired_deleted"] == 1 and result["retired_orphaned"] == 1
    assert 15 not in env.graph.nodes
    assert env.graph.nodes[16]["labels"] == {"OrphanSample"}
    assert archived_first and archived_first[0] is True
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert lines == [writer.RETIRED_ARCHIVE_HEADER.rstrip("\n"), "15\tu-15\tTIS\t1"]
    counted = env.graph.of(targeted.SET_SAMPLE_TYPE_COUNTS_FOR)
    assert [c.params["ids"] for c in counted] == [[26, 33]]


def test_retire_samples_leaves_an_id_mysql_still_holds(env, tmp_path):
    env.graph.add_sample(15, 26)
    result = targeted.retire_samples(env.graph, DB, [11, 15], run_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["retire_skipped_in_mysql"] == 1
    assert result["retired_deleted"] == 1
    assert 11 in env.graph.nodes and 15 not in env.graph.nodes
    assert [c.params["ids"] for c in env.graph.of(q.RETIRE_CANDIDATES)] == [[15]]
    assert [c.params["ids"] for c in env.graph.of(targeted.SET_SAMPLE_TYPE_COUNTS_FOR)] == [[26]]


def test_without_a_run_dir_the_archive_goes_under_gs_run_dir(env, tmp_path, monkeypatch):
    monkeypatch.setenv("GS_RUN_DIR", str(tmp_path))
    env.graph.add_sample(15, 26)
    result = targeted.retire_samples(env.graph, DB, [15])
    path = result["retired_archive_path"]
    assert path.startswith(str(tmp_path) + os.sep)
    assert os.path.basename(os.path.dirname(path)).startswith("targeted-")
    assert os.path.exists(path)


def test_without_a_run_dir_or_gs_run_dir_the_archive_goes_under_the_log_dir(env, tmp_path, settings):
    settings.LOG_DIR = str(tmp_path)
    env.graph.add_sample(15, 26)
    path = targeted.retire_samples(env.graph, DB, [15])["retired_archive_path"]
    assert path.startswith(os.path.join(str(tmp_path), "graph_sync") + os.sep)


# --- sync_samples_of_type ------------------------------------------------------------------------

def test_sync_samples_of_type_syncs_in_chunks_and_builds_the_catalog_once(env, tmp_path, monkeypatch):
    real, built = run.build_catalog, []
    monkeypatch.setattr(run, "build_catalog", lambda: built.append(1) or real())
    result = targeted.sync_samples_of_type(env.graph, DB, 26, run_dir=str(tmp_path), chunk=1)

    assert [[r["id"] for r in c.params["rows"]] for c in env.graph.of(q.WRITE_SAMPLES)] == [[10], [12]]
    assert result["status"] == "ok"
    assert result["sample_type_id"] == 26
    assert result["chunks"] == 2
    assert result["samples_written"] == 2
    assert result["requested"] == 2
    assert len(built) == 1
    assert env.lock.timeouts == [targeted.LOCK_WAIT_S] * 2


def test_sync_samples_of_type_stops_at_a_refused_chunk(env, tmp_path):
    env.lock.outcomes = [True, False]
    result = targeted.sync_samples_of_type(env.graph, DB, 26, run_dir=str(tmp_path), chunk=1)
    assert result["status"] == "lock_timeout"
    assert result["chunks"] == 1
    assert result["samples_written"] == 1
    assert len(env.graph.of(q.WRITE_SAMPLES)) == 1


# --- relabel_for_maps ----------------------------------------------------------------------------

@pytest.fixture
def labelled(env):
    """Edges labelled by the old maps: 11 -> 10 and 13 -> 11 with assay 5, 14 -> 12 with assay 6 (unchanged)."""
    env.graph.add_sample(14, 26)
    env.mysql.samples.append(_sample(14, "TIS-220119FLY-5", 26, {"Name": "liver-3"}))
    env.mysql.assays[14] = [6]
    env.graph.add_edge(11, 10, **_as_set(LABEL_11_10))
    env.graph.add_edge(13, 11, **_as_set(LABEL_13_11))
    label_6 = {"assay_id": 6, "internal_assay_id": 6, "internal_assay_title": "Seq run", "internal_assay_ids": [6],
               "internal_assay_titles": ["Seq run"]}
    env.graph.add_edge(14, 12, **label_6)
    env.graph.meta["label_maps_hash"] = labels.label_maps_hash(ASSAY_MAP, SOPS)
    return env


def test_relabel_for_maps_does_nothing_when_the_maps_are_unchanged(labelled):
    result = targeted.relabel_for_maps(labelled.graph, DB)
    assert result["status"] == "ok"
    assert result["maps_changed"] is False
    assert labelled.graph.writes() == []
    assert labelled.graph.of(targeted.GRAPH_ASSAY_LABELS) == []


def test_relabel_for_maps_touches_only_the_members_of_a_changed_assay(labelled):
    labelled.mysql.assay_map[5] = (99, "Patient Visit v2")
    edge_6 = copy.deepcopy(labelled.graph.edge(14, 12))
    result = targeted.relabel_for_maps(labelled.graph, DB)

    assert result["maps_changed"] is True
    assert result["changed_assays"] == [5]
    assert labelled.mysql.reads["_assay_members"] == [[5]]
    read_children = {i for c in labelled.graph.of(q.DERIVED_FROM_OF_CHILDREN) for i in c.params["ids"]}
    assert read_children <= {10, 11, 13}
    assert labelled.graph.edge(14, 12) == edge_6
    # an internal assay renamed under its id is written at once, with no approval (the operator's RELABEL ruling)
    assert result["labels_edges"] == 2
    assert (result["labels_renamed"], result["labels_changed"]) == (2, 0)
    assert (result["labels_written"], result["labels_refreshed"]) == (0, 2)
    assert result["label_differences"]["renamed"] == {"internal_assay_title": 2, "internal_assay_titles": 2}
    assert labelled.graph.edge(11, 10)["internal_assay_title"] == "Patient Visit v2"


def test_relabel_for_maps_writes_the_rename_with_apply_label_changes(labelled):
    labelled.mysql.assay_map[5] = (99, "Patient Visit v2")
    result = targeted.relabel_for_maps(labelled.graph, DB, apply_label_changes=True)
    assert result["labels_written"] == 2
    for child, parent in ((11, 10), (13, 11)):
        assert labelled.graph.edge(child, parent)["internal_assay_title"] == "Patient Visit v2"
        assert labelled.graph.edge(child, parent)["internal_assay_titles"] == ["Patient Visit v2"]


def test_relabel_for_maps_labels_an_unlabelled_edge_between_members(labelled):
    labelled.mysql.assay_map[5] = (99, "Patient Visit v2")
    del labelled.graph.edges[next(eid for eid, e in labelled.graph.edges.items() if e["child"] == 13)]
    labelled.graph.add_edge(13, 11)
    result = targeted.relabel_for_maps(labelled.graph, DB)
    assert result["labels_new"] == 1 and result["labels_written"] == 1
    assert labelled.graph.edge(13, 11)["internal_assay_title"] == "Patient Visit v2"


def test_relabel_for_maps_recomputes_the_edges_of_a_renamed_sop(labelled):
    labelled.mysql.sops[7] = "P.SOP-1 rev"   # the child's stored title no longer names any SOP
    result = targeted.relabel_for_maps(labelled.graph, DB)

    assert result["changed_assays"] == []
    assert result["changed_sops"] == [7]
    assert result["labels_edges"] == 1
    assert result["labels_cleared"] == 1
    assert result["label_differences"]["cleared"] == {"protocol_id": 1, "protocol_title": 1}
    assert labelled.graph.edge(11, 10)["protocol_id"] == 7


def test_relabel_for_maps_stamps_the_new_label_maps_hash_and_keeps_the_catalog_hash(labelled):
    labelled.mysql.assay_map[5] = (99, "Patient Visit v2")
    result = targeted.relabel_for_maps(labelled.graph, DB)
    new_hash = labels.label_maps_hash(labelled.mysql.assay_map, SOPS)
    assert result["label_maps_hash"] == new_hash
    assert result["previous_label_maps_hash"] == labels.label_maps_hash(ASSAY_MAP, SOPS)
    assert labelled.graph.meta["label_maps_hash"] == new_hash
    assert labelled.graph.meta["catalog_hash"] == "cat-0"


# --- an attribute's count follows the samples that fill it ------------------------------------------------------------

def _declared(env, key, title, count):
    env.graph.attributes[key] = {"key": key, "sample_type_id": int(key.split(":")[0]), "title": title,
                                 "declared": True, "sample_count": count}


def test_a_declared_attribute_at_zero_that_a_synced_sample_carries_is_counted(env, tmp_path):
    _declared(env, "26:Organ", "Organ", 0)
    result = targeted.sync_samples(env.graph, DB, [10, 12], run_dir=str(tmp_path))
    assert env.graph.attributes["26:Organ"]["sample_count"] == 2
    assert (result["attribute_counts_raised"], result["catalog_resynced"]) == (1, "ok")
    assert len(env.catalog_syncs) == 1


def test_an_attribute_no_written_sample_carries_is_not_counted(env, tmp_path):
    _declared(env, "26:Weight", "Weight", 0)
    result = targeted.sync_samples(env.graph, DB, [10], run_dir=str(tmp_path))
    assert all("26:Weight" not in [r["key"] for r in call.params["rows"]]
               for call in env.graph.of(q.SET_ATTRIBUTE_COUNTS_FROM_TYPE))
    assert env.graph.attributes["26:Weight"]["sample_count"] == 0 and result["attribute_counts_raised"] == 0


def test_an_attribute_already_counted_is_left_alone_and_no_catalog_sync_runs(env, tmp_path):
    _declared(env, "26:Organ", "Organ", 5)
    result = targeted.sync_samples(env.graph, DB, [10], run_dir=str(tmp_path))
    assert env.graph.attributes["26:Organ"]["sample_count"] == 5
    assert env.graph.of(q.SET_ATTRIBUTE_COUNTS_FROM_TYPE) == [] and env.catalog_syncs == []
    assert result["attribute_counts_raised"] == 0


def test_an_undeclared_key_created_in_the_same_sync_is_counted_with_one_catalog_sync(env, tmp_path):
    del env.graph.attributes["33:Lane"]
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert (result["undeclared_attributes_created"], result["attribute_counts_raised"]) == (1, 1)
    assert env.graph.attributes["33:Lane"]["sample_count"] == 1
    assert len(env.catalog_syncs) == 1


def test_the_count_statements_read_only_attributes_at_zero_and_count_by_title():
    assert "coalesce(a.sample_count, 0) = 0" in q.ATTRIBUTES_AT_ZERO
    assert "s[r.title] IS NOT NULL" in q.SET_ATTRIBUTE_COUNTS_FROM_TYPE
    assert q.SET_ATTRIBUTE_COUNTS_FROM_TYPE.lstrip().startswith("CYPHER 25")


# --- a by-id sync writes the Project nodes it links to, and a structural gap keeps it open ----------------------------

def test_a_sample_of_a_project_with_no_node_gets_the_node_and_its_link(env, tmp_path):
    result = targeted.sync_samples(env.graph, DB, [12], run_dir=str(tmp_path))       # sample 12 is in project 16
    assert env.graph.study.projects[16] == {"id": 16, "title": "TCGA"}
    assert (result["projects_written_for_links"], result["in_project_missing"], result["structural_gaps"]) == (1, 0, 0)
    assert env.graph.first(q.MERGE_PROJECTS) < env.graph.first(q.WRITE_SAMPLES)
    assert env.graph.nodes[12]["props"]["source_hash"] is not None
    assert env.graph.of(q.DELETE_GONE_PROJECTS) == []


def test_a_new_investigation_of_a_new_project_is_linked_in_one_sync(env, tmp_path):
    """The chain Sample, IN_STUDY, Study, IN_INVESTIGATION, Investigation, IN_PROJECT, Project after ONE by-id sync."""
    targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    inv = env.graph.study.investigation_by_id(3)
    (node,) = env.graph.study.studies_by_seek(70)
    assert env.graph.study.keys_of(11) == {("seek", 70)}
    assert env.graph.study.in_investigation[node] == [inv] and env.graph.study.inv_projects[inv] == {16}
    assert 16 in env.graph.study.projects


def test_existing_project_nodes_are_neither_rewritten_nor_deleted(env, tmp_path):
    env.graph.study.projects[2] = {"id": 2, "title": "Kept as it is"}
    result = targeted.sync_samples(env.graph, DB, [10], run_dir=str(tmp_path))       # sample 10 is in project 2
    assert env.graph.study.projects[2] == {"id": 2, "title": "Kept as it is"}
    assert result["projects_written_for_links"] == 0
    assert env.graph.of(q.DELETE_GONE_PROJECTS) == []


def test_a_project_id_seek_lacks_is_counted_and_keeps_the_sample_open(env, tmp_path):
    env.mysql.projects[10] = [2, 77]                        # a projects_samples row for a project SEEK lacks
    result = targeted.sync_samples(env.graph, DB, [10], run_dir=str(tmp_path))
    assert 77 not in env.graph.study.projects
    assert (result["project_ids_not_in_seek"], result["in_project_missing"]) == (1, 1)
    assert result["structural_gaps"] == 1 and result["structural_gap_parts"] == {"in_project_missing": 1}
    assert env.graph.nodes[10]["props"]["source_hash"] is None


def test_a_sync_whose_type_and_project_links_fail_reports_the_parts_and_leaves_the_hash_null(env, tmp_path,
                                                                                            monkeypatch):
    monkeypatch.setattr(writer, "merge_missing_projects",
                        lambda d, db, ids, rows: {"projects_written_for_links": 0, "project_ids_not_in_seek": 0})
    env.graph.types.pop(26)                                 # no SampleType node for sample 10's type
    monkeypatch.setattr(targeted, "_ensure_sample_types", lambda d, db, rows, cat: ([], {}))
    result = targeted.sync_samples(env.graph, DB, [10], run_dir=str(tmp_path))
    assert result["structural_gap_parts"] == {"untyped": 1, "in_project_missing": 1}
    assert result["structural_gaps"] == 2 and result["status"] == "ok"
    assert env.graph.nodes[10]["props"]["source_hash"] is None


def test_a_clean_sync_reports_no_gap_and_keeps_the_hash(env, tmp_path):
    result = targeted.sync_samples(env.graph, DB, [10, 11, 12, 13], run_dir=str(tmp_path))
    assert (result["structural_gaps"], result["structural_gap_parts"]) == (0, {})
    assert all(env.graph.nodes[i]["props"]["source_hash"] for i in (10, 11, 12, 13))
    assert "structural_gap_samples" not in result


# --- a gap names its samples, so the drain fails only those ---------------------------------------------------------

def test_a_gap_names_only_the_gapped_sample_with_the_project_ids_seek_lacks(env, tmp_path):
    env.mysql.projects[10] = [2, 77, 78]                   # projects_samples rows for two projects SEEK lacks
    result = targeted.sync_samples(env.graph, DB, [10, 11, 12, 13], run_dir=str(tmp_path))
    assert result["structural_gap_parts"] == {"in_project_missing": 2}
    assert result["structural_gap_samples"] == {10: "in_project_missing (project ids SEEK lacks: 77, 78)"}


def test_an_untyped_sample_is_named_with_its_type(env, tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "merge_missing_projects",
                        lambda d, db, ids, rows: {"projects_written_for_links": 0, "project_ids_not_in_seek": 0})
    env.graph.types.pop(26)                                 # no SampleType node for sample 10's type
    env.graph.study.projects[2] = {"id": 2, "title": "Local"}
    env.mysql.projects[11] = [16]
    monkeypatch.setattr(targeted, "_ensure_sample_types", lambda d, db, rows, cat: ([], {}))
    result = targeted.sync_samples(env.graph, DB, [10, 11], run_dir=str(tmp_path))
    assert result["structural_gap_parts"] == {"untyped": 1, "in_project_missing": 1}
    # project 16 has no node though SEEK has it (the writer is stubbed here), so it is named apart
    assert result["structural_gap_samples"] == {10: "untyped (no SampleType node for type 26)",
                                                11: "in_project_missing (no Project node for 16)"}


def test_a_study_whose_investigation_seek_lacks_names_the_samples_it_links(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "investigations", lambda: [])      # study 70 names investigation 3, SEEK lacks it
    result = targeted.sync_samples(env.graph, DB, [10, 11, 12], run_dir=str(tmp_path))
    assert result["structural_gap_parts"] == {"seek_study_investigation_missing": 1}
    reason = "seek_study_investigation_missing (study 70: investigation 3, which SEEK lacks)"
    assert result["structural_gap_samples"] == {10: reason, 11: reason}


def test_a_gap_no_sample_can_be_traced_to_names_every_written_sample_of_its_chunk(env, tmp_path, monkeypatch):
    real = writer.write_seek_studies
    monkeypatch.setattr(writer, "write_seek_studies",
                        lambda *a, **k: dict(real(*a, **k), in_study_samples_missing=1))
    result = targeted.sync_samples(env.graph, DB, [10, 11], run_dir=str(tmp_path))
    reason = "in_study_samples_missing 1 in its chunk, not traced to a sample"
    assert result["structural_gap_samples"] == {10: reason, 11: reason}


def test_a_count_the_trace_does_not_match_names_every_written_sample(env, tmp_path, monkeypatch):
    """The statements' counts are the truth: a trace that finds fewer links than they count names the whole chunk."""
    env.mysql.projects[10] = [2, 77]
    real = writer.write_samples
    monkeypatch.setattr(writer, "write_samples",
                        lambda *a, **k: dict(real(*a, **k), in_project_missing=2))
    result = targeted.sync_samples(env.graph, DB, [10, 11], run_dir=str(tmp_path))
    reason = "in_project_missing 2 in its chunk, not traced to a sample"
    assert result["structural_gap_samples"] == {10: reason, 11: reason}


def test_a_sample_type_sync_names_the_gapped_samples_of_every_chunk(env, tmp_path):
    env.mysql.projects[10] = [77]
    env.mysql.projects[12] = [78]
    result = targeted.sync_samples_of_type(env.graph, DB, 26, run_dir=str(tmp_path), chunk=1)
    assert result["chunks"] == 2
    assert result["structural_gap_samples"] == {10: "in_project_missing (project ids SEEK lacks: 77)",
                                                12: "in_project_missing (project ids SEEK lacks: 78)"}


# --- sync_assays (schema 1.3) --------------------------------------------------------------------

@pytest.fixture
def assayed(env):
    """Lineage 11 -> 10 and 13 -> 11 inside SEEK assay 5, and the assay layer a first sync_assays wrote for it."""
    env.graph.add_edge(11, 10)
    env.graph.add_edge(13, 11)
    assert targeted.sync_assays(env.graph, DB)["status"] == "ok"
    env.graph.calls.clear()
    env.mysql.reads.clear()
    return env


def test_sync_assays_writes_the_assay_layer_onto_a_graph_without_one(env):
    env.graph.assay_nodes.clear()
    env.graph.add_edge(11, 10)
    env.graph.add_edge(13, 11)
    result = targeted.sync_assays(env.graph, DB)

    assert result["status"] == "ok"
    assert env.graph.assay_nodes == {99: {"id": 99, "title": "Patient Visit", "description": "A visit.",
                                          "input_types": ["TIS"], "output_types": ["D.SEQ"], "has_context": True}}
    assert env.graph.runs == {(99, 70, (5,))}
    assert env.graph.catalog_edges == {("ACCEPTED_BY", "TIS", 99, True, 0), ("GENERATES", "D.SEQ", 99, None, 0)}
    assert env.graph.assay_edges == {10: {("INPUT_TO", 99, (5,))},
                                     11: {("OUTPUT_OF", 99, (5,)), ("INPUT_TO", 99, (5,))},
                                     13: {("OUTPUT_OF", 99, (5,))}}
    assert (result["assay_marks_added"], result["assay_members_to_rewrite"]) == (1, 3)
    order = [env.graph.first(s) for s in (q.MERGE_ASSAYS, q.RUN_IN_PAIRS, q.REPLACE_SAMPLE_ASSAY_EDGES,
                                          q.REPLACE_ASSAY_RUNS, q.REPLACE_ASSAY_CATALOG_EDGES, q.DELETE_GONE_ASSAYS)]
    assert order == sorted(order)


def test_a_second_sync_assays_marks_nothing_and_rewrites_no_member(assayed):
    before = copy.deepcopy(assayed.graph.assay_edges)
    result = targeted.sync_assays(assayed.graph, DB)
    assert (result["assay_marks_added"], result["assay_marks_removed"]) == (0, 0)
    assert assayed.graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES) == []
    assert "sample_ids_in_assays" not in assayed.mysql.reads
    assert assayed.graph.assay_edges == before


def test_a_new_mapping_that_wins_no_edge_label_still_reaches_the_members(assayed):
    """SEEK assay 5 gains a second internal assay, 120. The label rule keeps the smallest internal id (99), so
    relabel_for_maps sees no change; sync_assays still gives every member its edge to Assay 120."""
    assayed.mysql.internal.append({"id": 120, "title": "Visit two"})
    assayed.mysql.pairs.append((5, 120))
    result = targeted.sync_assays(assayed.graph, DB)

    assert result["assay_marked_seek_ids"] == [5]
    assert assayed.graph.assay_edges[10] == {("INPUT_TO", 99, (5,)), ("INPUT_TO", 120, (5,))}
    assert assayed.graph.assay_edges[13] == {("OUTPUT_OF", 99, (5,)), ("OUTPUT_OF", 120, (5,))}
    assert assayed.graph.runs == {(99, 70, (5,)), (120, 70, (5,))}
    rewritten = [sorted(r["id"] for r in c.params["rows"]) for c in assayed.graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES)]
    assert rewritten == [[10, 11, 13]]


def test_a_mapping_no_member_has_a_role_in_is_marked_once_only(assayed):
    """SEEK assay 6 (sample 12 alone, no lineage inside it) is mapped to 98: its member is read once and gets no
    edge, and RUN_IN then records the mapping, so the next run reads nothing."""
    assayed.mysql.internal.append({"id": 98, "title": "Sequencing run"})
    assayed.mysql.pairs.append((6, 98))
    first = targeted.sync_assays(assayed.graph, DB)
    assert (first["assay_marked_seek_ids"], first["assay_members_to_rewrite"]) == ([6], 1)
    assert first["assay_edge_members_without_role"] == 1
    assert assayed.graph.assay_edges.get(12, set()) == set()
    assert (98, 70, (6,)) in assayed.graph.runs

    assayed.mysql.reads.clear()
    second = targeted.sync_assays(assayed.graph, DB)
    assert second["assay_marks_added"] == 0 and "sample_ids_in_assays" not in assayed.mysql.reads


def test_a_seek_assay_deleted_from_seek_takes_its_id_off_the_sample_edges(assayed):
    assayed.mysql.seek_studies = [(6, 70)]           # SEEK assay 5 is gone from assays
    for sid in (10, 11, 13):
        assayed.mysql.assays[sid] = []               # and so are its assay_assets rows; its dmac mapping lingers
    result = targeted.sync_assays(assayed.graph, DB)

    assert (result["assay_marks_removed"], result["assay_marked_seek_ids"]) == (1, [5])
    assert all(assayed.graph.assay_edges[sid] == set() for sid in (10, 11, 13))
    assert assayed.graph.runs == set()
    assert 99 in assayed.graph.assay_nodes           # the internal assay itself is still there


def test_an_internal_assay_deleted_in_the_admin_loses_its_sample_edges_and_then_its_node(assayed):
    """Internal assay 98 (SEEK assay 6: sample 14 made from 12) is deleted with its mapping row while samples still
    point at it. Its edges go, the members of Assay 99 are not touched, and the node goes last."""
    graph, mysql = assayed.graph, assayed.mysql
    graph.add_sample(14, 26)
    graph.add_edge(14, 12)
    mysql.assays[14] = [6]
    mysql.internal.append({"id": 98, "title": "Sequencing run"})
    mysql.pairs.append((6, 98))
    targeted.sync_assays(graph, DB)
    assert graph.assay_edges[14] == {("OUTPUT_OF", 98, (6,))} and graph.assay_edges[12] == {("INPUT_TO", 98, (6,))}
    untouched = {sid: set(graph.assay_edges[sid]) for sid in (10, 11, 13)}
    graph.calls.clear()

    mysql.internal = [r for r in mysql.internal if r["id"] != 98]
    mysql.pairs.remove((6, 98))
    result = targeted.sync_assays(graph, DB)

    assert result["status"] == "ok" and result["assay_marked_seek_ids"] == [6]
    assert graph.assay_edges[12] == set() and graph.assay_edges[14] == set()
    assert {sid: graph.assay_edges[sid] for sid in (10, 11, 13)} == untouched
    assert sorted(r["id"] for c in graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES) for r in c.params["rows"]) == [12, 14]
    assert 98 not in graph.assay_nodes and 99 in graph.assay_nodes
    last_write = max(i for i, c in enumerate(graph.calls) if not c.read)
    assert graph.calls[last_write].query == q.DELETE_GONE_ASSAYS
    assert result["assays_deleted"] == 1


def test_a_crash_between_member_chunks_leaves_the_rest_for_the_retry(assayed):
    graph, mysql = assayed.graph, assayed.mysql
    mysql.internal.append({"id": 120, "title": "Visit two"})
    mysql.pairs[:] = [(5, 120)]                      # SEEK assay 5 moves from 99 to 120
    graph.faults[q.REPLACE_SAMPLE_ASSAY_EDGES] = {2}
    with pytest.raises(RuntimeError, match="injected failure"):
        targeted.sync_assays(graph, DB, chunk=1)
    assert graph.runs == {(99, 70, (5,))}             # still the mapping the edges were written from
    assert graph.assay_edges[10] == {("INPUT_TO", 120, (5,))}                           # the first chunk landed
    assert graph.assay_edges[11] == {("OUTPUT_OF", 99, (5,)), ("INPUT_TO", 99, (5,))}  # the second did not

    graph.faults.clear()
    retry = targeted.sync_assays(graph, DB, chunk=1)
    assert retry["status"] == "ok" and retry["assay_marked_seek_ids"] == [5]
    assert graph.assay_edges == {10: {("INPUT_TO", 120, (5,))},
                                 11: {("OUTPUT_OF", 120, (5,)), ("INPUT_TO", 120, (5,))},
                                 13: {("OUTPUT_OF", 120, (5,))}}
    assert graph.runs == {(120, 70, (5,))}


def test_above_the_rewrite_max_a_full_sync_is_enqueued_and_run_in_is_left_for_it(assayed, monkeypatch):
    queued = []
    monkeypatch.setattr(hooks, "enqueue", lambda kind, key, payload=None, **kw: queued.append((kind, key)) or True)
    monkeypatch.setattr(targeted, "ASSAY_REWRITE_MAX", 2)
    assayed.mysql.internal.append({"id": 120, "title": "Visit two"})
    assayed.mysql.pairs.append((5, 120))
    result = targeted.sync_assays(assayed.graph, DB, now=datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc))

    assert result["status"] == "ok" and result["assay_rewrite_guard_tripped"] is True
    assert queued == [("full", "slot:2026-09-25-assays")]
    assert assayed.graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES) == [] and assayed.graph.of(q.REPLACE_ASSAY_RUNS) == []
    assert assayed.graph.runs == {(99, 70, (5,))}
    assert 120 in assayed.graph.assay_nodes and assayed.graph.of(q.REPLACE_ASSAY_CATALOG_EDGES)


def test_an_assay_moved_to_another_study_moves_run_in_and_rewrites_no_member(assayed):
    assayed.mysql.seek_studies = [(5, 71), (6, 70)]
    assayed.mysql.studies.append({"id": 71, "title": "Study seventy-one", "description": None, "investigation_id": 3})
    result = targeted.sync_assays(assayed.graph, DB)
    assert assayed.graph.runs == {(99, 71, (5,))}
    (study,) = assayed.graph.study.studies_by_seek(71)      # the studies release's node, SEEK's whole row
    assert assayed.graph.study.studies[study]["title"] == "Study seventy-one"
    # its Investigation is written before it (the studies release's A1), so it is never left under none
    assert assayed.graph.first(q.MERGE_INVESTIGATIONS) < assayed.graph.first(q.MERGE_SEEK_STUDIES)
    assert assayed.graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES) == [] and result["assay_members_to_rewrite"] == 0


def test_members_false_writes_the_nodes_catalog_edges_and_deletions_only(assayed):
    assayed.mysql.internal.append({"id": 120, "title": "Visit two"})
    assayed.mysql.pairs.append((5, 120))
    result = targeted.sync_assays(assayed.graph, DB, members=False)

    assert result["status"] == "ok"
    for statement in (q.RUN_IN_PAIRS, q.SAMPLE_ASSAY_EDGE_PAIRS, q.REPLACE_SAMPLE_ASSAY_EDGES, q.REPLACE_ASSAY_RUNS):
        assert assayed.graph.of(statement) == []
    assert 120 in assayed.graph.assay_nodes and assayed.graph.runs == {(99, 70, (5,))}
    # RUN_IN still lacks (5, 120), so the next full run rewrites the members
    assert targeted.sync_assays(assayed.graph, DB)["assay_marked_seek_ids"] == [5]


def test_sync_assays_stops_at_a_member_chunk_that_cannot_take_the_lock(assayed):
    assayed.mysql.internal.append({"id": 120, "title": "Visit two"})
    assayed.mysql.pairs.append((5, 120))
    assayed.lock.outcomes = [True, True, False]      # the nodes, the first member chunk, then busy
    result = targeted.sync_assays(assayed.graph, DB, chunk=1)
    assert result["status"] == "lock_timeout"
    assert len(assayed.graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES)) == 1
    assert assayed.graph.of(q.REPLACE_ASSAY_RUNS) == []


def test_a_share_clone_mapped_to_the_same_internal_assay_adds_its_run_in_and_rewrites_no_member(assayed):
    """The studies tool's share mode: a new SEEK assay 7 in study 71, mapped by a copied row to internal assay 99 and
    holding no member yet (its mapping and its assay_map row come before the link unit). RUN_IN gains (99, 71) with
    the clone's id alone and keeps (99, 70); it is marked once and rewrites no member."""
    mysql = assayed.mysql
    mysql.seek_studies.append((7, 71))
    mysql.studies.append({"id": 71, "title": "Study seventy-one", "description": None, "investigation_id": 3})
    mysql.pairs.append((7, 99))
    first = targeted.sync_assays(assayed.graph, DB)
    assert (first["assay_marked_seek_ids"], first["assay_members_to_rewrite"]) == ([7], 0)
    assert assayed.graph.of(q.REPLACE_SAMPLE_ASSAY_EDGES) == []
    assert assayed.graph.runs == {(99, 70, (5,)), (99, 71, (7,))}
    assert targeted.sync_assays(assayed.graph, DB)["assay_marks_added"] == 0


# --- sync_small_tables ---------------------------------------------------------------------------

def test_sync_small_tables_rewrites_projects_investigations_people_and_study_nodes(env):
    node = env.graph.study.add_study(seek_study_id=70, title="Study seventy")
    result = targeted.sync_small_tables(env.graph, DB)

    order = [env.graph.first(s) for s in (q.MERGE_PROJECTS, q.MERGE_INVESTIGATIONS, q.MERGE_MEMBER_OF,
                                            q.MERGE_SEEK_STUDIES)]
    assert order == sorted(order)
    assert result["status"] == "ok"
    assert result["projects_written"] == 2
    assert result["investigations_written"] == 1
    assert result["memberships_written"] == 1
    assert (result["seek_studies"], result["seek_study_nodes_written"]) == (1, 1)
    assert env.graph.study.studies[node] == {"seek_study_id": 70, "title": "Study seventy (renamed)",
                                             "description": "About seventy"}
    assert env.graph.study.investigation_ids_of(node) == [3]


def test_every_seek_study_gets_a_node_even_with_no_sample(env):
    result = targeted.sync_small_tables(env.graph, DB)
    (node,) = env.graph.study.studies_by_seek(70)
    assert env.graph.study.investigation_ids_of(node) == [3] and result["seek_study_nodes_written"] == 1


def test_a_written_sample_with_no_seek_link_still_gets_its_row(env, tmp_path, monkeypatch):
    """The by-id path skipped IN_STUDY when a sample had no SEEK link, so a sample that left every SEEK study kept
    its links for ever. SEEK places sample 12 in no study: it gets its row, keeps its link and is counted."""
    old = env.graph.study.add_study(seek_study_id=71, title="Old study")
    env.graph.study.link(12, old)
    monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    result = targeted.sync_samples(env.graph, DB, [12], run_dir=str(tmp_path))
    assert env.graph.of(q.REPLACE_SEEK_IN_STUDY)[0].params["rows"] == [
        {"sample_id": 12, "study_ids": [], "withhold": [], "paper": False, "remove": []}]
    assert env.graph.study.keys_of(12) == {("seek", 71)}
    assert result["in_study_kept_no_seek_study"] == 1


@pytest.mark.parametrize("switch", ["follow", "add"])
def test_a_moved_sample_loses_its_old_link_only_with_the_switch_on(env, tmp_path, monkeypatch, switch):
    old = env.graph.study.add_study(seek_study_id=71, title="Old study")
    env.graph.study.link(11, old)                        # SEEK now files 11 under study 70 only
    monkeypatch.setenv(study_links.SWITCH_ENV, switch)
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    archive = tmp_path / study_links.ARCHIVE_FILE
    if switch == "follow":
        assert env.graph.study.keys_of(11) == {("seek", 70)}
        assert result["in_study_removed"] == 1
        assert archive.read_text(encoding="utf-8").splitlines()[1].split("\t")[4] == "by_id"
    else:
        assert env.graph.study.keys_of(11) == {("seek", 70), ("seek", 71)}
        assert result["in_study_stale"] == 1 and not archive.exists()


def test_the_switch_is_read_once_per_call(env, tmp_path, monkeypatch):
    reads = []
    monkeypatch.setattr(study_links, "follows_seek", lambda env=None: reads.append(1) or False)
    targeted.sync_samples(env.graph, DB, [10, 11, 12, 13], run_dir=str(tmp_path), chunk=1)
    assert len(reads) == 1


def test_a_sample_on_an_unmerged_legacy_node_keeps_it_and_gets_no_seek_link(env, tmp_path, monkeypatch):
    """A box rebuilt before its merge: the legacy node has no seek_study_id, so its samples are paper samples, and
    SEEK's study 70 is in the legacy node's own investigation."""
    inv = env.graph.study.add_investigation(3, "TCGA")
    legacy = env.graph.study.add_study(id=70, title="Study seventy", investigation=inv)
    env.graph.study.link(11, legacy)
    monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert env.graph.study.keys_of(11) == {("id", 70)}
    assert result["in_study_withheld"] == 1 and result["in_study_removed"] == 0
    assert len(env.graph.study.studies_by_seek(70)) == 1


def test_small_tables_archive_then_delete_an_investigation_seek_lost_that_no_study_holds(env, tmp_path):
    gone = env.graph.study.add_investigation(9, "Gone")
    held = env.graph.study.add_investigation(8, "Still held")
    env.graph.study.add_study(id=40, title="A paper", investigation=held)
    result = targeted.sync_small_tables(env.graph, DB, run_dir=str(tmp_path))
    assert gone not in env.graph.study.investigations and held in env.graph.study.investigations
    assert (result["investigations_deleted"], result["investigations_not_in_seek_held"]) == (1, 1)
    lines = (tmp_path / writer.INVESTIGATIONS_DELETED_FILE).read_text(encoding="utf-8").splitlines()
    assert lines[1].split("\t")[:2] == ["9", "Gone"]


def test_small_tables_delete_an_investigation_held_only_by_a_gone_seek_studys_node(env, tmp_path):
    """SEEK deletes an investigation after its studies, and a Study node is not deleted in this release. A node
    whose SEEK study is gone no longer holds its Investigation, which is archived and deleted; the node stays, without
    its IN_INVESTIGATION. A paper node, and the node of a study SEEK still has, still hold theirs."""
    s = env.graph.study
    dead_inv = s.add_investigation(9, "Gone with its study")
    dead = s.add_study(seek_study_id=41, title="A gone study", investigation=dead_inv)
    paper_inv = s.add_investigation(8, "A paper's")
    s.add_study(id=40, title="A paper", investigation=paper_inv)
    live_inv = s.add_investigation(7, "Left by study seventy")
    s.add_study(seek_study_id=70, title="Study seventy", investigation=live_inv)
    result = targeted.sync_small_tables(env.graph, DB, run_dir=str(tmp_path))
    assert dead_inv not in s.investigations and dead in s.studies and s.in_investigation[dead] == []
    assert paper_inv in s.investigations and live_inv in s.investigations
    assert (result["investigations_deleted"], result["investigations_not_in_seek_held"]) == (1, 2)
    lines = (tmp_path / writer.INVESTIGATIONS_DELETED_FILE).read_text(encoding="utf-8").splitlines()
    assert [line.split("\t")[:2] for line in lines[1:]] == [["9", "Gone with its study"]]


# --- the one MySQL reader of its own -------------------------------------------------------------

class _Cursor:
    def __init__(self, rows):
        self.rows, self.executed = list(rows), []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), list(params or [])))

    def fetchmany(self, size=1):
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_assay_members_reads_assay_assets_with_bound_parameters(monkeypatch, settings):
    cursor = _Cursor([(10, 5), (11, 5), (11, 6), (13, 5)])
    monkeypatch.setattr(sources, "connections", {settings.SEEK_DATABASE: SimpleNamespace(cursor=lambda: cursor)})
    assert targeted._assay_members([6, 5, 5]) == {10: frozenset({5}), 11: frozenset({5, 6}), 13: frozenset({5})}
    sql, params = cursor.executed[0]
    assert "FROM assay_assets" in sql and "asset_type = %s" in sql and "assay_id IN (%s, %s)" in sql
    assert params == ["Sample", 5, 6]
