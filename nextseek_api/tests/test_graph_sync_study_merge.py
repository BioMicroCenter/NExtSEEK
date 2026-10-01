"""The study merge (nextseek_api/graph_sync/study_merge.py): selection, the plan report, apply and undo.

No database and no Neo4j: ``StudyGraph`` stands in for the graph; SEEK's studies, investigations and study links come
from the ``world`` fixture. Every id and title is synthetic; only the shapes are taken from the boxes.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import sources, study_links, study_merge, writer
from nextseek_api.tests.graph_sync_study_fakes import StudyGraph

DB = "neo4j"


@pytest.fixture
def world(monkeypatch):
    """SEEK's side (``studies``, ``investigations``, ``links`` as (sample id, study id)) and the graph. SEEK holds
    investigations 101 (Alder) and 102 (Birch); the graph holds a node for each."""
    w = SimpleNamespace(graph=StudyGraph(), studies=[], investigations=[], links=[], inv={})
    monkeypatch.setattr(sources, "studies", lambda: [dict(s) for s in w.studies])
    monkeypatch.setattr(sources, "investigations", lambda: [dict(i) for i in w.investigations])
    monkeypatch.setattr(sources, "seek_study_links_for", lambda ids: [
        {"sample_id": s, "study_id": t, "study_title": None, "study_description": None, "investigation_id": None}
        for s, t in sorted(w.links) if s in set(ids)])
    monkeypatch.setattr(sources, "iter_seek_study_links", lambda: iter(sorted(w.links)))
    monkeypatch.setattr(sources, "investigation_projects", lambda: [])
    monkeypatch.setattr(sources, "projects", lambda: [])
    for inv_id, title in ((101, "Alder Investigation"), (102, "Birch Investigation")):
        w.investigations.append({"id": inv_id, "title": title, "description": None})
        w.inv[inv_id] = w.graph.add_investigation(inv_id, title)
    return w


def _seek(w, sid, title, inv=101, description=None):
    w.studies.append({"id": sid, "title": title, "description": description, "investigation_id": inv})


def _kind(w, sid):
    return study_merge.classify(study_merge.read_index(w.graph, DB), sid)


# --- selection: one case per kind, in the spec's order --------------------------------------------------------------

def test_already_merged(world):
    _seek(world, 1, "Alder Unpublished")
    world.graph.add_study(id=1, seek_study_id=1, title="Alder Unpublished", investigation=world.inv[101])
    assert _kind(world, 1).kind == "already_merged"


def test_seek_only_and_not_in_seek(world):
    _seek(world, 12, "Larch Study")
    world.graph.add_study(seek_study_id=12, title="Larch Study", investigation=world.inv[101])
    world.graph.add_study(id=99, title="Gone", DOI="", investigation=world.inv[101])
    assert _kind(world, 12).kind == "seek_only"
    assert _kind(world, 99).kind == "not_in_seek"


def test_duplicate_seek_key(world):
    _seek(world, 1, "Alder Unpublished")
    world.graph.add_study(id=1, title="Alder Unpublished", DOI="", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=1, title="Alder Unpublished", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=1, title="Alder again", investigation=world.inv[101])
    assert _kind(world, 1).kind == "duplicate_seek_key"


def test_k_relationship_names_the_extra_relationship(world):
    _seek(world, 1, "Alder Unpublished")
    world.graph.add_study(id=1, title="Alder Unpublished", DOI="", investigation=world.inv[101])
    k = world.graph.add_study(seek_study_id=1, title="Alder Unpublished", investigation=world.inv[101])
    world.graph.other_rels[k] = ["HAS_NOTE"]
    sel = _kind(world, 1)
    assert sel.kind == "k_relationship" and "HAS_NOTE" in sel.reason


@pytest.mark.parametrize("legacy_invs", [0, 2])
def test_investigation_count(world, legacy_invs):
    _seek(world, 1, "Alder Unpublished")
    invs = [world.inv[101], world.inv[102]][:legacy_invs]
    world.graph.add_study(id=1, title="Alder Unpublished", DOI="", investigation=invs)
    world.graph.add_study(seek_study_id=1, title="Alder Unpublished", investigation=world.inv[101])
    assert _kind(world, 1).kind == "investigation_count"


def test_a_split_whose_legacy_node_carries_the_empty_marker_reads_merge(world):
    _seek(world, 1, "Alder")                                   # before a rename the titles may differ
    world.graph.add_study(id=1, title="Alder Unpublished", DOI="", PMID="", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=1, title="Alder", investigation=world.inv[101])
    sel = _kind(world, 1)
    assert (sel.kind, sel.test) == ("merge", "marker")


def test_a_split_with_both_nodes_empty_reads_merge(world):
    _seek(world, 2, "Birch Unpublished", inv=102)
    world.graph.add_study(id=2, title="Birch Unpublished", DOI="", investigation=world.inv[102])
    world.graph.add_study(seek_study_id=2, title="Birch Unpublished", investigation=world.inv[102])
    assert _kind(world, 2).kind == "merge"


def test_a_split_paper_reads_merge_by_the_match(world):
    _seek(world, 6, "Fir paper")
    world.graph.add_study(id=6, title="Fir paper", DOI="10.9999/f6", PMID="6", investigation=world.inv[101])
    k = world.graph.add_study(seek_study_id=6, title="Fir paper", investigation=world.inv[101])
    world.graph.add_sample(1001)
    world.graph.link(1001, k)
    sel = _kind(world, 6)
    assert (sel.kind, sel.test) == ("merge", "match")


def test_an_orphan_samples_link_on_k_leaves_it_mergeable(world):
    _seek(world, 1, "Alder Unpublished")
    world.graph.add_study(id=1, title="Alder Unpublished", DOI="", investigation=world.inv[101])
    k = world.graph.add_study(seek_study_id=1, title="Alder Unpublished", investigation=world.inv[101])
    world.graph.link(world.graph.add_sample(1005, label="OrphanSample"), k)
    assert _kind(world, 1).kind == "merge"


def test_merge_other_investigation(world):
    legacy_inv = world.graph.add_investigation(901, "Alder Investigation")   # a node the sync never wrote
    _seek(world, 3, "Cedar Unpublished", inv=101)
    world.graph.add_study(id=3, title="Cedar Unpublished", DOI="", investigation=legacy_inv)
    world.graph.add_study(seek_study_id=3, title="Cedar Unpublished", investigation=world.inv[101])
    assert _kind(world, 3).kind == "merge_other_investigation"


@pytest.mark.parametrize("with_k", [False, True])
def test_a_seek_paper_with_nothing_split_is_rekeyed_in_place(world, with_k):
    _seek(world, 5, "Elm paper")
    world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=world.inv[101])
    if with_k:
        world.graph.add_study(seek_study_id=5, title="Elm paper", investigation=world.inv[101])
    sel = _kind(world, 5)
    assert (sel.kind, sel.test) == ("rekey_in_place", "match")


def test_a_seek_title_ending_in_a_no_break_space_still_matches(world):
    _seek(world, 7, "Gum paper ")
    world.graph.add_study(id=7, title="Gum paper", DOI="10.9999/g7", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=7, title="Gum paper ", investigation=world.inv[101])
    assert _kind(world, 7).kind == "rekey_in_place"


def test_the_investigation_title_is_compared_without_case_or_surrounding_whitespace(world):
    world.investigations[0]["title"] = "  ALDER investigation "
    _seek(world, 5, "Elm paper")
    world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=world.inv[101])
    assert _kind(world, 5).kind == "rekey_in_place"


def test_the_match_needs_the_investigations_id_and_title(world):
    world.investigations[0]["title"] = "Another investigation"
    _seek(world, 5, "Elm paper")
    world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=world.inv[101])
    assert _kind(world, 5).kind == "paper"


def test_legacy_only(world):
    _seek(world, 14, "Nutmeg")
    world.graph.add_study(id=14, title="Nutmeg Unpublished", DOI="", investigation=world.inv[101])
    assert _kind(world, 14).kind == "legacy_only"


def test_other_seek_investigation(world):
    world.investigations.append({"id": 103, "title": "Alder Investigation", "description": None})
    other = world.graph.add_investigation(103, "Alder Investigation")
    _seek(world, 3, "Cedar", inv=101)
    world.graph.add_study(id=3, title="Cedar", DOI="", investigation=other)
    world.graph.add_study(seek_study_id=3, title="Cedar", investigation=world.inv[101])
    assert _kind(world, 3).kind == "other_seek_investigation"


def test_investigation_title_differs(world):
    _seek(world, 3, "Cedar", inv=101)
    world.graph.add_study(id=3, title="Cedar", DOI="", investigation=world.inv[102])
    world.graph.add_study(seek_study_id=3, title="Cedar", investigation=world.inv[101])
    assert _kind(world, 3).kind == "investigation_title_differs"


def test_investigation_not_seeks(world):
    legacy_inv = world.graph.add_investigation(901, "Birch Investigation")
    _seek(world, 3, "Cedar", inv=101)
    world.graph.add_study(id=3, title="Cedar", DOI="", investigation=legacy_inv)
    world.graph.add_study(seek_study_id=3, title="Cedar", investigation=world.inv[102])
    assert _kind(world, 3).kind == "investigation_not_seeks"


def test_id_collision_and_paper(world):
    _seek(world, 8, "Hazel Study")
    world.graph.add_study(id=8, title="An unrelated paper", DOI="10.9999/p8", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=8, title="Hazel Study", investigation=world.inv[101])
    _seek(world, 10, "Juniper Study")
    world.graph.add_study(id=10, title="Another paper", DOI="10.9999/p10", investigation=world.inv[101])
    assert _kind(world, 8).kind == "id_collision"
    assert _kind(world, 10).kind == "paper"


def test_once_every_seek_study_has_a_node_an_empty_one_beside_a_paper_reads_id_collision(world):
    """Every SEEK study gets a node, members or not: a SEEK study with no sample whose id a graph-only paper also
    holds then reads id_collision, never acted on, where it read paper before its node existed."""
    _seek(world, 10, "Juniper Study")
    world.graph.add_study(id=10, title="Another paper", DOI="10.9999/p10", investigation=world.inv[101])
    assert _kind(world, 10).kind == "paper"
    writer.write_seek_study_nodes(world.graph, DB, world.studies)
    assert _kind(world, 10).kind == "id_collision"


def test_a_legacy_only_node_reads_merge_once_its_study_has_a_node(world):
    """legacy_only lasts until the first write gives SEEK study X its node; the id then goes back to the operator
    as a merge (rollout step 9's dry run lists it)."""
    _seek(world, 14, "Nutmeg")
    world.graph.add_study(id=14, title="Nutmeg Unpublished", DOI="", investigation=world.inv[101])
    assert _kind(world, 14).kind == "legacy_only"
    writer.write_seek_study_nodes(world.graph, DB, world.studies)
    assert _kind(world, 14).kind == "merge"


def test_non_int_ids_are_skipped(world):
    world.graph.add_study(id="legacy-3", title="x", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=2.5, title="y", investigation=world.inv[101])
    index = study_merge.read_index(world.graph, DB)
    assert study_merge.study_ids(index) == []
    assert study_merge.plan(world.graph, DB)["kinds"] == {}


# --- the plan report -----------------------------------------------------------------------------------------------

def _plan_world(world):
    """1 a marker split (L: 1001, 1002, 1006; K: 1003 and an orphan; both: 1004), 3 an other-investigation split,
    5 a paper to rekey in place, 8 a collision, 10 a paper, 12 SEEK-keyed only, 14 legacy only."""
    g, inv = world.graph, world.inv
    legacy_inv = g.add_investigation(901, "Alder Investigation")
    for sid, title, inv_id in ((1, "Alder Unpublished", 101), (3, "Cedar Unpublished", 101), (5, "Elm paper", 101),
                               (8, "Hazel Study", 101), (10, "Juniper Study", 101), (12, "Larch Study", 101),
                               (14, "Nutmeg", 101)):
        _seek(world, sid, title, inv=inv_id)
    l1 = g.add_study(id=1, title="Alder Unpublished", DOI="", PMID="", investigation=inv[101])
    k1 = g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=inv[101])
    g.add_study(id=3, title="Cedar Unpublished", DOI="", investigation=legacy_inv)
    g.add_study(seek_study_id=3, title="Cedar Unpublished", investigation=inv[101])
    g.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=inv[101])
    g.add_study(id=8, title="An unrelated paper", DOI="10.9999/p8", investigation=inv[101])
    g.add_study(seek_study_id=8, title="Hazel Study", investigation=inv[101])
    paper10 = g.add_study(id=10, title="Another paper", DOI="10.9999/p10", investigation=inv[101])
    g.add_study(seek_study_id=12, title="Larch Study", investigation=inv[101])
    g.add_study(id=14, title="Nutmeg Unpublished", DOI="", investigation=inv[101])
    for sid in (1001, 1002, 1003, 1004, 1006):
        g.add_sample(sid)
    for sid in (1001, 1002, 1006, 1004):
        g.link(sid, l1)
    for sid in (1003, 1004):
        g.link(sid, k1)
    g.link(g.add_sample(1005, label="OrphanSample"), k1)
    g.link(1006, paper10)
    world.links = [(1001, 1), (1003, 1), (1004, 12)]
    return legacy_inv


def test_the_plan_reports_each_id_and_ends_with_the_line_to_approve(world):
    legacy_inv = _plan_world(world)
    report = study_merge.plan(world.graph, DB)

    assert report["kinds"] == {1: "merge", 3: "merge_other_investigation", 5: "rekey_in_place", 8: "id_collision",
                               10: "paper", 12: "seek_only", 14: "legacy_only"}
    assert [s["study_id"] for s in report["studies"]] == [1, 3, 5, 8, 10, 14]      # seek_only is counted, not listed
    assert report["counts"]["seek_only"] == 1
    entry = report["studies"][0]
    assert (entry["kind"], entry["test"], entry["seek_title"]) == ("merge", "marker", "Alder Unpublished")
    assert entry["samples"] == {"on_legacy": 3, "on_seek_keyed": 1, "on_both": 1}
    assert entry["seek_keyed_other_sources"] == {"OrphanSample": 1}
    assert entry["studies_preview"] == {"kept": 2, "leaves": 1, "no_seek_study": 2, "paper_samples": 1}
    assert entry["description_differs"] is False and entry["seek_description_empty"] is True
    assert report["approval_line"] == "1:merge,3:merge_other_investigation,5:rekey_in_place"
    assert study_merge.parse_approval(report["approval_line"]) == {1: "merge", 3: "merge_other_investigation",
                                                                   5: "rekey_in_place"}
    assert report["merge_other_investigation"] == [3]
    assert report["id_collisions"] == [8] and report["legacy_only"] == [14]
    assert [(e["study_id"], e["investigation"]["element_id"]) for e in report["investigations_left_empty"]] == [
        (3, legacy_inv)]
    assert all(c.read for c in world.graph.calls)


def test_a_plan_of_listed_ids_classifies_only_those(world):
    _plan_world(world)
    report = study_merge.plan(world.graph, DB, [5, 1, 5])
    assert report["ids"] == [1, 5] and set(report["kinds"]) == {1, 5}


def test_a_plan_without_detail_reads_no_members(world):
    _plan_world(world)
    report = study_merge.plan(world.graph, DB, detail=False)
    assert world.graph.of(q.STUDY_SOURCES) == [] and "samples" not in report["studies"][0]


def test_the_preview_counts_every_sample_by_seek_and_paper_samples_besides(world):
    """--studies removes a paper sample's link to X too when SEEK files it elsewhere, so every sample is counted kept,
    leaves or no_seek_study from SEEK's studies alone, and paper_samples counts those also on a graph-only paper. A
    link to the legacy node of another id this plan acts on is no paper link: that node becomes a SEEK study."""
    g, inv = world.graph, world.inv
    _seek(world, 1, "Alder Unpublished")
    _seek(world, 2, "Birch Unpublished")
    _seek(world, 12, "Larch Study")
    l1 = g.add_study(id=1, title="Alder Unpublished", DOI="", investigation=inv[101])
    g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=inv[101])
    l2 = g.add_study(id=2, title="Birch Unpublished", DOI="", investigation=inv[101])
    g.add_study(seek_study_id=2, title="Birch Unpublished", investigation=inv[101])
    paper = g.add_study(id=19, title="A graph-only paper", DOI="10.9999/p19", investigation=inv[101])
    for sid in (1007, 1008, 1009):
        g.add_sample(sid)
        g.link(sid, l1)
    g.link(1007, paper)                         # SEEK files it under 12: its link to 1 goes
    g.link(1008, l2)                            # on the legacy node of 2, which this plan merges too
    g.link(1009, paper)                         # in no SEEK study
    world.links = [(1007, 12), (1008, 1)]
    entry = study_merge.plan(world.graph, DB, [1, 2])["studies"][0]
    assert entry["studies_preview"] == {"kept": 1, "leaves": 1, "no_seek_study": 1, "paper_samples": 2}


# --- apply ---------------------------------------------------------------------------------------------------------

def _split(world, sid=1, on_l=(1001,), on_k=(1002,), on_both=(1003,), doi="", pmid=""):
    _seek(world, sid, "Alder Unpublished")
    g = world.graph
    legacy = g.add_study(id=sid, title="Alder Unpublished", DOI=doi, PMID=pmid, investigation=world.inv[101])
    keyed = g.add_study(seek_study_id=sid, title="Alder Unpublished", investigation=world.inv[101])
    for s in on_l + on_k + on_both:
        g.add_sample(s)
    for s in on_l + on_both:
        g.link(s, legacy)
    for s in on_k + on_both:
        g.link(s, keyed)
    return legacy, keyed


def _journal(run_dir):
    lines = (run_dir / study_merge.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()
    assert lines[0] == study_merge.JOURNAL_HEADER.rstrip("\n")
    return [(int(a), b, json.loads(c)) for a, b, c in (line.split("\t", 2) for line in lines[1:])]


def test_apply_merges_a_split_and_journals_each_step_before_its_write(world, tmp_path):
    legacy, keyed = _split(world)
    at_write = []
    world.graph.before_write = lambda query, params: at_write.append(len(_journal(tmp_path)))
    result = study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))

    assert result["status"] == "ok" and result["merged"] == [{"study_id": 1, "kind": "merge"}]
    assert keyed not in world.graph.studies
    assert world.graph.studies[legacy] == {"id": 1, "title": "Alder Unpublished", "seek_study_id": 1}
    for sample_id in (1001, 1002, 1003):
        assert world.graph.keys_of(sample_id) == {("seek", 1)}
    assert sum(1 for s, _ in world.graph.in_study.values() if s == "s:1003") == 1
    assert [record for _, record, _ in _journal(tmp_path)] == ["plan", "source", "source", "done"]
    assert at_write == [3, 3]
    places = {p["id"]: p["place"] for _, record, p in _journal(tmp_path) if record == "source"}
    assert places == {1002: "only_on_k", 1003: "on_both"}
    plan = _journal(tmp_path)[0][2]
    assert plan["legacy"]["props"] == {"id": 1, "title": "Alder Unpublished", "DOI": "", "PMID": ""}
    assert plan["seek_keyed"]["props"] == {"seek_study_id": 1, "title": "Alder Unpublished"}
    assert plan["legacy_sources"] == [{"element_id": "s:1001", "id": 1001, "labels": ["Sample"]},
                                      {"element_id": "s:1003", "id": 1003, "labels": ["Sample"]}]


def test_a_rekey_in_place_keeps_a_non_empty_doi_and_deletes_the_empty_seek_keyed_node(world, tmp_path):
    _seek(world, 5, "Elm paper")
    legacy = world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", PMID="5", investigation=world.inv[101])
    keyed = world.graph.add_study(seek_study_id=5, title="Elm paper", investigation=world.inv[101])
    study_merge.apply(world.graph, DB, {5: "rekey_in_place"}, run_dir=str(tmp_path))
    assert world.graph.studies[legacy] == {"id": 5, "title": "Elm paper", "DOI": "10.9999/e5", "PMID": "5",
                                           "seek_study_id": 5}
    assert keyed not in world.graph.studies and world.graph.of(q.MOVE_IN_STUDY) == []


def test_merge_other_investigation_moves_the_legacy_nodes_investigation(world, tmp_path):
    legacy_inv = world.graph.add_investigation(901, "Alder Investigation")
    _seek(world, 3, "Cedar Unpublished", inv=101)
    legacy = world.graph.add_study(id=3, title="Cedar Unpublished", DOI="", investigation=legacy_inv)
    world.graph.add_study(seek_study_id=3, title="Cedar Unpublished", investigation=world.inv[101])
    study_merge.apply(world.graph, DB, {3: "merge_other_investigation"}, run_dir=str(tmp_path))
    assert world.graph.in_investigation[legacy] == [world.inv[101]]


def test_apply_moves_in_batches(world, tmp_path):
    _split(world, on_l=(), on_k=(1002, 1004, 1005), on_both=())
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path), batch=1)
    assert len(world.graph.of(q.MOVE_IN_STUDY)) == 3


def test_a_source_with_parallel_edges_moves_once(world, tmp_path):
    _, keyed = _split(world, on_l=(), on_k=(1002,), on_both=())
    world.graph.link(1002, keyed)
    result = study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))
    assert result["status"] == "ok"
    assert [c.params["sources"] for c in world.graph.of(q.MOVE_IN_STUDY)] == [["s:1002"]]
    assert world.graph.keys_of(1002) == {("seek", 1)}


def test_a_source_with_two_edges_to_the_legacy_node_counts_once(world, tmp_path):
    """MERGE to L matches both of a source's parallel edges to L, so the batch's check counts sources, not rows."""
    legacy, keyed = _split(world, on_l=(), on_k=(1002,), on_both=(1003,))
    world.graph.link(1003, legacy)
    result = study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))
    assert result["status"] == "ok" and keyed not in world.graph.studies
    assert world.graph.keys_of(1002) == world.graph.keys_of(1003) == {("seek", 1)}


def test_a_crash_between_batches_is_finished_by_a_rerun_into_the_same_journal(world, tmp_path):
    _split(world, on_l=(), on_k=(1002, 1004), on_both=())
    world.graph.fail_moves_after = 1
    with pytest.raises(RuntimeError):
        study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path), batch=1)
    assert _kind(world, 1).kind == "merge"
    world.graph.fail_moves_after = None
    result = study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path), batch=1)
    assert result["status"] == "ok"
    assert [record for _, record, _ in _journal(tmp_path)] == ["plan", "source", "source", "plan", "source", "done"]
    assert world.graph.keys_of(1002) == world.graph.keys_of(1004) == {("seek", 1)}


def test_an_already_merged_id_is_counted_and_not_written(world, tmp_path):
    _split(world)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "first"))
    writes = len(world.graph.writes())
    result = study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "second"))
    assert result["already_merged"] == [1] and result["merged"] == []
    assert len(world.graph.writes()) == writes and not (tmp_path / "second" / study_merge.JOURNAL_FILE).exists()


def test_an_id_whose_kind_changed_stops_the_run_before_its_first_write(world, tmp_path):
    """Nothing written in this run: a refusal (the command exits 2)."""
    _split(world)
    result = study_merge.apply(world.graph, DB, {1: "rekey_in_place"}, run_dir=str(tmp_path))
    assert (result["status"], result["stopped_at"]) == ("refused", 1)
    assert "rekey_in_place" in result["problem"] and world.graph.writes() == []


def test_a_kind_changed_after_an_earlier_id_merged_stops_the_run_part_way(world, tmp_path):
    _split(world)
    _seek(world, 5, "Elm paper")
    world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=world.inv[101])
    result = study_merge.apply(world.graph, DB, {1: "merge", 5: "merge"}, run_dir=str(tmp_path))
    assert (result["status"], result["stopped_at"]) == ("failed", 5)
    assert result["merged"] == [{"study_id": 1, "kind": "merge"}]


def test_a_rerun_of_a_journaled_id_is_held_to_the_kind_its_journal_recorded(world, tmp_path):
    """A rekey approved while the seek-keyed node was empty stopped before its last step; the node has gained a
    sample since. A rerun into the same run directory, even with a fresh approval of merge, is held to the journal's
    rekey_in_place and refuses."""
    _seek(world, 6, "Fir paper")
    world.graph.add_study(id=6, title="Fir paper", DOI="10.9999/f6", investigation=world.inv[101])
    keyed = world.graph.add_study(seek_study_id=6, title="Fir paper", investigation=world.inv[101])

    def lost_at_the_last_step(query, params):
        if query == q.FINISH_STUDY_MERGE:
            raise RuntimeError("the connection to Neo4j was lost")

    world.graph.before_write = lost_at_the_last_step
    with pytest.raises(RuntimeError):
        study_merge.apply(world.graph, DB, {6: "rekey_in_place"}, run_dir=str(tmp_path))
    world.graph.before_write = None
    world.graph.add_sample(1001)
    world.graph.link(1001, keyed)
    assert _kind(world, 6).kind == "merge"
    writes = len(world.graph.writes())
    result = study_merge.apply(world.graph, DB, {6: "merge"}, run_dir=str(tmp_path))
    assert (result["status"], result["stopped_at"]) == ("refused", 6)
    assert "journal" in result["problem"] and len(world.graph.writes()) == writes
    finished = study_merge.apply(world.graph, DB, {6: "merge"}, run_dir=str(tmp_path / "fresh"))
    assert finished["status"] == "ok"


def test_a_finished_id_in_the_journal_takes_the_approved_kind_again(world, tmp_path):
    """Merged, undone, then approved again into the same run directory: the journal's attempt finished, so the new
    approval is the one held to."""
    _split(world)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))
    study_merge.undo(world.graph, DB, [str(tmp_path)])
    assert study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))["status"] == "ok"


@pytest.mark.parametrize("text, expected", [
    ("3:merge,4:merge", {3: "merge", 4: "merge"}),
    (" 4:rekey_in_place , 3 ,4:rekey_in_place", {4: "rekey_in_place", 3: None}),
    ("6:merge_other_investigation", {6: "merge_other_investigation"}),
])
def test_parse_approval(text, expected):
    parsed = study_merge.parse_approval(text)
    assert parsed == expected and list(parsed) == list(expected)


@pytest.mark.parametrize("text", ["", "3,,4", "x", "3:mergee", "3:merge,3:rekey_in_place", "3:", ":merge"])
def test_parse_approval_refuses_what_it_cannot_read(text):
    with pytest.raises(ValueError):
        study_merge.parse_approval(text)


def test_the_last_step_refuses_a_seek_keyed_node_that_gained_a_relationship(world, tmp_path):
    _, keyed = _split(world)

    def meanwhile(query, params):
        if query == q.FINISH_STUDY_MERGE:
            world.graph.other_rels[keyed] = ["HAS_NOTE"]

    world.graph.before_write = meanwhile
    with pytest.raises(RuntimeError, match="last step"):
        study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))
    assert keyed in world.graph.studies


def test_the_last_step_refuses_a_second_node_that_took_the_key_meanwhile(world, tmp_path):
    _, keyed = _split(world)

    def meanwhile(query, params):
        if query == q.FINISH_STUDY_MERGE:
            world.graph.add_study(seek_study_id=1, title="Someone else", investigation=world.inv[101])

    world.graph.before_write = meanwhile
    with pytest.raises(RuntimeError, match="last step"):
        study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path))
    assert keyed in world.graph.studies


def test_a_match_merge_that_moved_every_source_before_a_crash_is_finished_by_a_rerun(world, tmp_path):
    """A paper that is also a SEEK study, split. After its last move and before its last step the seek-keyed node
    holds no IN_STUDY, so the id reads rekey_in_place; the rerun of the approved merge finishes it with the same last
    step."""
    _seek(world, 6, "Fir paper")
    legacy = world.graph.add_study(id=6, title="Fir paper", DOI="10.9999/f6", investigation=world.inv[101])
    keyed = world.graph.add_study(seek_study_id=6, title="Fir paper", investigation=world.inv[101])
    world.graph.add_sample(1001)
    world.graph.link(1001, keyed)
    assert _kind(world, 6).kind == "merge"

    def lost_at_the_last_step(query, params):
        if query == q.FINISH_STUDY_MERGE:
            raise RuntimeError("the connection to Neo4j was lost")

    world.graph.before_write = lost_at_the_last_step
    with pytest.raises(RuntimeError):
        study_merge.apply(world.graph, DB, {6: "merge"}, run_dir=str(tmp_path))
    assert _kind(world, 6).kind == "rekey_in_place"
    world.graph.before_write = None
    result = study_merge.apply(world.graph, DB, {6: "merge"}, run_dir=str(tmp_path))
    assert result["status"] == "ok" and result["merged"] == [{"study_id": 6, "kind": "rekey_in_place"}]
    assert keyed not in world.graph.studies and world.graph.studies[legacy]["seek_study_id"] == 6
    assert world.graph.keys_of(1001) == {("seek", 6)}
    assert [record for _, record, _ in _journal(tmp_path)] == ["plan", "source", "plan", "done"]


def test_an_approved_rekey_that_now_reads_merge_still_stops(world, tmp_path):
    """Only the one direction is finished: a rekey approved while K was empty that now finds K holding samples is a
    different operation, and stops for the operator."""
    _seek(world, 6, "Fir paper")
    world.graph.add_study(id=6, title="Fir paper", DOI="10.9999/f6", investigation=world.inv[101])
    keyed = world.graph.add_study(seek_study_id=6, title="Fir paper", investigation=world.inv[101])
    world.graph.add_sample(1001)
    world.graph.link(1001, keyed)
    result = study_merge.apply(world.graph, DB, {6: "rekey_in_place"}, run_dir=str(tmp_path))
    assert (result["status"], result["stopped_at"]) == ("refused", 6) and world.graph.writes() == []


# --- undo ----------------------------------------------------------------------------------------------------------

def _snapshot(g):
    """The graph's Study layer by content, element ids aside: each node's properties and Investigation ids, and each
    IN_STUDY as (source id, label, target properties)."""
    studies = sorted((json.dumps(p, sort_keys=True), tuple(sorted(g.investigation_ids_of(e))))
                     for e, p in g.studies.items())
    links = sorted((g.sources[s]["id"], sorted(g.sources[s]["labels"])[0], json.dumps(g.studies[st], sort_keys=True))
                   for s, st in g.in_study.values())
    return studies, links


def test_undo_restores_a_split_exactly(world, tmp_path):
    _split(world)
    world.graph.link(world.graph.add_sample(1005, label="OrphanSample"), world.graph.studies_by_seek(1)[0])
    before = _snapshot(world.graph)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert result["status"] == "ok" and result["refused"] == []
    assert _snapshot(world.graph) == before


def test_undo_re_creates_an_empty_seek_keyed_node_and_the_legacy_investigation(world, tmp_path):
    legacy_inv = world.graph.add_investigation(901, "Alder Investigation")
    _seek(world, 3, "Cedar Unpublished", inv=101)
    world.graph.add_study(id=3, title="Cedar Unpublished", DOI="", investigation=legacy_inv)
    world.graph.add_study(seek_study_id=3, title="Cedar Unpublished", investigation=world.inv[101])
    _seek(world, 5, "Elm paper")
    world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=5, title="Elm paper", investigation=world.inv[101])
    before = _snapshot(world.graph)
    study_merge.apply(world.graph, DB, {3: "merge_other_investigation", 5: "rekey_in_place"},
                      run_dir=str(tmp_path / "m1"))
    study_merge.undo(world.graph, DB, [str(tmp_path / "m1" / study_merge.JOURNAL_FILE)])
    assert _snapshot(world.graph) == before


def test_undo_gathers_a_crash_and_its_rerun_from_two_run_directories(world, tmp_path):
    _split(world, on_l=(1001,), on_k=(1002, 1004), on_both=(1003,))
    before = _snapshot(world.graph)
    world.graph.fail_moves_after = 1
    with pytest.raises(RuntimeError):
        study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"), batch=1)
    world.graph.fail_moves_after = None
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m2"), batch=1)
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1"), str(tmp_path / "m2")])
    assert result["status"] == "ok"
    assert _snapshot(world.graph) == before


def test_undo_restores_a_link_studies_removed_from_a_merged_node(world, tmp_path):
    """1005 sat only on the seek-keyed node; after the merge it sat on L, and --studies removed that link because
    SEEK files it under study 12. Undo re-creates it on L from the archive, then moves it back to the re-created K."""
    _split(world, on_l=(1001,), on_k=(1005,), on_both=())
    _seek(world, 12, "Larch Study")
    world.graph.add_study(seek_study_id=12, title="Larch Study", investigation=world.inv[101])
    world.links = [(1001, 1), (1005, 12)]
    before = _snapshot(world.graph)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    study_links.rebuild_in_study(world.graph, DB, remove=True, run_dir=str(tmp_path / "s1"))
    assert world.graph.keys_of(1005) == {("seek", 12)}

    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1"), str(tmp_path / "s1")])
    assert result["archive_rows"] == 1 and result["archive_restored"] == 1
    assert world.graph.keys_of(1005) == {("seek", 1), ("seek", 12)}       # its link to 12 was added, not restored
    assert world.graph.keys_of(1001) == {("id", 1)}
    studies, links = _snapshot(world.graph)
    before_studies, before_links = before
    assert studies == before_studies
    assert sorted(set(links) - set(before_links)) == [
        (1005, "Sample", json.dumps({"seek_study_id": 12, "title": "Larch Study"}, sort_keys=True))]
    assert set(before_links) <= set(links)


def test_undo_moves_a_sample_that_reached_the_study_after_the_merge_to_the_seek_keyed_node(world, tmp_path):
    """After the merge, an upload links 1009 to study 1 and --studies adds SEEK's link of 1010; both land on the
    merged node. Neither is among the legacy node's journaled sources, so the undo moves both to the re-created
    seek-keyed node and lists them, where they would otherwise stay on the legacy node as paper samples."""
    legacy, _ = _split(world)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    for sample_id in (1009, 1010):
        world.graph.add_sample(sample_id)
    world.graph.link(1009, legacy)
    world.links = [(1001, 1), (1002, 1), (1003, 1), (1009, 1), (1010, 1)]
    study_links.rebuild_in_study(world.graph, DB, remove=False, run_dir=None)
    assert world.graph.keys_of(1010) == {("seek", 1)}

    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert result["status"] == "ok"
    assert world.graph.keys_of(1009) == world.graph.keys_of(1010) == {("seek", 1)}
    assert world.graph.keys_of(1001) == {("id", 1)} and world.graph.keys_of(1003) == {("id", 1), ("seek", 1)}
    entry = result["studies"][0]
    assert [a["id"] for a in entry["arrived_after_merge"]] == [1009, 1010] and entry["arrived_moved"] == 2


def test_an_arrival_on_a_rekey_with_no_seek_keyed_node_is_reported_and_the_undo_is_partial(world, tmp_path):
    _seek(world, 5, "Elm paper")
    legacy = world.graph.add_study(id=5, title="Elm paper", DOI="10.9999/e5", investigation=world.inv[101])
    world.graph.add_sample(1001)
    world.graph.link(1001, legacy)
    study_merge.apply(world.graph, DB, {5: "rekey_in_place"}, run_dir=str(tmp_path / "m1"))
    world.graph.add_sample(1011)
    world.graph.link(1011, legacy)
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert result["status"] == "partial"
    assert [a["id"] for a in result["studies"][0]["arrived_left_on_legacy"]] == [1011]
    assert world.graph.keys_of(1011) == world.graph.keys_of(1001) == {("id", 5)}


def test_undo_refuses_an_id_another_node_now_carries(world, tmp_path):
    _split(world)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    world.graph.add_study(seek_study_id=1, title="Someone else", investigation=world.inv[101])
    writes = len(world.graph.writes())
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert result["status"] == "partial" and [r["study_id"] for r in result["refused"]] == [1]
    assert len(world.graph.writes()) == writes


def test_an_undo_dry_run_writes_nothing(world, tmp_path):
    _split(world)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    writes = len(world.graph.writes())
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")], dry_run=True)
    assert result["status"] == "dry_run" and result["studies"][0]["state"] == "merged"
    assert len(world.graph.writes()) == writes


def test_undo_restores_non_ascii_titles_exactly(world, tmp_path):
    title = "Érable Unpublished "
    _seek(world, 1, title)
    legacy = world.graph.add_study(id=1, title=title, DOI="", investigation=world.inv[101])
    world.graph.add_study(seek_study_id=1, title=title, investigation=world.inv[101])
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    writer.write_seek_study_nodes(world.graph, DB, [{"id": 1, "title": "Maple Unpublished", "description": None,
                                                     "investigation_id": 101}])
    study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert world.graph.studies[legacy]["title"] == title
    text = (tmp_path / "m1" / study_merge.JOURNAL_FILE).read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text)


def test_undo_refuses_a_path_that_holds_no_journal_or_archive(world, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="journal"):
        study_merge.undo(world.graph, DB, [str(tmp_path / "empty")])
    assert world.graph.calls == []


def test_undo_reports_a_legacy_investigation_that_is_gone(world, tmp_path):
    """The nightly deletes an Investigation node SEEK lacks once no Study holds it, so the legacy Investigation a
    merge_other_investigation left empty can be gone by the time of an undo: L comes back with no IN_INVESTIGATION,
    and the undo says so."""
    legacy_inv = world.graph.add_investigation(901, "Alder Investigation")
    _seek(world, 3, "Cedar Unpublished", inv=101)
    legacy = world.graph.add_study(id=3, title="Cedar Unpublished", DOI="", investigation=legacy_inv)
    world.graph.add_study(seek_study_id=3, title="Cedar Unpublished", investigation=world.inv[101])
    study_merge.apply(world.graph, DB, {3: "merge_other_investigation"}, run_dir=str(tmp_path / "m1"))
    del world.graph.investigations[legacy_inv]
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert result["status"] == "partial"
    assert result["investigation_not_restored"] == [
        {"study_id": 3, "node": "legacy", "investigation": {"id": 901, "title": "Alder Investigation"}}]
    assert world.graph.in_investigation[legacy] == []


def test_an_on_both_source_whose_link_was_removed_since_is_not_linked_again(world, tmp_path):
    """SEEK moved 1002 (only on K) and 1003 (on both) to study 12 after the merge, and the rebuild removed their
    links to study 1; its archive is not given. Neither is linked to study 1 again: an "on both" source goes back to K
    only while it still links to L, as an "only on K" one does."""
    _split(world)
    _seek(world, 12, "Larch Study")
    world.graph.add_study(seek_study_id=12, title="Larch Study", investigation=world.inv[101])
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    world.links = [(1001, 1), (1002, 12), (1003, 12)]
    study_links.rebuild_in_study(world.graph, DB, remove=True, run_dir=str(tmp_path / "s1"))
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert result["status"] == "ok"
    assert world.graph.keys_of(1002) == world.graph.keys_of(1003) == {("seek", 12)}


def test_undo_refuses_unless_given_every_merge_journal_naming_its_ids(world, tmp_path):
    """A crash and its rerun into a new run directory: an undo given only the rerun's directory would leave what the
    crashed run moved on the legacy node. With the run root, it finds the other journal and refuses, writing
    nothing; given both, it restores the split exactly."""
    _split(world, on_l=(1001,), on_k=(1002, 1004), on_both=(1003,))
    before = _snapshot(world.graph)
    world.graph.fail_moves_after = 1
    with pytest.raises(RuntimeError):
        study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "merge_studies-1"), batch=1)
    world.graph.fail_moves_after = None
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "merge_studies-2"), batch=1)
    (tmp_path / "unrelated").mkdir()
    writes = len(world.graph.writes())
    with pytest.raises(ValueError, match="merge_studies-1"):
        study_merge.undo(world.graph, DB, [str(tmp_path / "merge_studies-2")], run_root=str(tmp_path))
    assert len(world.graph.writes()) == writes
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "merge_studies-2"),
                                               str(tmp_path / "merge_studies-1" / study_merge.JOURNAL_FILE)],
                              run_root=str(tmp_path))
    assert result["status"] == "ok" and _snapshot(world.graph) == before


def _retire(g, sample_id):
    eid = f"s:{sample_id}"
    for edge in [e for e, (s, _) in g.in_study.items() if s == eid]:
        del g.in_study[edge]
    del g.sources[eid]
    return eid


def test_undo_moves_back_only_the_node_it_journaled(world, tmp_path):
    """Neo4j hands a freed element id to a new node. 1003 (on both) is retired after the merge and an Attribute node
    takes its element id: the undo matches each source by element id, id and labels, so it links nothing to the
    Attribute and reports the source as replaced."""
    _split(world)
    study_merge.apply(world.graph, DB, {1: "merge"}, run_dir=str(tmp_path / "m1"))
    eid = _retire(world.graph, 1003)
    world.graph.sources[eid] = {"labels": {"Attribute"}, "id": None}
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert [st for s, st in world.graph.in_study.values() if s == eid] == []
    entry = result["studies"][0]
    assert entry["sources_replaced"] == [{"element_id": eid, "id": 1003, "labels": ["Sample"]}]
    assert world.graph.keys_of(1002) == {("seek", 1)} and world.graph.keys_of(1001) == {("id", 1)}


def test_undo_links_no_study_to_an_investigation_that_took_the_journaled_ones_element_id(world, tmp_path):
    """The nightly deleted the legacy Investigation a merge_other_investigation left empty, and a new Investigation
    took its element id: the undo matches the Investigation by element id and id, so L stays under none and the
    undo names the Investigation it could not restore."""
    legacy_inv = world.graph.add_investigation(901, "Alder Investigation")
    _seek(world, 3, "Cedar Unpublished", inv=101)
    legacy = world.graph.add_study(id=3, title="Cedar Unpublished", DOI="", investigation=legacy_inv)
    world.graph.add_study(seek_study_id=3, title="Cedar Unpublished", investigation=world.inv[101])
    study_merge.apply(world.graph, DB, {3: "merge_other_investigation"}, run_dir=str(tmp_path / "m1"))
    world.graph.investigations[legacy_inv] = {"id": 41, "title": "Juniper Investigation"}
    result = study_merge.undo(world.graph, DB, [str(tmp_path / "m1")])
    assert world.graph.in_investigation[legacy] == []
    assert result["investigation_not_restored"] == [
        {"study_id": 3, "node": "legacy", "investigation": {"id": 901, "title": "Alder Investigation"}}]
