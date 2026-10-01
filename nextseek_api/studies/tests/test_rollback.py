"""rollback_study_moves (tool spec 7.7): reverse order, rows back with their ids, what changed since reported."""
import json

import pytest
from seek.models import Assays_internal_assays

from nextseek_api.graph_sync import paper_studies, targeted, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.studies import apply as a
from nextseek_api.studies import rollback
from nextseek_api.studies.journal import JOURNAL_FILE, Journal
from nextseek_api.studies.models import StudyTarget
from nextseek_api.studies.tests.conftest import apply_to_world, journal_events, links_of, outbox_of, rows_of

ORIGINAL = [(101, 1, 1), (101, 2, 2), (101, 3, 2), (102, 1, 1), (102, 4, 2), (301, 6, 1)]


@pytest.fixture
def undo_env(apply_env, monkeypatch):
    calls = []
    env = apply_env
    env.calls = calls
    env.original_meta = dict(apply_env.meta.metadata)
    monkeypatch.setattr(targeted, "_refusal", lambda d, db: None)
    monkeypatch.setattr(targeted, "preview_labels", lambda d, db, ids: calls.append(("preview", list(ids))) or [])
    monkeypatch.setattr(targeted, "sync_samples",
                        lambda d, db, ids, *, run_dir=None, apply_label_changes=False, **kw:
                        calls.append(("sync", list(ids), apply_label_changes)) or {"status": targeted.OK})
    monkeypatch.setattr(paper_studies, "restore_paper_links",
                        lambda d, db, graph_dir: calls.append(("restore", graph_dir.name))
                        or {"study_nodes_restored": 0, "paper_links_restored": 0})
    env.rollback = lambda run_dir, **kw: rollback.rollback_study_moves(run_dir, env.session, None, "neo4j",
                                                                       **{"confirm": True, **kw})
    return env


def _applied(env):
    run_dir, plan = env.make()
    assert env.apply(run_dir).status == a.DONE
    return run_dir, plan


@pytest.mark.django_db
def test_without_confirm_it_lists_and_writes_nothing(undo_env):
    run_dir, _plan = _applied(undo_env)
    before = journal_events(run_dir)
    result = undo_env.rollback(run_dir, confirm=False)
    assert result.status == a.DONE and "dry run" in result.message
    assert result.counts["would_undo"]["units"] == [1]
    assert result.counts["would_undo"]["clones"] == [302] and result.counts["would_undo"]["studies"] == [100]
    assert journal_events(run_dir) == before and undo_env.session.deleted == []
    assert undo_env.calls == [("preview", [2, 3])]


@pytest.mark.django_db
def test_rollback_undoes_the_run_in_reverse(undo_env):
    run_dir, _plan = _applied(undo_env)
    result = undo_env.rollback(run_dir)
    assert (result.status, result.exit_code) == (a.DONE, 0)
    assert sorted(links_of(undo_env.engine)) == sorted(ORIGINAL)
    assert (3, 101, 3) in rows_of(undo_env.engine)
    assert Assays_internal_assays.objects.filter(assay_id=302).count() == 0
    assert undo_env.session.deleted == [("assay", 302), ("study", 100)]
    assert undo_env.meta.metadata[2] == undo_env.original_meta[2]
    assert undo_env.meta.metadata[3] == undo_env.original_meta[3]
    assert outbox_of(undo_env.engine)[-1] == ("samples", "batch:studies:run-1:undo:1", [2, 3])
    keys = {(r.kind, r.key) for r in GraphSyncOutbox.objects.all()}
    assert ("samples", "batch:studies:run-1:undo:pubs:0") in keys and ("isa", "*") in keys
    assert undo_env.calls == [("sync", [2, 3], True)]   # study 100's node stays: Study nodes are not deleted
    parts = [json.loads(l)["part"] for l in (run_dir / JOURNAL_FILE).read_text().splitlines()
             if json.loads(l)["step"] == "undo" and json.loads(l)["event"] == "done"]
    assert parts == ["pubs", "unit", "map", "clone", "study", "sync", "run"]


@pytest.mark.django_db
def test_after_the_graph_step_the_paper_links_come_back_first(undo_env):
    run_dir, _plan = _applied(undo_env)
    (run_dir / a.GRAPH_DIR).mkdir()
    (run_dir / a.GRAPH_DIR / paper_studies.IN_STUDY_REMOVED_FILE).write_text(writer.IN_STUDY_ARCHIVE_HEADER)
    undo_env.rollback(run_dir)
    assert undo_env.calls[0] == ("restore", "graph")


@pytest.mark.django_db
def test_a_row_changed_since_apply_is_reported_not_restored(undo_env):
    run_dir, _plan = _applied(undo_env)
    with undo_env.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE assay_assets SET asset_id = 9 WHERE assay_id = 302 AND asset_id = 3")
    result = undo_env.rollback(run_dir)
    assert result.counts["units"][0]["not_deleted_changed"] and ("assay", 302) not in undo_env.session.deleted
    assert any(item[1] == 302 for item in result.counts["not_deleted"])


@pytest.mark.django_db
def test_a_delete_seek_refuses_is_listed_for_the_operator(undo_env):
    run_dir, _plan = _applied(undo_env)
    undo_env.session.refuse_delete = {302}
    result = undo_env.rollback(run_dir)
    assert result.status == a.DONE and ["assay", 302, 422] in [list(x) for x in result.counts["not_deleted"]]
    assert "delete them in SEEK" in result.message


@pytest.mark.django_db
def test_a_run_a_later_run_built_on_is_refused(undo_env):
    run_dir, plan = _applied(undo_env)
    apply_to_world(undo_env.world, plan)
    later, later_plan = undo_env.make(StudyTarget(key="sheet:7:paper one", investigation_id=7, title="Paper One",
                                                  seek_study_id=100, sample_ids=[2]), name="run-2")
    Journal(later / JOURNAL_FILE, run_id="run-2").append("run", "start", login="operator", person_id=42)
    result = undo_env.rollback(run_dir)
    assert result.status == a.REFUSED and "run-2" in result.message
    assert rollback.later_runs_using(run_dir, {302}) == "run-2"


@pytest.mark.django_db
def test_a_rolled_back_run_is_closed(undo_env):
    run_dir, _plan = _applied(undo_env)
    undo_env.rollback(run_dir)
    assert undo_env.apply(run_dir).status == a.REFUSED
    assert a.graph_step(run_dir, None, "neo4j", approve_label_changes=True).status == a.REFUSED


@pytest.mark.django_db
def test_an_investigation_rollback_keys_its_publication_rows_by_the_investigation(undo_env):
    run_dir, _plan = _applied(undo_env)
    undo_env.rollback(run_dir, investigation=7)
    keys = {(r.kind, r.key) for r in GraphSyncOutbox.objects.all()}
    assert ("samples", "batch:studies:run-1:undo:pubs:7:0") in keys
