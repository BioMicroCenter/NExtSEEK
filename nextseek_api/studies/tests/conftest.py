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
    projects: set = field(default_factory=set)                  # SEEK's project ids

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

    def project_ids_present(self, ids):
        return {int(i) for i in ids if int(i) in self.w.projects}

    def project_investigations(self, project_id):
        return {inv for inv, projects in self.w.investigation_projects.items() if project_id in projects}

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
        projects={3, 4},
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


# --- SEEK's link tables on SQLite (tasks 17 onward) ----------------------------------------------

from contextlib import contextmanager  # noqa: E402

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@pytest.fixture
def seek_db(monkeypatch):
    """assay_assets and samples on one SQLite connection, dmac's graph_sync_outbox in an attached ``dmac`` schema.
    The registration planner's schema-qualified reads name ``main``."""
    from nextseek_api.assay_registration import planner as registration_planner

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, _record):
        dbapi_conn.isolation_level = None
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS dmac")

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE assay_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, assay_id INTEGER, "
                             "asset_id INTEGER, version INTEGER, created_at TEXT, updated_at TEXT, "
                             "relationship_type_id INTEGER, asset_type TEXT, direction INTEGER)")
        conn.exec_driver_sql("CREATE TABLE samples (id INTEGER PRIMARY KEY, uuid TEXT, json_metadata TEXT)")
        conn.exec_driver_sql("CREATE TABLE dmac.graph_sync_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, "
                             "key TEXT, payload TEXT, enqueued_at TEXT, attempts INTEGER, UNIQUE (kind, key))")
    monkeypatch.setattr(registration_planner, "_seek_db", lambda: "main")
    return engine


@contextmanager
def sqlite_connection(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
            trans.commit()
        except BaseException:
            trans.rollback()
            raise


def seed(engine, world: World) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM assay_assets")
        conn.exec_driver_sql("DELETE FROM samples")
        for assay, sample, direction in world.links:
            conn.execute(text("INSERT INTO assay_assets (assay_id, asset_id, version, created_at, updated_at, "
                              "relationship_type_id, asset_type, direction) VALUES (:a, :s, 1, "
                              "'2026-01-01 00:00:00', '2026-01-01 00:00:00', NULL, 'Sample', :d)"),
                         {"a": assay, "s": sample, "d": direction})
        for sid, sample in world.samples.items():
            meta = sample.get("meta", {})
            conn.execute(text("INSERT INTO samples (id, uuid, json_metadata) VALUES (:i, :u, :m)"),
                         {"i": sid, "u": sample["uuid"], "m": meta if isinstance(meta, str) else json.dumps(meta)})


def links_of(engine) -> list:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text("SELECT assay_id, asset_id, direction FROM assay_assets "
                                                    "ORDER BY id")).fetchall()]


def rows_of(engine) -> list:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text("SELECT id, assay_id, asset_id FROM assay_assets "
                                                    "ORDER BY id")).fetchall()]


def outbox_of(engine) -> list:
    with engine.connect() as conn:
        return [(k, key, json.loads(p)) for k, key, p in conn.execute(text(
            "SELECT kind, key, payload FROM dmac.graph_sync_outbox ORDER BY id")).fetchall()]


class FakeSeekMetadata:
    """samples.json_metadata as the backfill's cursor and graph_sync's ``samples_by_ids`` both read it."""

    def __init__(self, world: World):
        self.metadata = {sid: (s["meta"] if isinstance(s["meta"], str) else json.dumps(s["meta"]))
                         for sid, s in world.samples.items()}
        self.updated: list = []
        self._result: list = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        params = list(params or [])
        if sql.lstrip().startswith("SELECT"):
            self._result = [(sid, self.metadata[sid]) for sid in params if sid in self.metadata]
        else:
            new, sample_id = params
            self.metadata[sample_id] = new
            self.updated.append(sample_id)

    def fetchall(self):
        return self._result

    def samples_by_ids(self, ids):
        return [{"id": i, "uuid": "", "title": "", "sample_type_id": 1, "json_metadata": self.metadata[i]}
                for i in sorted(ids) if i in self.metadata]

    def meta(self, sample_id) -> dict:
        return json.loads(self.metadata[sample_id])


# --- apply and rollback (tasks 18 onward) --------------------------------------------------------

from types import SimpleNamespace as _NS  # noqa: E402

from nextseek_api.studies.buckets import title_key  # noqa: E402
from nextseek_api.studies.seek import SeekUnknownOutcome  # noqa: E402


class FakeSession:
    """The SEEK session apply and rollback use. ``script[kind]`` ("study", "assay") says what each POST does in turn:
    "ok"; "lost" (raises, creates nothing); "late:N" (creates, raises, and the object is hidden from the next N
    lookups); or an exception to raise."""

    def __init__(self, *, next_study=100, next_assay=302, engine=None):
        self.login, self.person_id = "operator", 42
        self.studies: dict = {}
        self.assays: dict = {}
        self.posts: list = []
        self.deleted: list = []
        self.script = {"study": [], "assay": []}
        self.hidden: dict = {}
        self.refuse_delete: set = set()
        self.engine = engine
        self._next = {"study": next_study, "assay": next_assay}

    def _post(self, kind, parent, title):
        how = self.script[kind].pop(0) if self.script[kind] else "ok"
        self.posts.append((kind, title))
        if isinstance(how, Exception):
            raise how
        if how == "lost":
            raise SeekUnknownOutcome("timeout")
        new = self._next[kind]
        self._next[kind] += 1
        (self.studies if kind == "study" else self.assays)[new] = (parent, title)
        if how.startswith("late"):
            self.hidden[(kind, new)] = int(how.split(":")[1]) if ":" in how else 0
            raise SeekUnknownOutcome("timeout")
        return new

    def create_study(self, payload):
        data = payload["data"]
        return self._post("study", int(data["relationships"]["investigation"]["data"]["id"]),
                          data["attributes"]["title"])

    def create_assay(self, payload):
        data = payload["data"]
        return self._post("assay", int(data["relationships"]["study"]["data"]["id"]), data["attributes"]["title"])

    def _shown(self, kind, i):
        left = self.hidden.get((kind, i), 0)
        if left > 0:
            self.hidden[(kind, i)] = left - 1
            return False
        return True

    def find_study(self, investigation_id, title):
        return [i for i, (parent, t) in sorted(self.studies.items())
                if parent == investigation_id and title_key(t) == title_key(title) and self._shown("study", i)]

    def find_assay(self, study_id, title):
        return [i for i, (parent, t) in sorted(self.assays.items())
                if parent == study_id and title_key(t) == title_key(title) and self._shown("assay", i)]

    def delete_study(self, study_id):
        if study_id in self.refuse_delete:
            return False, 422
        self.studies.pop(study_id, None)
        self.deleted.append(("study", study_id))
        return True, 204

    def delete_assay(self, assay_id):
        if assay_id in self.refuse_delete:
            return False, 422
        self.assays.pop(assay_id, None)
        self.deleted.append(("assay", assay_id))
        return True, 204

    def study_assay_count(self, study_id):
        return sum(1 for parent, _t in self.assays.values() if parent == study_id)

    def assay_link_count(self, assay_id):
        if self.engine is None:
            return 0
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM assay_assets WHERE assay_id = :a"),
                                {"a": assay_id}).scalar()


@pytest.fixture
def apply_env(tmp_path, alpha, seek_db, monkeypatch):
    """The alpha world ready to apply: SEEK's links on SQLite, sample metadata in one fake cursor, the session faked,
    the studies release's checks passed, the run lock free, the adoption clock fast. ``make(*targets)`` plans a run
    in ``tmp_path / name``; ``apply(run_dir)`` applies it."""
    from nextseek_api.graph_sync import sources
    from nextseek_api.management.commands import backfill_publication_attributes as backfill
    from nextseek_api.studies import apply as apply_mod
    from nextseek_api.studies import planner, preflight, report
    from nextseek_api.studies.models import AssociationSet, StudyTarget

    seed(seek_db, alpha)
    meta = FakeSeekMetadata(alpha)
    monkeypatch.setattr(backfill, "_cursor", meta.cursor)
    monkeypatch.setattr(sources, "samples_by_ids", meta.samples_by_ids)
    monkeypatch.setattr(apply_mod, "_connection", lambda: sqlite_connection(seek_db))

    def outbox_exists(key):
        return any(k == key for _kind, k, _p in outbox_of(seek_db))

    monkeypatch.setattr(apply_mod, "_unit_outbox_exists", outbox_exists)
    monkeypatch.setattr(apply_mod, "_sleep", lambda seconds: None)
    ticks = iter(range(0, 10**6, 10))
    monkeypatch.setattr(apply_mod, "_clock", lambda: next(ticks))
    monkeypatch.setattr(preflight, "_switch_follows", lambda: True)
    monkeypatch.setattr(preflight, "_acting_merge_ids", lambda driver, db: [])

    @contextmanager
    def free_lock():
        yield True

    monkeypatch.setattr(preflight, "run_lock", free_lock)
    session = FakeSession(next_study=alpha.next_study_id, next_assay=302, engine=seek_db)

    def make(*targets, name="run-1"):
        targets = targets or (StudyTarget(key="sheet:7:paper one", investigation_id=7, title="Paper One",
                                          description="About paper one", doi="10.0000/one", pmid="1111",
                                          sample_ids=[3]),)
        aset = AssociationSet(source="replay", source_ref="t", created_at="t", targets=list(targets))
        plan = planner.plan_study_moves(aset, FakeReader(alpha), run_id=name, now="t")
        report.write_plan_files(tmp_path / name, plan, aset)
        return tmp_path / name, plan

    def apply(run_dir, **kwargs):
        return apply_mod.apply_study_moves(run_dir, session, None, "neo4j", reader=FakeReader(alpha), **kwargs)

    return _NS(world=alpha, engine=seek_db, meta=meta, session=session, make=make, apply=apply, tmp=tmp_path)


def journal_events(run_dir) -> list:
    from nextseek_api.studies.journal import JOURNAL_FILE, read_journal

    return [(line["step"], line["event"]) for line in read_journal(run_dir / JOURNAL_FILE)[0]]


def truncate_journal_after(run_dir, step, event) -> None:
    """Keep the journal up to and including the first line of ``step``/``event``: a crash right after it."""
    from nextseek_api.studies.journal import JOURNAL_FILE

    path = run_dir / JOURNAL_FILE
    kept = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        kept.append(raw)
        line = json.loads(raw)
        if (line["step"], line["event"]) == (step, event):
            break
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
