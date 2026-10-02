"""IN_STUDY follows SEEK (nextseek_api/graph_sync/study_links.py).

No database and no Neo4j: ``StudyGraph`` stands in for the graph, and the SEEK readers of ``sources`` answer from a
list of (sample id, study id) links and a few rows. The graph-write lock is a recorder.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import sources, state, study_links, writer
from nextseek_api.tests.graph_sync_study_fakes import StudyGraph

DB = "neo4j"


@pytest.fixture
def seek(monkeypatch):
    """SEEK's side: ``links`` (sample id, study id), ``studies``, ``investigations``, ``investigation_projects`` and
    ``projects`` rows. Tests change them before a call."""
    world = SimpleNamespace(links=[], studies=[], investigation_projects=[], projects=[],
                            investigations=[{"id": 101, "title": "Alder Investigation", "description": None},
                                            {"id": 102, "title": "Birch Investigation", "description": None}])
    monkeypatch.setattr(sources, "iter_seek_study_links", lambda: iter(sorted(world.links)))
    monkeypatch.setattr(sources, "studies", lambda: [dict(s) for s in world.studies])
    monkeypatch.setattr(sources, "investigations", lambda: [dict(i) for i in world.investigations])
    monkeypatch.setattr(sources, "investigation_projects", lambda: [dict(r) for r in world.investigation_projects])
    monkeypatch.setattr(sources, "projects", lambda: [dict(r) for r in world.projects])
    return world


@pytest.fixture
def lock(monkeypatch):
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


def _graph():
    """Studies 1 and 2 (SEEK-keyed), a graph-only paper (id 9), and samples 1001 to 1006 in every state of the rule."""
    g = StudyGraph()
    inv = g.add_investigation(101, "Alder Investigation")
    nodes = {"s1": g.add_study(seek_study_id=1, title="Alder", investigation=inv),
             "s2": g.add_study(seek_study_id=2, title="Birch", investigation=inv),
             "paper": g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=inv)}
    for sid in range(1001, 1007):
        g.add_sample(sid)
    g.link(1001, nodes["s1"])                    # SEEK: 1 -> already right
    g.link(1002, nodes["s2"])                    # SEEK: 1 -> missing 1, stale 2
    g.link(1003, nodes["s1"])                    # SEEK: none -> kept, reported
    g.link(1004, nodes["paper"])                 # SEEK: 2 -> paper sample, 2 (its own investigation) withheld
    g.link(1004, nodes["s1"])                    # ...and its stale link to 1 goes
    g.link(g.add_sample(1005, label="OrphanSample"), nodes["s2"])   # never read
    return g, nodes


SEEK_LINKS = [(1001, 1), (1002, 1), (1004, 2), (1006, 2)]
SEEK_STUDIES = [{"id": 1, "title": "Alder", "description": None, "investigation_id": 101},
                {"id": 2, "title": "Birch", "description": "About birch", "investigation_id": 101}]


# --- the switch ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, follows", [("follow", True), (" FOLLOW ", True), ("Follow\n", True), ("add", False),
                                            ("", False), ("yes", False), ("apply", False), (None, False)])
def test_follows_seek_reads_the_switch(value, follows):
    env = {} if value is None else {study_links.SWITCH_ENV: value}
    assert study_links.follows_seek(env) is follows
    assert study_links.switch_value(env) == ("follow" if follows else "add")


def test_follows_seek_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.delenv(study_links.SWITCH_ENV, raising=False)
    assert study_links.follows_seek() is False
    monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    assert study_links.follows_seek() is True


# --- the diff ------------------------------------------------------------------------------------------------------

def test_diff_names_every_sample_that_breaks_the_rule_or_is_reported(seek):
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    seek.studies = list(SEEK_STUDIES)
    stats = {}
    found = {d.sample_id: d for d in study_links.diff_in_study(g, DB, stats=stats)}

    assert set(found) == {1002, 1003, 1004, 1006}
    assert found[1006].add == (2,) and found[1006].remove == () and found[1006].breaks_rule
    assert found[1002].add == (1,) and [l["seek_study_id"] for l in found[1002].remove] == [2]
    assert found[1002].breaks_rule and not found[1002].paper and not found[1002].no_seek_study
    assert found[1003].no_seek_study and found[1003].add == () and found[1003].remove == ()
    assert not found[1003].breaks_rule
    assert found[1004].paper and found[1004].add == () and found[1004].withheld == (2,)
    assert [l["seek_study_id"] for l in found[1004].remove] == [1] and not found[1004].investigation_unknown
    assert stats["samples_read"] == 6
    assert all(c.read for c in g.calls)


def test_seek_links_of_samples_with_no_node_are_skipped(seek):
    g, _ = _graph()
    seek.links = [(5, 1), (1001, 1), (1050, 2), (99999, 1)]    # below, between and above every graph id
    found = {d.sample_id for d in study_links.diff_in_study(g, DB, page=2)}
    assert found == {1002, 1003, 1004}


def test_a_seek_stream_out_of_sample_order_raises(seek, monkeypatch):
    g, _ = _graph()
    monkeypatch.setattr(sources, "iter_seek_study_links", lambda: iter([(1002, 1), (1001, 1)]))
    with pytest.raises(RuntimeError, match="out of sample-id order"):
        list(study_links.diff_in_study(g, DB))


# --- the rebuild ---------------------------------------------------------------------------------------------------

def test_rebuild_removes_archives_adds_and_withholds(seek, lock, tmp_path):
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    seek.studies = list(SEEK_STUDIES)
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path))

    assert report["status"] == "ok"
    assert g.keys_of(1001) == {("seek", 1)}
    assert g.keys_of(1002) == {("seek", 1)}
    assert g.keys_of(1003) == {("seek", 1)}
    assert g.keys_of(1004) == {("id", 9)}
    assert g.keys_of(1006) == {("seek", 2)}
    assert g.keys_of(1005, label="OrphanSample") == {("seek", 2)}
    archive = tmp_path / study_links.ARCHIVE_FILE
    assert report["archive_path"] == str(archive)
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 3      # header + 2 removed links
    assert (report["samples_read"], report["samples_differing"], report["to_add"], report["to_remove"]) == (6, 3, 2, 2)
    assert (report["paper_samples"], report["withheld"], report["kept_no_seek_study"]) == (1, 1, 1)
    assert (report["in_study_added"], report["in_study_removed"], report["orphan_in_study"]) == (2, 2, 1)
    assert g.studies[g.studies_by_seek(2)[0]]["description"] == "About birch"
    assert lock.timeouts == []                                              # lock=None: the caller holds it


def test_rebuild_gives_every_seek_study_a_node_with_its_investigation_first(seek, lock, tmp_path):
    """A SEEK study with no sample gets its node too, and a study in an investigation the graph lacks is linked
    to it: the investigation's node is written first."""
    g, _ = _graph()
    seek.studies = list(SEEK_STUDIES) + [{"id": 5, "title": "Elm Study", "description": None,
                                          "investigation_id": 102}]
    seek.investigation_projects = [{"investigation_id": 102, "project_id": 3}]
    seek.projects = [{"id": 3, "title": "Elm"}]
    g.add_project(3, "Elm")
    report = study_links.rebuild_in_study(g, DB, remove=False, run_dir=str(tmp_path))
    (elm,) = g.studies_by_seek(5)
    assert g.investigation_ids_of(elm) == [102] and g.inv_projects[g.investigation_by_id(102)] == {3}
    assert (report["seek_studies"], report["investigations_written"], report["seek_study_investigation_missing"]) == (
        3, 2, 0)
    writes = [c.query for c in g.writes()]
    assert writes.index(q.MERGE_INVESTIGATIONS) < writes.index(q.MERGE_SEEK_STUDIES)
    assert q.DELETE_INVESTIGATION_IN_PROJECT not in [c.query for c in g.calls]


def test_rebuild_without_remove_archives_and_deletes_nothing(seek, lock, tmp_path):
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    report = study_links.rebuild_in_study(g, DB, remove=False, run_dir=str(tmp_path))
    assert g.keys_of(1002) == {("seek", 1), ("seek", 2)}
    assert g.keys_of(1004) == {("id", 9), ("seek", 1)}
    assert report["in_study_removed"] == 0 and report["to_remove"] == 2
    assert not (tmp_path / study_links.ARCHIVE_FILE).exists()


def test_a_dry_run_writes_nothing(seek, lock, tmp_path):
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path), dry_run=True)
    assert g.writes() == [] and list(tmp_path.iterdir()) == []
    assert report["dry_run"] is True and report["to_add"] == 2 and report["in_study_added"] == 0


def test_rebuild_refuses_a_graph_where_two_nodes_share_a_seek_study_id(seek, lock, tmp_path):
    g, _ = _graph()
    g.add_study(seek_study_id=1, title="Alder again")
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path))
    assert report["status"] == "refused" and report["seek_study_id_duplicates"] == [{"seek_study_id": 1, "nodes": 2}]
    assert g.writes() == []


def test_rebuild_never_checks_the_schema_version(seek, lock, tmp_path):
    """The full sync runs this step before it writes GraphMeta: a fresh install, an upgrade, a rollback."""
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path))
    assert report["status"] == "ok"
    assert q.READ_GRAPHMETA not in [c.query for c in g.calls]


def test_lock_chunk_takes_the_lock_for_the_nodes_and_per_chunk(seek, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "REL_CHUNK", 1)
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    held_at_write = []
    g.before_write = lambda query, params: held_at_write.append(lock.held)
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path), lock="chunk",
                                          lock_timeout_s=7)
    assert report["status"] == "ok"
    assert lock.timeouts == [7, 7, 7, 7]           # the nodes, then samples 1002, 1004 and 1006, one chunk each
    assert held_at_write and all(held_at_write)


def test_lock_chunk_stops_with_lock_timeout_when_a_chunk_cannot_take_it(seek, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "REL_CHUNK", 1)
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    lock.outcomes = [True, False]
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path), lock="chunk")
    assert report["status"] == "lock_timeout" and report["stopped_at"] == "in_study"
    assert g.keys_of(1004) == {("id", 9), ("seek", 1)}      # never reached


def test_each_chunks_archive_is_flushed_before_its_write(seek, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "REL_CHUNK", 1)
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    archive = tmp_path / study_links.ARCHIVE_FILE
    sizes = []
    g.before_write = lambda query, params: (sizes.append(len(archive.read_text().splitlines()))
                                            if query == q.REPLACE_SEEK_IN_STUDY else None)
    study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path))
    assert sizes == [2, 3, 3]                      # 1002 and 1004 remove a link each; 1006 only adds


def test_lock_mode_must_be_none_or_chunk(seek, tmp_path):
    g, _ = _graph()
    with pytest.raises(ValueError):
        study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path), lock="run")


def test_preview_uses_the_switch_and_writes_nothing(seek, lock, monkeypatch):
    g, _ = _graph()
    seek.links = list(SEEK_LINKS)
    monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    report = study_links.preview_in_study(g, DB)
    assert report["remove"] is True and report["dry_run"] is True and g.writes() == []


# --- a paper sample links other investigations' studies (operator ruling SHARED SAMPLES) ----------------------------

def _two_investigation_world(seek):
    g = StudyGraph()
    alder = g.add_investigation(101, "Alder Investigation")
    birch = g.add_investigation(102, "Birch Investigation")
    g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=alder)
    g.add_study(seek_study_id=3, title="Birch Study", investigation=birch)
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=alder)
    g.add_sample(1003)
    g.link(1003, paper)
    seek.studies = [{"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101},
                    {"id": 3, "title": "Birch Study", "description": None, "investigation_id": 102}]
    seek.links = [(1003, 1), (1003, 3)]
    return g


def test_a_paper_sample_gains_another_investigations_study_and_the_rule_then_holds(seek, lock, tmp_path):
    g = _two_investigation_world(seek)
    (found,) = study_links.diff_in_study(g, DB)
    assert (found.add, found.withheld, found.paper, found.breaks_rule) == ((3,), (1,), True, True)

    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path))
    assert g.keys_of(1003) == {("id", 9), ("seek", 3)}
    assert (report["in_study_paper_links_written"], report["withheld"]) == (1, 1)
    (again,) = study_links.diff_in_study(g, DB)
    assert (again.add, again.breaks_rule, again.paper) == ((), False, True)


def test_a_shared_link_seek_stopped_holding_is_removed_only_with_remove(seek, lock, tmp_path):
    g = _two_investigation_world(seek)
    study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path / "a"))
    seek.links = [(1003, 1)]
    kept = study_links.rebuild_in_study(g, DB, remove=False, run_dir=str(tmp_path / "b"))
    assert kept["to_remove"] == 1 and g.keys_of(1003) == {("id", 9), ("seek", 3)}
    study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path / "c"))
    assert g.keys_of(1003) == {("id", 9)}
    assert (tmp_path / "c" / study_links.ARCHIVE_FILE).exists()


def test_a_paper_whose_investigation_seek_lacks_withholds_everything_and_is_counted(seek, lock, tmp_path):
    g = _two_investigation_world(seek)
    seek.investigations = [{"id": 102, "title": "Birch Investigation", "description": None}]
    report = study_links.rebuild_in_study(g, DB, remove=True, run_dir=str(tmp_path))
    assert g.keys_of(1003) == {("id", 9)}
    assert (report["paper_investigation_unknown"], report["withheld"]) == (1, 2)
