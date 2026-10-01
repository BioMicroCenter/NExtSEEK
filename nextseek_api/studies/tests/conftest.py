"""Fakes for the studies tool's suite. Synthetic data only (tool spec T28): no title, id or UID here is a box's.

``World`` is a small SEEK (investigations, studies, assays, assay_assets rows, samples), the dmac mapping and a graph
(stored DERIVED_FROM edges). ``FakeReader`` answers the snapshot interface from it. ``FakeDriver`` answers graph
statements by their exact text and fails on any other.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import NamedTuple, Optional

import pytest
from neo4j import RoutingControl

from nextseek_api.studies.buckets import buckets_from_rows

try:  # the snapshot module lands in Task 13; the rows are defined there from then on
    from nextseek_api.studies.snapshot import AssayRow, StudyRow
except ImportError:  # pragma: no cover - only before Task 13
    class StudyRow(NamedTuple):
        id: int
        investigation_id: Optional[int]
        title: str
        description: Optional[str] = None

    class AssayRow(NamedTuple):
        id: int
        study_id: Optional[int]
        title: str

LABEL_KEYS = ("assay_id", "internal_assay_id", "internal_assay_title", "internal_assay_ids",
              "internal_assay_titles", "protocol_id", "protocol_title")


def uid(n: int, code: str = "AAA", kind: str = "TIS") -> str:
    return f"{kind}-260101{code}-{n}"


@dataclass
class World:
    investigations: dict = field(default_factory=dict)         # id -> title
    investigation_projects: dict = field(default_factory=dict)  # id -> set of project ids
    studies: list = field(default_factory=list)                 # [StudyRow]
    assays: dict = field(default_factory=dict)                  # id -> AssayRow
    links: list = field(default_factory=list)                   # [(assay, sample, direction)], assay_assets.id order
    samples: dict = field(default_factory=dict)                 # id -> {"uuid": str, "meta": dict | str}
    mapping: dict = field(default_factory=dict)                 # assay id -> [internal ids]
    internal_titles: dict = field(default_factory=dict)         # internal id -> title
    sample_projects: dict = field(default_factory=dict)         # sample id -> set of project ids
    next_study_id: int = 100
    max_assay_id: Optional[int] = None
    graph_max_study_id: Optional[int] = 60
    stored: list = field(default_factory=list)                  # [{"child_id", "parent_id", "stored": {...}}]
    sops: dict = field(default_factory=dict)
    assay_reps: dict = field(default_factory=dict)              # assay id -> GET body
    study_reps: dict = field(default_factory=dict)              # study id -> GET body

    def assay_map(self) -> dict:
        out = {}
        for assay_id, row in self.assays.items():
            internal = sorted(self.mapping.get(assay_id) or [])
            out[assay_id] = ((internal[0], self.internal_titles.get(internal[0])) if internal
                             else (None, row.title or ""))
        return dict(sorted(out.items()))

    def labels(self, child: int, parent: int, protocol=(None, None)) -> dict:
        """The seven labels the rule gives an edge now, for building stored edges in a fixture."""
        from nextseek_api.graph_sync import labels

        mine = {a for a, s, _d in self.links if s == child}
        theirs = {a for a, s, _d in self.links if s == parent}
        return labels.edge_labels(mine, theirs, self.assay_map(), protocol)


class FakeReader:
    """The snapshot interface over a World (nextseek_api/studies/snapshot.py's SnapshotReader, method for method)."""

    def __init__(self, world: World):
        self.w = world
        self.calls: list[str] = []

    def investigations(self):
        return dict(self.w.investigations)

    def studies(self):
        return list(self.w.studies)

    def buckets(self):
        return buckets_from_rows((s.id, s.investigation_id, s.title) for s in self.w.studies)

    def uid_counts(self, uids):
        counts = {}
        for sample in self.w.samples.values():
            if sample["uuid"] in uids:
                counts[sample["uuid"]] = counts.get(sample["uuid"], 0) + 1
        return counts

    def sample_ids_for_uids(self, uids):
        return {s["uuid"]: sid for sid, s in self.w.samples.items() if s["uuid"] in uids}

    def existing_sample_ids(self, ids):
        return {int(i) for i in ids if int(i) in self.w.samples}

    def memberships(self, sample_ids):
        wanted = set(sample_ids)
        out: dict = {}
        for assay, sample, direction in self.w.links:
            if sample in wanted:
                out.setdefault(sample, {}).setdefault(assay, direction)
        return out

    def assays(self, assay_ids):
        return {a: self.w.assays[a] for a in assay_ids if a in self.w.assays}

    def study_assays(self, study_ids):
        out = {s: [] for s in study_ids}
        for row in sorted(self.w.assays.values()):
            if row.study_id in out:
                out[row.study_id].append(row)
        return out

    def assay_rows(self, assay_ids):
        wanted = set(assay_ids)
        out = {a: [] for a in wanted}
        for assay, sample, direction in self.w.links:
            if assay in wanted:
                out[assay].append((sample, direction))
        return out

    def sample_rows(self, ids):
        out = {}
        for sid in ids:
            sample = self.w.samples.get(sid)
            if sample is None:
                continue
            meta = sample.get("meta", {})
            out[sid] = {"id": sid, "uuid": sample["uuid"],
                        "json_metadata": meta if isinstance(meta, str) else json.dumps(meta)}
        return out

    def uuid_index(self, tokens):
        wanted = set(tokens)
        out: dict = {}
        for sid, sample in sorted(self.w.samples.items()):
            if sample["uuid"] in wanted:
                out.setdefault(sample["uuid"], []).append(sid)
        return out

    def mapping_rows(self, assay_ids):
        return {a: sorted(self.w.mapping.get(a) or []) for a in assay_ids}

    def sample_projects(self, ids):
        return {i: set(self.w.sample_projects.get(i, set())) for i in ids if i in self.w.sample_projects}

    def investigation_projects(self, ids):
        return {i: set(self.w.investigation_projects.get(i, set())) for i in ids}

    def next_study_id(self):
        return self.w.next_study_id

    def max_assay_id(self):
        return self.w.max_assay_id if self.w.max_assay_id is not None else max(self.w.assays, default=0)

    def graph_max_study_id(self):
        return self.w.graph_max_study_id

    def stored_edges(self, ids):
        wanted = set(ids)
        out = []
        for n, edge in enumerate(self.w.stored):
            if edge["child_id"] in wanted or edge["parent_id"] in wanted:
                stored = {k: edge["stored"].get(k) for k in LABEL_KEYS}
                out.append({"child_id": edge["child_id"], "parent_id": edge["parent_id"],
                            "element_id": f"e{n}", "stored": stored})
        return out

    def assay_map(self):
        return self.w.assay_map()

    def sops(self):
        return dict(self.w.sops)

    def assay_representation(self, assay_id):
        self.calls.append(f"GET /assays/{assay_id}")
        return self.w.assay_reps[assay_id]

    def study_representation(self, study_id):
        self.calls.append(f"GET /studies/{study_id}")
        return self.w.study_reps[study_id]


class FakeDriver:
    """``execute_query`` answered by statement text; each answer is a list of dict records. Records every call and
    whether it was sent as a read."""

    def __init__(self, handlers: dict):
        self.handlers = dict(handlers)
        self.calls: list = []

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        read = kwargs.get("routing_") == RoutingControl.READ
        self.calls.append(SimpleNamespace(query=query, params=params, read=read))
        handler = self.handlers.get(query)
        if handler is None:
            raise AssertionError(f"unexpected statement:\n{query}")
        records = handler(params)
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace()))


def assay_rep(assay_id: int, title: str, *, study_id: int = 20, technology: bool = True) -> dict:
    attributes = {"title": title, "description": f"About {title}",
                  "assay_class": {"key": "EXP", "title": "Experimental Assay"},
                  "assay_type": {"label": "Assay type", "uri": "http://example.org/assay/1"},
                  "tags": ["synthetic"], "policy": {"access": "no_access", "permissions": []},
                  "other_creators": None}
    if technology:
        attributes["technology_type"] = {"label": "Tech", "uri": "http://example.org/tech/1"}
    return {"data": {"id": str(assay_id), "type": "assays", "attributes": attributes, "relationships": {
        "study": {"data": {"id": str(study_id), "type": "studies"}},
        "sops": {"data": [{"id": "7", "type": "sops"}]},
        "organisms": {"data": []},
        "creators": {"data": [{"id": "55", "type": "people"}]},
        "samples": {"data": [{"id": "1", "type": "samples"}]},
        "data_files": {"data": [{"id": "9", "type": "data_files"}]}}}}


def study_rep(study_id: int, title: str) -> dict:
    permissions = [{"resource": {"id": "3", "type": "projects"}, "access": "manage"}]
    return {"data": {"id": str(study_id), "type": "studies", "attributes": {
        "title": title, "policy": {"access": "visible", "permissions": permissions}}}}


def alpha_world() -> World:
    """Two investigations. Alpha (7, project 3) has its bucket (study 20) with three assays and an existing paper
    (study 21) holding a clone of assay 101; Beta (8, project 4) has its own bucket. Lineage in assay 101:
    3 -> 2 -> 1 (child to parent); in assay 102: 4 -> 1. Sample 5 is in no assay; sample 6 is Beta's."""
    w = World(
        investigations={7: "Alpha Investigation", 8: "Beta Investigation"},
        investigation_projects={7: {3}, 8: {4}},
        studies=[StudyRow(20, 7, "Alpha Unpublished", None), StudyRow(21, 7, "Alpha Paper Existing", "An old paper"),
                 StudyRow(30, 8, "Beta Unpublished", None)],
        assays={101: AssayRow(101, 20, "RNA-seq run"), 102: AssayRow(102, 20, "Imaging run"),
                103: AssayRow(103, 20, "Unmapped run"), 201: AssayRow(201, 21, "RNA-seq run"),
                301: AssayRow(301, 30, "Beta run")},
        links=[(101, 1, 1), (101, 2, 2), (101, 3, 2), (102, 1, 1), (102, 4, 2), (301, 6, 1)],
        samples={1: {"uuid": uid(1), "meta": {"UID": uid(1)}},
                 2: {"uuid": uid(2, kind="D.SEQ"), "meta": {"UID": uid(2, kind="D.SEQ"), "Parent": uid(1)}},
                 3: {"uuid": uid(3, kind="D.SEQ"), "meta": {"UID": uid(3, kind="D.SEQ"),
                                                             "Parent": uid(2, kind="D.SEQ")}},
                 4: {"uuid": uid(4, kind="IMG"), "meta": {"UID": uid(4, kind="IMG"), "Parent": uid(1)}},
                 5: {"uuid": uid(5), "meta": {"UID": uid(5)}},
                 6: {"uuid": uid(6, "BBB"), "meta": {"UID": uid(6, "BBB")}}},
        mapping={101: [900], 102: [901], 103: [], 201: [900], 301: [902]},
        internal_titles={900: "RNA-seq", 901: "Imaging", 902: "Beta"},
        sample_projects={1: {3}, 2: {3}, 3: {3}, 4: {3}, 5: {3}, 6: {4}},
        sops={7: "P.SOP-1"},
    )
    w.assay_reps = {a: assay_rep(a, row.title, study_id=row.study_id) for a, row in w.assays.items()}
    w.study_reps = {20: study_rep(20, "Alpha Unpublished"), 21: study_rep(21, "Alpha Paper Existing"),
                    30: study_rep(30, "Beta Unpublished")}
    return w


@pytest.fixture
def alpha() -> World:
    return alpha_world()


def add_sample(world: World, sid: int, *, parents=(), assays=((101, 2),), kind="D.SEQ", projects=(3,)) -> str:
    """A synthetic sample of ``kind`` whose Parent names the samples ``parents`` (by their UIDs), linked to each
    ``(assay, direction)`` of ``assays``. Returns its UID."""
    code = uid(sid, kind=kind)
    meta = {"UID": code}
    if parents:
        meta["Parent"] = "; ".join(world.samples[p]["uuid"] for p in parents)
    world.samples[sid] = {"uuid": code, "meta": meta}
    world.sample_projects[sid] = set(projects)
    for assay, direction in assays:
        world.links.append((assay, sid, direction))
    return code


def apply_to_world(world: World, plan) -> dict:
    """What a complete apply would leave in SEEK, done to the World: new studies from ``next_study_id``, clones at
    their placeholder ids with their source's mapping, the units' inserts and removals."""
    next_id = world.next_study_id
    ids = {}
    for t in plan.targets:
        study_id = t.study.seek_study_id
        if t.study.action == "create":
            study_id = next_id
            next_id += 1
            world.studies.append(StudyRow(study_id, t.investigation_id, t.title, t.description))
        for c in t.clones:
            new = c.seek_assay_id if c.action == "reuse" else c.placeholder_id
            if c.action == "create":
                world.assays[new] = AssayRow(new, study_id, c.title)
                world.mapping[new] = list(c.internal_assay_ids)
                world.assay_reps[new] = assay_rep(new, c.title, study_id=study_id)
            ids[(t.key, c.source_assay_id)] = new
    for unit in plan.units:
        for x in unit.inserts:
            assay = ids[(x.target_key, x.source_assay_id)]
            if not any(a == assay and s == x.sample_id for a, s, _d in world.links):
                world.links.append((assay, x.sample_id, x.direction))
        gone = {(r.assay_id, r.sample_id) for r in unit.removals}
        world.links = [link for link in world.links if (link[0], link[1]) not in gone]
    world.next_study_id = next_id
    return ids
