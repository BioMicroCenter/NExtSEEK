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
    assert entry["studies_preview"] == {"kept": 2, "leaves": 1, "no_seek_study": 1, "paper_samples": 1}
    assert entry["description_differs"] is False and entry["seek_description_empty"] is True
    assert report["approval_line"] == "1,5"
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
