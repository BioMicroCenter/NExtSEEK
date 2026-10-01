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
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from neo4j import RoutingControl

from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.graph_sync import catalog, labels, projection, run, sources, state, targeted, writer
from nextseek_api.graph_sync import cypher as q

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
STUDY_LINKS = [{"sample_id": 10, "study_id": 70, "study_title": "Study seventy", "investigation_id": 3},
               {"sample_id": 11, "study_id": 70, "study_title": "Study seventy", "investigation_id": 3}]

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

    def __init__(self, version="1.2"):
        self.meta = None if version is None else {"schema_version": version, "catalog_hash": "cat-0",
                                                  "label_maps_hash": None}
        self.types = {26: "TIS", 33: "D.SEQ"}
        self.nodes: dict = {}
        self.edges: dict = {}
        self.attributes = {"33:Lane": catalog.undeclared_attribute(33, "D.SEQ", "Lane")}
        self.studies: dict = {}
        self.in_study: set = set()
        self.calls: list = []
        self.before_delete: list = []
        self._next = 0
        self.handlers = {
            q.READ_GRAPHMETA: self._graphmeta,
            q.WRITE_GRAPHMETA_WITH_LABEL_MAPS: self._write_graphmeta,
            targeted.SAMPLE_TYPES_PRESENT: self._types_present,
            targeted.TYPES_OF_SAMPLES: self._types_of_samples,
            targeted.SET_SAMPLE_TYPE_COUNTS_FOR: lambda p: [{"n": len([i for i in p["ids"] if i in self.types])}],
            targeted.ATTRIBUTE_KEYS_PRESENT: lambda p: [{"key": k} for k in p["keys"] if k in self.attributes],
            targeted.CREATE_UNDECLARED_ATTRIBUTES: self._create_attributes,
            targeted.GRAPH_ASSAY_LABELS: self._assay_labels,
            targeted.GRAPH_PROTOCOL_LABELS: self._protocol_labels,
            targeted.EDGES_WITH_PROTOCOLS: self._edges_with_protocols,
            targeted.SET_SEEK_STUDY_TITLES: self._study_titles,
            q.WRITE_SAMPLES: self._write_samples,
            q.DERIVED_FROM_ID_FORM: lambda p: [],
            q.WRITE_MISSING_LINEAGE: self._write_missing_lineage,
            q.DERIVED_FROM_OF_CHILDREN: self._edges_of_children,
            q.DELETE_UNDECLARED_DERIVED_FROM: self._delete_edges,
            q.EDGES_INCIDENT: self._edges_incident,
            q.WRITE_EDGE_LABELS_NEW: lambda p: self._write_labels(p, approved=False),
            q.WRITE_EDGE_LABELS_CHANGED: lambda p: self._write_labels(p, approved=True),
            q.SAMPLES_IN_PAPER_STUDIES: lambda p: [],
            q.MERGE_SEEK_STUDIES: self._merge_studies,
            q.MERGE_SEEK_IN_STUDY: self._merge_in_study,
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
        }

    # the driver surface
    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        read = kwargs.get("routing_") == RoutingControl.READ
        self.calls.append(SimpleNamespace(query=query, params=params, read=read, database=database_))
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
            typed += node["type_id"] is not None
            linked += len(props.get("project_ids") or ())
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

    def _merge_studies(self, p):
        for r in p["rows"]:
            self.studies[r["study_id"]] = r["title"]
        return []

    def _merge_in_study(self, p):
        linked = 0
        for r in p["rows"]:
            if self._is_sample(r["sample_id"]) and r["study_id"] in self.studies:
                self.in_study.add((r["sample_id"], r["study_id"]))
                linked += 1
        return [{"linked": linked}]

    def _study_titles(self, p):
        n = 0
        for r in p["rows"]:
            if r["study_id"] in self.studies:
                self.studies[r["study_id"]] = r["title"]
                n += 1
        return [{"n": n}]

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


# --- fixtures ------------------------------------------------------------------------------------

@pytest.fixture
def graph():
    g = FakeGraph()
    for sid, type_id in ((10, 26), (11, 33), (12, 26), (13, 33)):
        g.add_sample(sid, type_id)
    return g


@pytest.fixture
def mysql(monkeypatch):
    """Install the MySQL world into ``sources``; tests may change it before a call. ``reads`` records the by-id
    readers' arguments."""
    world = SimpleNamespace(samples=copy.deepcopy(SAMPLES), projects=copy.deepcopy(PROJECTS),
                            assays=copy.deepcopy(ASSAYS), assay_map=dict(ASSAY_MAP), sops=dict(SOPS),
                            links=copy.deepcopy(STUDY_LINKS), reads=defaultdict(list))

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
        "studies": lambda: [{"id": 70, "title": "Study seventy (renamed)", "investigation_id": 3}],
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


@pytest.fixture
def catalog_syncs(monkeypatch, graph, lock):
    """``run.catalog_sync`` replaced by a recorder that writes the MySQL types into the graph. Each entry is
    ``(calls sent before it, whether the lock was held)``."""
    seen = []

    def fake(driver, db, dry_run=False):
        seen.append((len(graph.calls), lock.held))
        graph.types.update({t["id"]: t["title"] for t in TYPES})
        return {"mode": "catalog", "status": "ok"}

    monkeypatch.setattr(run, "catalog_sync", fake)
    return seen


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
}


@pytest.mark.parametrize("version", ["1.1", None])
@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_refuses_a_graph_not_at_the_writer_version_and_writes_nothing(env, name, version):
    env.graph.meta = None if version is None else dict(env.graph.meta, schema_version=version)
    result = ENTRY_POINTS[name](env.graph)
    assert result["status"] == "not_at_version"
    assert result["schema_version"] == version
    assert result["writer_version"] == writer.SCHEMA_VERSION == "1.2"
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


def test_runs_catalog_sync_first_when_a_sample_type_node_holds_another_title(env, tmp_path):
    env.graph.types[33] = "D.SEQ old"
    targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert len(env.catalog_syncs) == 1
    assert env.catalog_syncs[0][0] <= env.graph.first(q.WRITE_SAMPLES)


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
    assert env.graph.in_study == {(11, 70)}


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


def test_an_equal_label_sends_no_write(env, tmp_path):
    env.graph.add_edge(11, 10, **_as_set(LABEL_11_10))
    result = targeted.sync_samples(env.graph, DB, [11], run_dir=str(tmp_path))
    assert result["labels_equal"] == 1
    assert env.graph.of(q.WRITE_EDGE_LABELS_NEW) == []


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
    # a renamed title is a changed label: reported, not written, without the operator's approval
    assert result["labels_edges"] == 2
    assert result["labels_changed"] == 2
    assert result["labels_written"] == 0
    assert result["label_differences"]["changed"] == {"internal_assay_title": 2, "internal_assay_titles": 2}
    assert labelled.graph.edge(11, 10)["internal_assay_title"] == "Patient Visit"


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


# --- sync_small_tables ---------------------------------------------------------------------------

def test_sync_small_tables_rewrites_projects_investigations_people_and_study_titles(env):
    env.graph.studies[70] = "Study seventy"
    result = targeted.sync_small_tables(env.graph, DB)

    order = [env.graph.first(s) for s in (q.MERGE_PROJECTS, q.MERGE_INVESTIGATIONS, q.MERGE_MEMBER_OF,
                                            targeted.SET_SEEK_STUDY_TITLES)]
    assert order == sorted(order)
    assert result["status"] == "ok"
    assert result["projects_written"] == 2
    assert result["investigations_written"] == 1
    assert result["memberships_written"] == 1
    assert result["seek_study_titles_set"] == 1
    assert env.graph.studies[70] == "Study seventy (renamed)"
    assert env.graph.of(q.MERGE_SEEK_STUDIES) == []   # no Study node is created for a study with no samples


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
