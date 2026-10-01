"""The studies release on a real Neo4j: the study merge (plan, apply, rerun, undo, reapply), IN_STUDY following SEEK
and gate G's family 12, with the study-link switch off and on.

Runs only under lane.sh (a private, throwaway Neo4j); elsewhere every test here skips. The lane has no MySQL, so SEEK
is faked: the ``graph_sync.sources`` readers the code under test calls answer from this module's SEEK rows. No
graph-write lock is taken and no run is recorded: these tests call ``study_merge`` and ``study_links`` below the
command, which is what does both. Each test wipes the lane's database and loads this module's fixture; the folder's
own fixture is loaded again when the module ends. Every id and title is synthetic; only the shapes come from the boxes:
split pairs, an empty pair, a pair under another Investigation node, SEEK papers keyed on id (with an empty seek-keyed
node, with none, split, and with a title ending in a no-break space), a collision, graph-only papers, an orphan's link;
and the by-id path's shapes: a study in an investigation (and a project) the graph lacks, a paper sample shared into
another investigation's study.
"""
from __future__ import annotations

import json
import os

import pytest

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

DB = "neo4j"
NBSP = " "

INVESTIGATIONS = [(101, "Alder Investigation"), (102, "Birch Investigation"), (103, "Cedar Investigation")]
LEGACY_INVESTIGATION = (901, "Alder Investigation")         # a node graph_sync never wrote

# Study nodes: (key, properties, investigation id). The key names the node in LINKS.
STUDIES = [
    ("L1", {"id": 1, "title": "Alder Unpublished", "DOI": "", "PMID": ""}, 101),
    ("K1", {"seek_study_id": 1, "title": "Alder Unpublished"}, 101),
    ("L2", {"id": 2, "title": "Birch Unpublished", "DOI": ""}, 102),
    ("K2", {"seek_study_id": 2, "title": "Birch Unpublished"}, 102),
    ("L3", {"id": 3, "title": "Cedar Unpublished", "DOI": ""}, 901),
    ("K3", {"seek_study_id": 3, "title": "Cedar Unpublished"}, 101),
    ("L4", {"id": 4, "title": "Dogwood paper", "description": "A dogwood paper", "DOI": "10.9999/d4", "PMID": "4"},
     102),
    ("K4", {"seek_study_id": 4, "title": "Dogwood paper", "description": "A dogwood paper"}, 102),
    ("L5", {"id": 5, "title": "Elm paper", "DOI": "10.9999/e5"}, 102),
    ("L6", {"id": 6, "title": "Fir paper", "DOI": "10.9999/f6"}, 102),
    ("K6", {"seek_study_id": 6, "title": "Fir paper"}, 102),
    ("L7", {"id": 7, "title": "Gum paper", "DOI": "10.9999/g7"}, 102),
    ("K7", {"seek_study_id": 7, "title": "Gum paper" + NBSP}, 102),
    ("P8", {"id": 8, "title": "An unrelated paper", "DOI": "10.9999/p8"}, 103),
    ("K8", {"seek_study_id": 8, "title": "Hazel Study"}, 103),
    ("P9", {"id": 9, "title": "A graph-only paper", "DOI": "10.9999/p9"}, 101),
    ("P10", {"id": 10, "title": "Another graph-only paper", "DOI": "10.9999/p10"}, 103),
    ("K12", {"seek_study_id": 12, "title": "Larch Study"}, 103),
    ("L14", {"id": 14, "title": "Nutmeg Unpublished", "DOI": ""}, 103),
]
SAMPLES = [1001, 1002, 1003, 1004, 1005, 1006, 1011, 1012, 1021, 1031, 1032, 1041, 1051, 1061, 1062, 1063]
ORPHANS = [1033]
LINKS = [(1001, "L1"), (1002, "L1"), (1004, "L1"), (1003, "K1"), (1004, "K1"), (1005, "K1"), (1011, "L3"),
         (1012, "K3"), (1021, "L5"), (1031, "L6"), (1032, "K6"), (1033, "K6"), (1041, "K8"), (1051, "P9"),
         (1061, "K12"), (1062, "K12"), (1063, "K12")]

SEEK_STUDIES = [
    {"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101},
    {"id": 2, "title": "Birch Unpublished", "description": None, "investigation_id": 102},
    {"id": 3, "title": "Cedar Unpublished", "description": None, "investigation_id": 101},
    {"id": 4, "title": "Dogwood paper", "description": "A dogwood paper", "investigation_id": 102},
    {"id": 5, "title": "Elm paper", "description": None, "investigation_id": 102},
    {"id": 6, "title": "Fir paper", "description": None, "investigation_id": 102},
    {"id": 7, "title": "Gum paper" + NBSP, "description": None, "investigation_id": 102},
    {"id": 8, "title": "Hazel Study", "description": None, "investigation_id": 103},
    {"id": 10, "title": "Juniper Study", "description": None, "investigation_id": 103},
    {"id": 12, "title": "Larch Study", "description": None, "investigation_id": 103},
    {"id": 13, "title": "Maple Study", "description": None, "investigation_id": 103},
    {"id": 14, "title": "Nutmeg", "description": None, "investigation_id": 103},
]
SEEK_LINKS = [(1001, 1), (1003, 1), (1004, 1), (1005, 12), (1011, 3), (1012, 3), (1021, 5), (1031, 6), (1032, 6),
              (1041, 8), (1051, 1), (1061, 12), (1062, 13)]
EXPECTED_KINDS = {1: "merge", 2: "merge", 3: "merge_other_investigation", 4: "rekey_in_place", 5: "rekey_in_place",
                  6: "merge", 7: "rekey_in_place", 8: "id_collision", 9: "not_in_seek", 10: "paper",
                  12: "seek_only", 14: "legacy_only"}
APPROVED = {x: k for x, k in EXPECTED_KINDS.items() if k in ("merge", "merge_other_investigation", "rekey_in_place")}


@pytest.fixture(scope="module")
def studies_lane(lane):
    yield lane
    lane.reload()


@pytest.fixture
def graph(studies_lane, monkeypatch):
    """This module's graph in the lane's Neo4j, and SEEK's side faked in ``sources``."""
    from nextseek_graph import schema
    from nextseek_api.graph_sync import sources

    def load(tx):
        tx.run("MATCH (n) DETACH DELETE n").consume()
        tx.run("CREATE (:GraphMeta {schema_version: $v, catalog_hash: 'lane'})", v=schema.SCHEMA_VERSION).consume()
        for inv_id, title in INVESTIGATIONS + [LEGACY_INVESTIGATION]:
            tx.run("CREATE (:Investigation {id: $id, title: $title, lane_key: $key})", id=inv_id, title=title,
                   key=f"I{inv_id}").consume()
        for key, props, inv_id in STUDIES:
            tx.run("CREATE (st:Study) SET st = $props, st.lane_key = $key WITH st "
                   "MATCH (i:Investigation {lane_key: $inv}) CREATE (st)-[:IN_INVESTIGATION]->(i)",
                   props=props, key=key, inv=f"I{inv_id}").consume()
        for sid in SAMPLES:
            tx.run("CREATE (:Sample {id: $id, uuid: $uuid})", id=sid, uuid=f"TIS-000000LNE-{sid}").consume()
        for sid in ORPHANS:
            tx.run("CREATE (:OrphanSample {id: $id, uuid: $uuid})", id=sid, uuid=f"TIS-000000LNE-{sid}").consume()
        for sid, key in LINKS:
            tx.run("MATCH (x {id: $id}) WHERE x:Sample OR x:OrphanSample MATCH (st:Study {lane_key: $key}) "
                   "CREATE (x)-[:IN_STUDY]->(st)", id=sid, key=key).consume()
        tx.run("MATCH (n) WHERE n.lane_key IS NOT NULL REMOVE n.lane_key").consume()

    with studies_lane.driver.session() as session:
        session.execute_write(load)
    monkeypatch.setattr(sources, "studies", lambda: [dict(s) for s in SEEK_STUDIES])
    monkeypatch.setattr(sources, "investigations",
                        lambda: [{"id": i, "title": t, "description": None} for i, t in INVESTIGATIONS])
    monkeypatch.setattr(sources, "iter_seek_study_links", lambda: iter(sorted(SEEK_LINKS)))
    monkeypatch.setattr(sources, "investigation_projects", lambda: [])
    monkeypatch.setattr(sources, "projects", lambda: [])
    monkeypatch.setattr(sources, "seek_study_links_for", lambda ids: [
        {"sample_id": s, "study_id": t, "study_title": None, "study_description": None, "investigation_id": None}
        for s, t in sorted(SEEK_LINKS) if s in set(ids)])
    return studies_lane


def _snapshot(lane):
    """The Study layer by content: each Study's properties and Investigation ids, each IN_STUDY as (source labels,
    source id, target properties). Element ids aside, so a re-created node compares equal."""
    studies = lane.read("MATCH (st:Study) OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(i:Investigation) "
                        "RETURN properties(st) AS props, collect(i.id) AS invs")
    links = lane.read("MATCH (x)-[:IN_STUDY]->(st:Study) RETURN labels(x) AS labels, x.id AS source, "
                      "properties(st) AS props")
    return (sorted(json.dumps([r["props"], sorted(r["invs"])], sort_keys=True) for r in studies),
            sorted(json.dumps([sorted(r["labels"]), r["source"], r["props"]], sort_keys=True) for r in links))


def _keys(lane, sample_id):
    rows = lane.read("MATCH (s:Sample {id: $id})-[:IN_STUDY]->(st:Study) RETURN st.id AS id, "
                     "st.seek_study_id AS seek", {"id": sample_id})
    return sorted(("seek", r["seek"]) if r["seek"] is not None else ("id", r["id"]) for r in rows)


def _family(lane, monkeypatch, follow):
    from nextseek_api.graph_sync import study_links, verify
    if follow:
        monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    else:
        monkeypatch.delenv(study_links.SWITCH_ENV, raising=False)
    checks = []
    verify._check_studies(lane.driver, DB, checks, {})
    return {c["name"]: c for c in checks}


def test_the_plan_reads_every_kind(graph):
    from nextseek_api.graph_sync import study_merge
    report = study_merge.plan(graph.driver, DB)
    assert report["kinds"] == EXPECTED_KINDS
    assert report["approval_line"] == ("1:merge,2:merge,3:merge_other_investigation,4:rekey_in_place,"
                                       "5:rekey_in_place,6:merge,7:rekey_in_place")
    assert study_merge.parse_approval(report["approval_line"]) == APPROVED
    assert report["merge_other_investigation"] == [3]
    assert report["id_collisions"] == [8] and report["legacy_only"] == [14]


def test_merge_studies_undo_and_reapply(graph, monkeypatch, tmp_path):
    from nextseek_api.graph_sync import study_links, study_merge
    initial = _snapshot(graph)
    family = _family(graph, monkeypatch, follow=False)
    assert all(c["pass"] for c in family.values())
    assert family["12.studies.split_pairs"]["actual"] == 3

    first = study_merge.apply(graph.driver, DB, APPROVED, run_dir=str(tmp_path / "m1"))
    assert first["status"] == "ok" and len(first["merged"]) == 7
    again = study_merge.apply(graph.driver, DB, APPROVED, run_dir=str(tmp_path / "m1b"))
    assert again["already_merged"] == sorted(APPROVED) and again["merged"] == []
    props = {r["id"]: r["props"] for r in graph.read("MATCH (st:Study) WHERE st.id IS NOT NULL "
                                                     "RETURN st.id AS id, properties(st) AS props")}
    assert props[1] == {"id": 1, "title": "Alder Unpublished", "seek_study_id": 1}
    assert props[4]["DOI"] == "10.9999/d4" and props[4]["seek_study_id"] == 4
    assert graph.read("MATCH (st:Study {id: 3})-[:IN_INVESTIGATION]->(i) RETURN i.id AS id") == [{"id": 101}]
    assert graph.read("MATCH (:OrphanSample {id: 1033})-[:IN_STUDY]->(st) RETURN st.id AS id") == [{"id": 6}]
    assert _keys(graph, 1004) == [("seek", 1)]

    rebuilt = study_links.rebuild_in_study(graph.driver, DB, remove=True, run_dir=str(tmp_path / "s1"),
                                           path="studies")
    assert rebuilt["status"] == "ok"
    assert (rebuilt["seek_studies"], rebuilt["seek_study_investigation_missing"]) == (len(SEEK_STUDIES), 0)
    assert (rebuilt["in_study_added"], rebuilt["in_study_removed"]) == (2, 2)
    assert (rebuilt["paper_samples"], rebuilt["withheld"], rebuilt["kept_no_seek_study"]) == (1, 1, 2)
    assert _keys(graph, 1005) == [("seek", 12)] and _keys(graph, 1062) == [("seek", 13)]
    assert _keys(graph, 1002) == [("seek", 1)] and _keys(graph, 1051) == [("id", 9)]
    # Every SEEK study now has a node: 14's legacy node reads merge, and 10's paper node collides with SEEK study 10.
    plan = study_merge.plan(graph.driver, DB)
    assert (plan["kinds"][14], plan["kinds"][10]) == ("merge", "id_collision")
    family = _family(graph, monkeypatch, follow=True)
    assert [n for n, c in family.items() if not c["pass"]] == ["12.studies.merge_candidates"]
    later = study_merge.apply(graph.driver, DB, {14: "merge"}, run_dir=str(tmp_path / "m2"))
    assert later["merged"] == [{"study_id": 14, "kind": "merge"}]
    study_links.rebuild_in_study(graph.driver, DB, remove=True, run_dir=str(tmp_path / "s2"), path="studies")
    merged_state = _snapshot(graph)
    family = _family(graph, monkeypatch, follow=True)
    assert [n for n, c in family.items() if not c["pass"]] == []
    assert family["12.studies.id_collisions"]["actual"] == 2 and family["12.studies.orphan_in_study"]["actual"] == 1

    undone = study_merge.undo(graph.driver, DB, [str(tmp_path / "m1"), str(tmp_path / "m2"), str(tmp_path / "s1")])
    assert undone["status"] == "ok" and undone["archive_restored"] == 2
    studies, links = _snapshot(graph)
    added_studies = [json.dumps([{"seek_study_id": sid, "title": title}, [103]], sort_keys=True)
                     for sid, title in ((10, "Juniper Study"), (13, "Maple Study"), (14, "Nutmeg"))]
    added_links = [json.dumps([["Sample"], 1005, {"seek_study_id": 12, "title": "Larch Study"}], sort_keys=True),
                   json.dumps([["Sample"], 1062, {"seek_study_id": 13, "title": "Maple Study"}], sort_keys=True)]
    assert studies == sorted(initial[0] + added_studies)
    assert links == sorted(initial[1] + added_links)

    study_merge.apply(graph.driver, DB, {**APPROVED, 14: "merge"}, run_dir=str(tmp_path / "m3"))
    study_links.rebuild_in_study(graph.driver, DB, remove=True, run_dir=str(tmp_path / "s3"), path="studies")
    assert _snapshot(graph) == merged_state


def test_a_crash_between_batches_is_finished_by_a_rerun(graph, monkeypatch, tmp_path):
    from nextseek_api.graph_sync import cypher as q
    from nextseek_api.graph_sync import study_merge
    real_run, moves = study_merge._run, []

    def flaky(driver, db, query, params=None, **kwargs):
        if query == q.MOVE_IN_STUDY:
            moves.append(1)
            if len(moves) == 2:
                raise RuntimeError("the connection to Neo4j was lost")
        return real_run(driver, db, query, params, **kwargs)

    monkeypatch.setattr(study_merge, "_run", flaky)
    with pytest.raises(RuntimeError):
        study_merge.apply(graph.driver, DB, {1: "merge"}, run_dir=str(tmp_path), batch=1)
    assert study_merge.classify(study_merge.read_index(graph.driver, DB), 1).kind == "merge"
    result = study_merge.apply(graph.driver, DB, {1: "merge"}, run_dir=str(tmp_path), batch=1)
    assert result["status"] == "ok"
    assert _keys(graph, 1003) == [("seek", 1)] and _keys(graph, 1005) == [("seek", 1)]
    records = [line.split("\t")[1] for line in
               (tmp_path / study_merge.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()[1:]]
    assert records.count("plan") == 2 and records[-1] == "done"


def test_the_seek_keyed_nodes_follow_seek_after_the_rebuild(graph, monkeypatch, tmp_path):
    from nextseek_api.graph_sync import study_links, study_merge
    study_merge.apply(graph.driver, DB, APPROVED, run_dir=str(tmp_path / "m1"))
    study_links.rebuild_in_study(graph.driver, DB, remove=True, run_dir=str(tmp_path / "s1"))
    rows = {r["seek"]: r for r in graph.read(
        "MATCH (st:Study) WHERE st.seek_study_id IS NOT NULL OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(i) "
        "RETURN st.seek_study_id AS seek, st.title AS title, st.description AS description, collect(i.id) AS invs")}
    for seek in SEEK_STUDIES:
        if seek["id"] in rows:
            assert rows[seek["id"]]["title"] == seek["title"]
            assert rows[seek["id"]]["description"] == seek["description"]
            assert rows[seek["id"]]["invs"] == [seek["investigation_id"]]
    assert rows[7]["title"].endswith(NBSP)


# --- the by-id path on a real Neo4j ---------------------------------------------------------------------------------

def _load(lane, statements):
    def load(tx):
        tx.run("MATCH (n) DETACH DELETE n").consume()
        for statement in statements:
            tx.run(statement).consume()

    with lane.driver.session() as session:
        session.execute_write(load)


def test_one_by_id_sync_writes_a_new_investigation_and_project_before_the_study(studies_lane):
    """A study SEEK made in a new investigation of a new project: after one by-id write the chain Sample, IN_STUDY,
    Study, IN_INVESTIGATION, Investigation, IN_PROJECT, Project exists."""
    from nextseek_api.graph_sync import writer
    _load(studies_lane, ["CREATE (:Sample {id: 1001, uuid: 'TIS-000000LNE-1001'})"])
    tables = writer.SeekTables(
        studies=({"id": 20, "title": "Poplar Study", "description": "About poplar", "investigation_id": 120},),
        investigations=({"id": 120, "title": "Poplar Investigation", "description": None},),
        investigation_projects=({"investigation_id": 120, "project_id": 15},),
        projects=({"id": 15, "title": "Poplar"},))
    link = {"sample_id": 1001, "study_id": 20, "study_title": "Poplar Study", "study_description": "About poplar",
            "investigation_id": 120}
    counts = writer.write_seek_studies(studies_lane.driver, DB, [link], [1001], remove=True, archive_path=None,
                                       tables=tables)
    chain = studies_lane.read(
        "MATCH (:Sample {id: 1001})-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(i:Investigation)"
        "-[:IN_PROJECT]->(p:Project) RETURN st.seek_study_id AS study, st.description AS description, "
        "i.id AS investigation, i.title AS title, i.project_id AS project_id, p.id AS project, "
        "p.title AS project_title")
    assert chain == [{"study": 20, "description": "About poplar", "investigation": 120,
                      "title": "Poplar Investigation", "project_id": 15, "project": 15, "project_title": "Poplar"}]
    assert (counts["seek_study_investigation_missing"], counts["investigation_projects_written"]) == (0, 1)
    # gate G's family 14 reads the chain the sync wrote as SEEK's
    from nextseek_api.graph_sync import sources, verify
    import pytest as _pytest
    patch = _pytest.MonkeyPatch()
    try:
        patch.setattr(sources, "projects", lambda: list(tables.projects))
        patch.setattr(sources, "investigations", lambda: list(tables.investigations))
        patch.setattr(sources, "investigation_projects", lambda: list(tables.investigation_projects))
        patch.setattr(sources, "memberships", lambda: [])
        checks = []
        verify._check_small_tables(studies_lane.driver, DB, checks, {})
    finally:
        patch.undo()
    assert [c["name"] for c in checks if not c["pass"]] == []


def test_a_paper_sample_shared_into_another_investigations_study_is_linked_there_only(studies_lane, monkeypatch):
    from nextseek_api.graph_sync import sources, writer
    _load(studies_lane, [
        "CREATE (a:Investigation {id: 101, title: 'Alder Investigation'}), "
        "(b:Investigation {id: 102, title: 'Birch Investigation'}), "
        "(:Study {seek_study_id: 1, title: 'Alder Unpublished'})-[:IN_INVESTIGATION]->(a), "
        "(:Study {seek_study_id: 3, title: 'Birch Study'})-[:IN_INVESTIGATION]->(b), "
        "(p:Study {id: 9, title: 'A paper', DOI: '10.9999/p9'})-[:IN_INVESTIGATION]->(a), "
        "(:Sample {id: 1003, uuid: 'TIS-000000LNE-1003'})-[:IN_STUDY]->(p)",
        "CREATE (:GraphMeta {schema_version: '1.2', catalog_hash: 'lane'})"])
    seek_studies = [{"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101},
                    {"id": 3, "title": "Birch Study", "description": None, "investigation_id": 102}]
    seek_investigations = [{"id": 101, "title": "Alder Investigation", "description": None},
                           {"id": 102, "title": "Birch Investigation", "description": None}]
    tables = writer.SeekTables(studies=tuple(seek_studies), investigations=tuple(seek_investigations))
    links = [{"sample_id": 1003, "study_id": 1, "study_title": "Alder Unpublished", "study_description": None,
              "investigation_id": 101},
             {"sample_id": 1003, "study_id": 3, "study_title": "Birch Study", "study_description": None,
              "investigation_id": 102}]
    counts = writer.write_seek_studies(studies_lane.driver, DB, links, [1003], remove=True, archive_path=None,
                                       tables=tables)
    assert _keys(studies_lane, 1003) == [("id", 9), ("seek", 3)]
    assert (counts["in_study_paper_links_written"], counts["in_study_withheld"]) == (1, 1)
    monkeypatch.setattr(sources, "studies", lambda: [dict(s) for s in seek_studies])
    monkeypatch.setattr(sources, "investigations", lambda: [dict(i) for i in seek_investigations])
    monkeypatch.setattr(sources, "iter_seek_study_links", lambda: iter([(1003, 1), (1003, 3)]))
    family = _family(studies_lane, monkeypatch, follow=True)
    assert [n for n, c in family.items() if not c["pass"]] == []
    assert family["12.studies.paper_samples"]["detail"]["withheld_links"] == 1


def test_an_attribute_at_zero_is_counted_from_its_types_samples(studies_lane):
    """The by-id sync's attribute count (targeted._attribute_counts): only attributes at 0 are read, and a count is
    the number of the type's samples carrying the title."""
    from nextseek_api.graph_sync import cypher as q
    _load(studies_lane, [
        "CREATE (t:SampleType {id: 26, title: 'TIS', label: 'T_TIS'}), "
        "(t)-[:HAS_ATTRIBUTE]->(:Attribute {key: '26:Organ', title: 'Organ', sample_count: 0}), "
        "(t)-[:HAS_ATTRIBUTE]->(:Attribute {key: '26:Name', title: 'Name', sample_count: 4}), "
        "(:Sample {id: 1, Organ: 'Lung'})-[:OF_TYPE]->(t), (:Sample {id: 2, Organ: 'Liver'})-[:OF_TYPE]->(t), "
        "(:Sample {id: 3})-[:OF_TYPE]->(t)"])
    zero = studies_lane.read(q.ATTRIBUTES_AT_ZERO, {"type_ids": [26]})
    assert zero == [{"type_id": 26, "key": "26:Organ", "title": "Organ"}]
    with studies_lane.driver.session() as session:
        raised = session.run(q.SET_ATTRIBUTE_COUNTS_FROM_TYPE, rows=zero).single()["raised"]
    assert raised == 1
    assert studies_lane.read("MATCH (a:Attribute {key: '26:Organ'}) RETURN a.sample_count AS n") == [{"n": 2}]


def test_a_gone_empty_sample_type_and_investigation_leave_the_graph(studies_lane, tmp_path):
    """The catalog step's and the small tables' deletes: a SampleType SEEK lost that no Sample reaches (and its
    Attributes), and an Investigation SEEK lost that no Study holds; a type with a sample and an investigation a
    Study holds stay. The gone type's title is then no conflict for the type that took it."""
    from nextseek_api.graph_sync import cypher as q
    from nextseek_api.graph_sync import writer
    _load(studies_lane, [
        "CREATE (old:SampleType {id: 9, title: 'TIS', label: 'T_TIS'})"
        "-[:HAS_ATTRIBUTE]->(:Attribute {key: '9:Organ', title: 'Organ'}), "
        "(kept:SampleType {id: 8, title: 'OLD', label: 'T_OLD'}), (:Sample {id: 1})-[:OF_TYPE]->(kept), "
        "(:Investigation {id: 7, title: 'Gone'}), "
        "(held:Investigation {id: 6, title: 'Held'}), (:Study {id: 40, title: 'A paper'})-[:IN_INVESTIGATION]->(held)"])
    assert studies_lane.read(q.SAMPLE_TYPE_TITLE_CONFLICTS, {"rows": [{"id": 26, "title": "TIS"}], "ids": [26]}) == []
    counts = writer.write_sample_types(studies_lane.driver, DB,
                                       [{"id": 26, "title": "TIS", "label": "T_TIS", "deprecated": False}],
                                       archive_path=str(tmp_path / writer.SAMPLE_TYPES_DELETED_FILE))
    assert (counts["sample_types_deleted"], counts["graph_only_sample_types"]) == (1, ["OLD"])
    assert studies_lane.read("MATCH (t:SampleType) RETURN t.id AS id ORDER BY id") == [{"id": 8}, {"id": 26}]
    assert studies_lane.read("MATCH (a:Attribute) RETURN count(a) AS n") == [{"n": 0}]
    counts = writer.write_investigation_projects(studies_lane.driver, DB, [{"id": 5, "title": "New",
                                                                          "description": None}], [],
                                                 archive_path=str(tmp_path / writer.INVESTIGATIONS_DELETED_FILE))
    assert (counts["investigations_deleted"], counts["investigations_not_in_seek_held"]) == (1, 1)
    assert studies_lane.read("MATCH (i:Investigation) RETURN i.id AS id ORDER BY id") == [{"id": 5}, {"id": 6}]


# --- the merge's statements on shapes the module's fixture lacks ----------------------------------------------------

_SPLIT_PAIR = (
    "CREATE (i:Investigation {id: 101, title: 'Alder Investigation'}), "
    "(l:Study {id: 1, title: 'Alder Unpublished', DOI: '', PMID: ''})-[:IN_INVESTIGATION]->(i), "
    "(k:Study {seek_study_id: 1, title: 'Alder Unpublished'})-[:IN_INVESTIGATION]->(i), "
    "(a:Sample {id: 1001, uuid: 'TIS-000000LNE-1001'})-[:IN_STUDY]->(l), (a)-[:IN_STUDY]->(k), "
    "(b:Sample {id: 1002, uuid: 'TIS-000000LNE-1002'})-[:IN_STUDY]->(k), "
    "(:GraphMeta {schema_version: '1.2', catalog_hash: 'lane'})")


def _split_pair(lane, monkeypatch, *extra):
    """SEEK study 1 split: L (the empty marker) and K, both under Investigation 101; sample 1001 on both, 1002 only on
    K. SEEK holds study 1 under investigation 101. ``extra`` statements run after the load."""
    from nextseek_api.graph_sync import sources
    _load(lane, [_SPLIT_PAIR, *extra])
    monkeypatch.setattr(sources, "studies", lambda: [
        {"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101}])
    monkeypatch.setattr(sources, "investigations", lambda: [
        {"id": 101, "title": "Alder Investigation", "description": None}])


def test_a_source_with_two_edges_to_the_legacy_node_moves_once(studies_lane, monkeypatch, tmp_path):
    """MERGE to L matches both of 1001's parallel edges to L: the batch still counts one source, the merge finishes,
    and every link ends on the merged node."""
    from nextseek_api.graph_sync import study_merge
    _split_pair(studies_lane, monkeypatch,
                "MATCH (a:Sample {id: 1001}), (l:Study {id: 1}) CREATE (a)-[:IN_STUDY]->(l)")
    result = study_merge.apply(studies_lane.driver, DB, {1: "merge"}, run_dir=str(tmp_path))
    assert result["status"] == "ok" and result["merged"] == [{"study_id": 1, "kind": "merge"}]
    assert studies_lane.read("MATCH (st:Study) RETURN st.id AS id, st.seek_study_id AS seek") == [
        {"id": 1, "seek": 1}]
    assert _keys(studies_lane, 1001) == [("seek", 1), ("seek", 1)] and _keys(studies_lane, 1002) == [("seek", 1)]


def _element_ids(lane, label, key):
    return {r["key"]: r["element_id"] for r in lane.read(
        f"MATCH (n:{label}) RETURN elementId(n) AS element_id, coalesce(n.{key}, -1) AS key")}


def _finish(lane, **params):
    from nextseek_api.graph_sync import cypher as q
    from nextseek_api.graph_sync.writer import _one, _run
    return _one(_run(lane.driver, DB, q.FINISH_STUDY_MERGE,
                     {"study_id": 1, "new_investigation": None, **params}), "merged")


def test_the_last_step_refuses_a_second_node_carrying_the_key(studies_lane, monkeypatch):
    """FINISH with no seek-keyed node to delete, while another node already carries seek_study_id 1: it writes
    nothing, so two nodes never share the key."""
    _split_pair(studies_lane, monkeypatch, "MATCH (x)-[e:IN_STUDY]->() DELETE e")
    legacy = _element_ids(studies_lane, "Study", "id")[1]
    assert _finish(studies_lane, l=legacy, k=None) == 0
    assert studies_lane.read("MATCH (st:Study {seek_study_id: 1}) RETURN count(st) AS n") == [{"n": 1}]


def test_the_last_step_never_leaves_the_legacy_node_under_no_investigation(studies_lane, monkeypatch):
    """A merge_other_investigation whose new Investigation is not the seek-keyed node's: nothing is written, and L
    keeps its own Investigation."""
    _split_pair(studies_lane, monkeypatch, "MATCH (x)-[e:IN_STUDY]->() DELETE e",
                "MATCH (l:Study {id: 1})-[e:IN_INVESTIGATION]->() DELETE e "
                "CREATE (l)-[:IN_INVESTIGATION]->(:Investigation {id: 901, title: 'Alder Investigation'})")
    nodes = _element_ids(studies_lane, "Study", "id")
    assert _finish(studies_lane, l=nodes[1], k=nodes[-1], new_investigation="no-such-element") == 0
    assert studies_lane.read("MATCH (:Study {id: 1})-[:IN_INVESTIGATION]->(i) RETURN i.id AS id") == [{"id": 901}]


def test_the_last_step_fails_on_a_link_that_arrives_after_its_check(studies_lane, monkeypatch):
    """Another transaction links a sample to K and holds its commit while FINISH runs: FINISH reads K as empty, waits
    on K's lock, and once the link commits its delete of K must fail rather than take the link with it."""
    import threading
    _split_pair(studies_lane, monkeypatch, "MATCH (x)-[e:IN_STUDY]->() DELETE e")
    nodes = _element_ids(studies_lane, "Study", "id")
    outcome = {}

    def finish():
        try:
            outcome["merged"] = _finish(studies_lane, l=nodes[1], k=nodes[-1])
        except Exception as exc:  # noqa: BLE001 - the outcome under test is the error itself
            outcome["error"] = exc

    session = studies_lane.driver.session()
    tx = session.begin_transaction()
    worker = threading.Thread(target=finish)
    committed = False
    try:
        tx.run("MATCH (s:Sample {id: 1002}), (k:Study {seek_study_id: 1}) CREATE (s)-[:IN_STUDY]->(k)").consume()
        worker.start()
        worker.join(3)
        assert worker.is_alive(), "FINISH did not wait on the seek-keyed node's lock"
        tx.commit()
        committed = True
    finally:
        if not committed:
            tx.rollback()
        session.close()
        worker.join(60)
    assert "error" in outcome, outcome
    assert _keys(studies_lane, 1002) == [("seek", 1)]
    nodes = studies_lane.read("MATCH (st:Study) RETURN st.id AS id, st.seek_study_id AS seek")
    assert sorted((r["id"] or 0, r["seek"] or 0) for r in nodes) == [(0, 1), (1, 0)]


_OTHER_INVESTIGATION = ("MATCH (l:Study {id: 1})-[e:IN_INVESTIGATION]->() DELETE e "
                        "CREATE (l)-[:IN_INVESTIGATION]->(:Investigation {id: 901, title: 'Alder Investigation'})")


def _rewrite_journal(run_dir, old_new: dict):
    """Replace element ids in a journal: what an undo reads after Neo4j handed a freed element id to another node."""
    from nextseek_api.graph_sync import study_merge
    path = run_dir / study_merge.JOURNAL_FILE
    text = path.read_text(encoding="utf-8")
    for old, new in old_new.items():
        text = text.replace(json.dumps(old), json.dumps(new))
    path.write_text(text, encoding="utf-8")


def test_undo_matches_each_source_and_investigation_by_its_id_too(studies_lane, monkeypatch, tmp_path):
    """After a merge_other_investigation, sample 1001 (on both) is retired and the emptied legacy Investigation
    deleted; an Attribute and a new Investigation hold the element ids the journal names (rewritten here, as reuse
    would leave them). The undo links neither, names the Investigation, and reports the source as replaced."""
    from nextseek_api.graph_sync import study_merge
    _split_pair(studies_lane, monkeypatch, _OTHER_INVESTIGATION)
    old = {**_element_ids(studies_lane, "Sample", "id"), "inv": _element_ids(studies_lane, "Investigation", "id")[901]}
    result = study_merge.apply(studies_lane.driver, DB, {1: "merge_other_investigation"}, run_dir=str(tmp_path))
    assert result["status"] == "ok"
    studies_lane.write("MATCH (s:Sample {id: 1001}) DETACH DELETE s")
    studies_lane.write("MATCH (i:Investigation {id: 901}) DETACH DELETE i")
    studies_lane.write("CREATE (:Attribute {name: 'attr'}), (:Investigation {id: 41, title: 'Juniper Investigation'})")
    new = {"sample": _element_ids(studies_lane, "Attribute", "id")[-1],
           "inv": _element_ids(studies_lane, "Investigation", "id")[41]}
    _rewrite_journal(tmp_path, {old[1001]: new["sample"], old["inv"]: new["inv"]})

    undone = study_merge.undo(studies_lane.driver, DB, [str(tmp_path)])
    assert studies_lane.read("MATCH (a:Attribute)-[:IN_STUDY]->() RETURN count(a) AS n") == [{"n": 0}]
    assert studies_lane.read("MATCH (:Study {id: 1})-[:IN_INVESTIGATION]->(i) RETURN i.id AS id") == []
    assert [i["investigation"]["id"] for i in undone["investigation_not_restored"]] == [901]
    assert [s["id"] for s in undone["studies"][0]["sources_replaced"]] == [1001]
    assert _keys(studies_lane, 1002) == [("seek", 1)]


def test_undo_after_neo4j_reuses_a_retired_sources_element_id(studies_lane, monkeypatch, tmp_path):
    """The same without rewriting anything, where Neo4j can be made to reuse an element id within a bounded wait:
    sample 1001 (on both) is retired after the merge and new Attribute nodes are created until one takes its element
    id. Skips when Neo4j reuses none in time."""
    import time
    from nextseek_api.graph_sync import study_merge
    _split_pair(studies_lane, monkeypatch)
    study_merge.apply(studies_lane.driver, DB, {1: "merge"}, run_dir=str(tmp_path))
    freed = _element_ids(studies_lane, "Sample", "id")[1001]
    studies_lane.write("MATCH (s:Sample {id: 1001}) DETACH DELETE s")
    # Freed ids are handed out again after a short delay, oldest first, and earlier tests freed many: create in bulk.
    deadline, taken = time.monotonic() + 45, False
    while not taken and time.monotonic() < deadline:
        made = studies_lane.driver.execute_query(
            "UNWIND range(1, 1000) AS i CREATE (a:Attribute {name: 'probe'}) RETURN elementId(a) AS e", database_=DB)
        taken = freed in {r["e"] for r in made.records}
        if not taken:
            time.sleep(1)
    if not taken:
        pytest.skip("Neo4j reused no freed element id within 45 s")
    studies_lane.write("MATCH (a:Attribute) WHERE elementId(a) <> $e DETACH DELETE a", {"e": freed})
    undone = study_merge.undo(studies_lane.driver, DB, [str(tmp_path)])
    assert studies_lane.read("MATCH (a:Attribute)-[:IN_STUDY]->() RETURN count(a) AS n") == [{"n": 0}]
    assert [s["id"] for s in undone["studies"][0]["sources_replaced"]] == [1001]


def test_undo_after_the_nightly_deleted_the_legacy_investigation_is_partial(studies_lane, monkeypatch, tmp_path):
    """A merge_other_investigation leaves the legacy Investigation empty and the nightly deletes it (no Study holds
    it, SEEK lacks it). The undo cannot restore it: the legacy study comes back under none, the undo names it, and its
    status is partial (the command exits 1)."""
    from nextseek_api.graph_sync import study_merge
    _split_pair(studies_lane, monkeypatch, _OTHER_INVESTIGATION)
    assert study_merge.apply(studies_lane.driver, DB, {1: "merge_other_investigation"},
                             run_dir=str(tmp_path))["status"] == "ok"
    studies_lane.write("MATCH (i:Investigation {id: 901}) WHERE NOT EXISTS { (i)<-[:IN_INVESTIGATION]-() } "
                       "DETACH DELETE i")
    undone = study_merge.undo(studies_lane.driver, DB, [str(tmp_path)])
    assert undone["status"] == "partial"
    assert undone["investigation_not_restored"] == [
        {"study_id": 1, "node": "legacy", "investigation": {"id": 901, "title": "Alder Investigation"}}]
    assert studies_lane.read("MATCH (:Study {id: 1})-[:IN_INVESTIGATION]->(i) RETURN i.id AS id") == []
    assert _keys(studies_lane, 1002) == [("seek", 1)]


def test_undo_links_an_on_both_source_back_only_while_it_links_to_the_legacy_node(studies_lane, monkeypatch,
                                                                                    tmp_path):
    """1001 sat on both nodes; after the merge a removal SEEK made (its archive not given to the undo) took its link
    to the merged node. The undo does not link it to the seek-keyed node again."""
    from nextseek_api.graph_sync import study_merge
    _split_pair(studies_lane, monkeypatch)
    study_merge.apply(studies_lane.driver, DB, {1: "merge"}, run_dir=str(tmp_path))
    studies_lane.write("MATCH (:Sample {id: 1001})-[e:IN_STUDY]->() DELETE e")
    assert study_merge.undo(studies_lane.driver, DB, [str(tmp_path)])["status"] == "ok"
    assert _keys(studies_lane, 1001) == [] and _keys(studies_lane, 1002) == [("seek", 1)]


# --- the connections endpoint's selectors on a real Neo4j (Task 14, A8) ---------------------------------------------

_CONNECTIONS_GRAPH = [
    "CREATE (p1:Project {id: 11, title: 'Alder'}), (p2:Project {id: 12, title: 'Birch'}), "
    "(i:Investigation {id: 101, title: 'Alder Investigation', project_id: 11}), "
    "(i)-[:IN_PROJECT]->(p1), (i)-[:IN_PROJECT]->(p2), "
    "(:Investigation {id: 102, title: 'Bare Investigation', project_id: 12})-[:IN_PROJECT]->(p2), "
    "(st:Study {seek_study_id: 1, title: 'Alder Unpublished'})-[:IN_INVESTIGATION]->(i), "
    "(paper:Study {id: 1, title: 'A paper', DOI: '10.9999/p1'})-[:IN_INVESTIGATION]->(i), "
    "(t:Sample {id: 1001, type: 'TIS'})-[:IN_PROJECT]->(p1), (t)-[:IN_STUDY]->(st), "
    "(c:Sample {id: 1002, type: 'CEL'})-[:IN_PROJECT]->(p1), (c)-[:IN_STUDY]->(st), "
    "(c)-[:DERIVED_FROM {internal_assay_title: 'Cell Isolation'}]->(t), "
    "(c2:Sample {id: 1003, type: 'CEL'})-[:IN_PROJECT]->(p2), "
    "(d:Sample {id: 1004, type: 'DNA'})-[:IN_PROJECT]->(p2), "
    "(d)-[:DERIVED_FROM {internal_assay_titles: ['DNA Extraction']}]->(c2), "
    "(x:Sample {id: 1005, type: 'DNA'})-[:IN_STUDY]->(paper), "
    "(x)-[:DERIVED_FROM {internal_assay_title: 'Paper Assay'}]->(t)",
    "CREATE (:SampleType {id: 1, title: 'TIS'}), (:SampleType {id: 2, title: 'CEL'}), "
    "(:SampleType {id: 3, title: 'DNA'})",
]
_CELL = ("TIS", "CEL", "Cell Isolation", 1)
_DNA = ("CEL", "DNA", "DNA Extraction", 1)
_PAPER = ("TIS", "DNA", "Paper Assay", 1)


def _connections(lane, cypher, **selector):
    params = {"sample_type": None, "project_id": None, "graph_inv_id": None, "seek_inv_id": None, "name": None,
              "graph_study_ids": None, "seek_study_ids": None, **selector}
    return sorted((r["parent_sample_type"], r["child_sample_type"], r["internal_assay"], r["n_edges"])
                  for r in lane.read(cypher, params))


def test_the_connections_selectors_on_a_real_graph(studies_lane):
    """project_id reaches a project's samples whatever their study (samples no study holds included); seek_inv_id
    reaches an investigation under every project it is linked to; an edge with only plural assay titles counts; the
    study predicate reads each node by its own key; the subtree form takes the same scopes."""
    from nextseek_api.services import sampletype_connections as sc
    _load(studies_lane, _CONNECTIONS_GRAPH)
    direct = sc.CONNECTIONS_CYPHER
    assert _connections(studies_lane, direct) == [_DNA, _CELL, _PAPER]
    assert _connections(studies_lane, direct, project_id=12) == [_DNA]
    assert _connections(studies_lane, direct, project_id=11) == [_CELL]
    assert _connections(studies_lane, direct, seek_inv_id=12) == [_CELL, _PAPER]
    assert _connections(studies_lane, direct, seek_inv_id=11) == [_CELL, _PAPER]
    assert _connections(studies_lane, direct, graph_inv_id=102) == []
    assert _connections(studies_lane, direct, seek_study_ids=[1], graph_study_ids=[]) == [_CELL]
    assert _connections(studies_lane, direct, graph_study_ids=[1], seek_study_ids=[]) == [_PAPER]
    assert _connections(studies_lane, direct, graph_study_ids=[], seek_study_ids=[]) == []
    assert _connections(studies_lane, sc.CONNECTIONS_SUBTREE_CYPHER, sample_type="TIS", project_id=11) == [_CELL]


def test_the_empty_answer_notes_read_a_real_graph(studies_lane):
    from nextseek_api.services import sampletype_connections as sc
    _load(studies_lane, _CONNECTIONS_GRAPH)
    notes = {"graph_inv_id": None, "seek_inv_id": None, "name": None}
    assert studies_lane.read(sc.INVESTIGATION_NOTES_CYPHER, {**notes, "graph_inv_id": 102}) == [
        {"investigations": 1, "with_studies": 0}]
    assert studies_lane.read(sc.INVESTIGATION_NOTES_CYPHER, {**notes, "seek_inv_id": 12}) == [
        {"investigations": 2, "with_studies": 1}]
    assert studies_lane.read(sc.INVESTIGATION_NOTES_CYPHER, {**notes, "name": "no such"}) == [
        {"investigations": 0, "with_studies": 0}]
    assert studies_lane.read(sc.PROJECT_NOTES_CYPHER, {"project_id": 12}) == [{"projects": 1, "samples": 2}]
    assert studies_lane.read(sc.PROJECT_NOTES_CYPHER, {"project_id": 99}) == [{"projects": 0, "samples": 0}]
    assert sorted(r["title"] for r in studies_lane.read(sc.SAMPLE_TYPE_TITLES_CYPHER)) == ["CEL", "DNA", "TIS"]
    assert {(r["id"], r["seek_study_id"]) for r in studies_lane.read(sc.STUDY_KEYS_CYPHER)} == {(None, 1), (1, None)}
