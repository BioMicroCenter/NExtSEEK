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
from nextseek_api.studies.tests.conftest import (FakeDriver, FakeReader, apply_to_world, journal_events, links_of,
                                                 outbox_of, rows_of, truncate_journal_after)

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
                        lambda d, db, graph_dir, **kw: calls.append(("restore", graph_dir.name))
                        or {"study_nodes_restored": 0, "paper_links_restored": 0})
    env.rollback = lambda run_dir, **kw: rollback.rollback_study_moves(
        run_dir, env.session, None, "neo4j", **{"confirm": True, "reader": FakeReader(env.world), **kw})
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
    # the live labels are checked first; study 100's node stays: Study nodes are not deleted
    assert undo_env.calls == [("preview", [2, 3]), ("sync", [2, 3], True)]
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


# --- one investigation at a time, partial applies, and what SEEK keeps ---------------------------------------------

from nextseek_api.graph_sync import cypher as q  # noqa: E402
from nextseek_api.studies.journal import journal_state, read_journal  # noqa: E402

REAL_RESTORE = paper_studies.restore_paper_links
T7 = StudyTarget(key="sheet:7:paper one", investigation_id=7, title="Paper One", description="About paper one",
                 doi="10.0000/one", pmid="1111", sample_ids=[3])
T8 = StudyTarget(key="sheet:8:paper two", investigation_id=8, title="Paper Two", description="About paper two",
                 doi="10.0000/two", pmid="2222", sample_ids=[6])


def _state(run_dir):
    return journal_state(read_journal(run_dir / JOURNAL_FILE)[0])


def _mapping(assay_id):
    return Assays_internal_assays.objects.filter(assay_id=assay_id).count()


def _clone_of(run_dir, key):
    return [v["seek_id"] for (k, _a), v in _state(run_dir).clones.items() if k == key][0]


@pytest.mark.django_db
def test_two_investigations_roll_back_one_after_the_other(undo_env):
    run_dir, _plan = undo_env.make(T7, T8)
    assert undo_env.apply(run_dir).status == a.DONE
    c8 = _clone_of(run_dir, T8.key)
    assert undo_env.rollback(run_dir, investigation=7).status == a.DONE
    assert a.graph_step(run_dir, None, "neo4j", approve_label_changes=True, investigation=8).status == a.DONE
    assert undo_env.apply(run_dir, investigation=7).status == a.REFUSED
    r8 = undo_env.rollback(run_dir, investigation=8)
    assert r8.status == a.DONE and r8.counts["publications"]["restored"] == [6]
    assert ("assay", c8) in undo_env.session.deleted and _mapping(c8) == 0
    assert undo_env.meta.metadata[6] == undo_env.original_meta[6]
    assert sorted(links_of(undo_env.engine)) == sorted(ORIGINAL)


@pytest.mark.django_db
def test_a_whole_run_rollback_after_one_investigation_undoes_the_rest(undo_env):
    run_dir, _plan = undo_env.make(T7, T8)
    assert undo_env.apply(run_dir).status == a.DONE
    c8 = _clone_of(run_dir, T8.key)
    undo_env.rollback(run_dir, investigation=7)
    result = undo_env.rollback(run_dir)
    assert result.status == a.DONE and result.counts["not_deleted"] == []
    assert ("assay", c8) in undo_env.session.deleted and _mapping(c8) == 0
    assert undo_env.meta.metadata[6] == undo_env.original_meta[6]
    assert sorted(links_of(undo_env.engine)) == sorted(ORIGINAL)


@pytest.mark.django_db
def test_an_investigation_rollback_restores_only_its_own_papers(undo_env, monkeypatch):
    p7 = T7.model_copy(update={"key": "graph_only:57"})
    p8 = T8.model_copy(update={"key": "graph_only:58"})
    run_dir, _plan = undo_env.make(p7, p8)
    assert undo_env.apply(run_dir).status == a.DONE
    graph = run_dir / a.GRAPH_DIR
    graph.mkdir()
    (graph / paper_studies.STUDY_NODES_REMOVED_FILE).write_text("".join(json.dumps(
        {"study_id": n, "element_id": f"x{n}", "props": {"id": n}, "investigation_ids": [inv]}) + "\n"
        for n, inv in ((57, 7), (58, 8))))
    (graph / paper_studies.IN_STUDY_REMOVED_FILE).write_text(
        writer.IN_STUDY_ARCHIVE_HEADER + "3\t\t57\te1\t" + paper_studies.PAPER_PATH + "\n"
        + "6\t\t58\te2\t" + paper_studies.PAPER_PATH + "\n")
    seen = []
    driver = FakeDriver({
        q.RESTORE_PAPER_STUDY_NODES: lambda p: seen.append(("node", [r["study_id"] for r in p["rows"]]))
        or [{"restored": len(p["rows"])}],
        q.RESTORE_PAPER_IN_STUDY: lambda p: seen.append(("link", p["rows"])) or [{"restored": len(p["rows"])}],
    })
    monkeypatch.setattr(paper_studies, "restore_paper_links", REAL_RESTORE)
    result = rollback.rollback_study_moves(run_dir, undo_env.session, driver, "neo4j", confirm=True, investigation=7,
                                           reader=FakeReader(undo_env.world))
    assert result.status == a.DONE
    assert seen == [("node", [57]), ("link", [{"sample_id": 3, "study_id": 57}])]


@pytest.mark.django_db
def test_a_paper_link_of_a_sample_the_rollback_does_not_move_stays_retired(undo_env, monkeypatch):
    run_dir, _plan = undo_env.make(T7.model_copy(update={"key": "graph_only:57"}))
    assert undo_env.apply(run_dir).status == a.DONE
    graph = run_dir / a.GRAPH_DIR
    graph.mkdir()
    (graph / paper_studies.STUDY_NODES_REMOVED_FILE).write_text(json.dumps(
        {"study_id": 57, "element_id": "x", "props": {"id": 57}, "investigation_ids": [7]}) + "\n")
    (graph / paper_studies.IN_STUDY_REMOVED_FILE).write_text(   # sample 9 was already in the SEEK study
        writer.IN_STUDY_ARCHIVE_HEADER + "3\t\t57\te1\t" + paper_studies.PAPER_PATH + "\n"
        + "9\t\t57\te2\t" + paper_studies.PAPER_PATH + "\n")
    links_sent = []
    driver = FakeDriver({q.RESTORE_PAPER_STUDY_NODES: lambda p: [{"restored": len(p["rows"])}],
                         q.RESTORE_PAPER_IN_STUDY: lambda p: links_sent.extend(p["rows"])
                         or [{"restored": len(p["rows"])}]})
    monkeypatch.setattr(paper_studies, "restore_paper_links", REAL_RESTORE)
    rollback.rollback_study_moves(run_dir, undo_env.session, driver, "neo4j", confirm=True,
                                  reader=FakeReader(undo_env.world))
    assert links_sent == [{"sample_id": 3, "study_id": 57}]


@pytest.mark.django_db
def test_a_unit_committed_without_its_journal_line_is_recovered_and_undone(undo_env):
    run_dir, _plan = _applied(undo_env)
    truncate_journal_after(run_dir, "links", "prepared")    # the COMMIT happened; the crash beat the journal line
    result = undo_env.rollback(run_dir)
    assert result.status == a.DONE and [u["unit"] for u in result.counts["units"]] == [1]
    assert sorted(links_of(undo_env.engine)) == sorted(ORIGINAL) and _mapping(302) == 0
    assert ("assay", 302) in undo_env.session.deleted
    assert any(l.get("recovered") for l in read_journal(run_dir / JOURNAL_FILE)[0] if l["event"] == "committed")


@pytest.mark.django_db
def test_a_unit_in_neither_state_refuses_the_rollback_before_any_write(undo_env):
    with undo_env.engine.begin() as conn:     # the outbox row goes after the commit: the state decides
        conn.exec_driver_sql("INSERT INTO dmac.graph_sync_outbox (kind, key, payload, attempts) "
                             "VALUES ('samples', 'batch:studies:run-1:1', '[]', 0)")
    run_dir, _plan = _applied(undo_env)
    truncate_journal_after(run_dir, "links", "prepared")
    with undo_env.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM assay_assets WHERE assay_id = 302 AND asset_id = 2")
    before = journal_events(run_dir)
    result = undo_env.rollback(run_dir)
    assert result.status == a.REFUSED and journal_events(run_dir) == before and undo_env.session.deleted == []


@pytest.mark.django_db
def test_a_clone_still_holding_samples_keeps_its_mapping_rows(undo_env):
    run_dir, _plan = _applied(undo_env)
    with undo_env.engine.begin() as conn:   # sample 3's link re-made with a new id (an upload's delete and insert)
        conn.exec_driver_sql("DELETE FROM assay_assets WHERE assay_id = 302 AND asset_id = 3")
        conn.exec_driver_sql("INSERT INTO assay_assets (assay_id, asset_id, version, created_at, updated_at, "
                             "relationship_type_id, asset_type, direction) VALUES (302, 3, 1, '2026-01-02 00:00:00', "
                             "'2026-01-02 00:00:00', NULL, 'Sample', 2)")
    result = undo_env.rollback(run_dir)
    assert (302, 3, 2) in links_of(undo_env.engine) and _mapping(302) == 1
    assert any(item[1] == 302 for item in result.counts["not_deleted"])


def _seek_holds_its_sops(env):
    """SEEK's own rule for an assay delete: refused while the assay holds any asset, SOPs included."""
    s = env.session

    def delete_assay(assay_id):
        if s.assay_link_count(assay_id):
            return False, 422
        s.assays.pop(assay_id, None)
        s.deleted.append(("assay", assay_id))
        return True, 204

    s.delete_assay = delete_assay


@pytest.mark.django_db
def test_a_clone_that_copied_its_sources_sops_is_deleted(undo_env):
    _seek_holds_its_sops(undo_env)
    run_dir, _plan = _applied(undo_env)
    with undo_env.engine.begin() as conn:   # what SEEK wrote for the clone's copied SOP (the payload's sops)
        conn.exec_driver_sql("INSERT INTO assay_assets (assay_id, asset_id, version, asset_type) "
                             "VALUES (302, 7, 1, 'Sop')")
    result = undo_env.rollback(run_dir)
    assert result.counts["not_deleted"] == [] and undo_env.session.deleted == [("assay", 302), ("study", 100)]
    assert not any(r[1] == 302 for r in rows_of(undo_env.engine))
    assert ("undo", "intent") in journal_events(run_dir)
    sops = [l for l in read_journal(run_dir / JOURNAL_FILE)[0] if l.get("part") == "sops"]
    assert sops and sops[0]["rows"][0]["asset_type"] == "Sop"


@pytest.mark.django_db
def test_a_clone_kept_for_its_samples_keeps_its_sops(undo_env):
    _seek_holds_its_sops(undo_env)
    run_dir, _plan = _applied(undo_env)
    with undo_env.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO assay_assets (assay_id, asset_id, version, asset_type) "
                             "VALUES (302, 7, 1, 'Sop')")
        conn.exec_driver_sql("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                             "VALUES (302, 5, 'Sample', 1)")
    result = undo_env.rollback(run_dir)
    assert ["assay", 302, "not empty"] in [list(x) for x in result.counts["not_deleted"]]
    assert (302, 7) in {(r[1], r[2]) for r in rows_of(undo_env.engine)}


@pytest.mark.django_db
def test_a_study_whose_post_answer_was_lost_is_listed(undo_env):
    run_dir, _plan = undo_env.make()
    undo_env.session.script["study"] = ["late:100000", "lost", "lost"]
    assert undo_env.apply(run_dir).status == a.STOPPED
    undo_env.session.hidden.clear()     # Rails has finished the create by now
    result = undo_env.rollback(run_dir)
    assert ["study", 100, "answer lost: look in SEEK"] in [list(x) for x in result.counts["not_deleted"]]


@pytest.mark.django_db
def test_a_second_rollback_reads_seeks_404_as_gone(undo_env, monkeypatch):
    s = undo_env.session

    def gone_is_404(kind, store):
        def delete(seek_id):
            if seek_id not in store:
                return False, 404
            store.pop(seek_id)
            s.deleted.append((kind, seek_id))
            return True, 204
        return delete

    s.delete_assay, s.delete_study = gone_is_404("assay", s.assays), gone_is_404("study", s.studies)
    answers = iter([{"status": "lock_timeout"}])
    monkeypatch.setattr(targeted, "sync_samples", lambda d, db, ids, **kw: next(answers, {"status": targeted.OK}))
    run_dir, _plan = _applied(undo_env)
    assert undo_env.rollback(run_dir).status == a.STOPPED
    second = undo_env.rollback(run_dir)
    assert second.status == a.DONE and second.counts["not_deleted"] == []


@pytest.mark.django_db
def test_mapping_rows_a_crash_left_unjournaled_are_deleted_with_their_clone(undo_env, monkeypatch):
    from nextseek_api.studies import mapping

    real = mapping.insert_clone_mappings

    def commit_then_die(pairs):
        real(pairs)
        raise RuntimeError("the process died before map.done")

    monkeypatch.setattr(mapping, "insert_clone_mappings", commit_then_die)
    run_dir, _plan = undo_env.make()
    with pytest.raises(RuntimeError):
        undo_env.apply(run_dir)
    assert _mapping(302) == 1
    assert undo_env.rollback(run_dir).status == a.DONE
    assert ("assay", 302) in undo_env.session.deleted and _mapping(302) == 0


@pytest.mark.django_db
def test_a_run_that_never_started_is_refused(undo_env):
    run_dir, _plan = undo_env.make()
    result = undo_env.rollback(run_dir)
    assert result.status == a.REFUSED and undo_env.calls == []


@pytest.mark.django_db
def test_the_final_sync_covers_only_the_units_that_committed(undo_env):
    t2 = StudyTarget(key="sheet:7:paper two", investigation_id=7, title="Paper Two", sample_ids=[4])
    run_dir, _plan = undo_env.make(T7, t2)
    with undo_env.engine.begin() as conn:      # unit 2's source assay changes after the plan: unit 2 is refused
        conn.exec_driver_sql("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                             "VALUES (102, 5, 'Sample', 1)")
    assert undo_env.apply(run_dir).status == a.STOPPED
    undo_env.rollback(run_dir)
    assert [c for c in undo_env.calls if c[0] == "sync"] == [("sync", [2, 3], True)]


@pytest.mark.django_db
def test_a_later_run_rolled_back_for_another_investigation_still_blocks(undo_env):
    run_dir, plan = _applied(undo_env)
    apply_to_world(undo_env.world, plan)
    later, _ = undo_env.make(StudyTarget(key="sheet:7:paper one", investigation_id=7, title="Paper One",
                                         seek_study_id=100, sample_ids=[2]), name="run-2")
    jr = Journal(later / JOURNAL_FILE, run_id="run-2")
    jr.append("run", "start", login="operator", person_id=42)
    jr.append("undo", "done", part="run", investigation=8)
    assert rollback.later_runs_using(run_dir, {302}) == "run-2"


# --- the final approved sync writes only what the undo accounts for ----------------------------------------------

def _after_the_graph_step(env, plan):
    """The world as SEEK holds it after apply, and the graph labels the graph step wrote for edge 3 -> 2."""
    apply_to_world(env.world, plan)
    env.world.stored = [{"child_id": 3, "parent_id": 2, "stored": env.world.labels(3, 2)}]


def _live(properties):
    return lambda d, db, ids: [{"child_id": 3, "parent_id": 2, "element_id": "e0", "class": "changed",
                                "properties": list(properties), "stored": {}, "computed": {}}]


@pytest.mark.django_db
def test_the_dry_run_says_what_the_undo_implies_for_the_labels(undo_env):
    run_dir, plan = _applied(undo_env)
    _after_the_graph_step(undo_env, plan)
    dry = undo_env.rollback(run_dir, confirm=False)
    assert "before the undo" in dry.message
    assert dry.counts["undo_label_changes"] == {"changed": 1}


@pytest.mark.django_db
def test_the_final_sync_writes_the_labels_the_undo_implies(undo_env, monkeypatch):
    run_dir, plan = _applied(undo_env)
    _after_the_graph_step(undo_env, plan)
    monkeypatch.setattr(targeted, "preview_labels", _live(["assay_id"]))
    result = undo_env.rollback(run_dir)
    assert result.status == a.DONE and ("sync", [2, 3], True) in undo_env.calls


@pytest.mark.django_db
def test_the_final_sync_refuses_a_label_the_undo_does_not_imply(undo_env, monkeypatch):
    run_dir, plan = _applied(undo_env)
    _after_the_graph_step(undo_env, plan)
    monkeypatch.setattr(targeted, "preview_labels",
                        _live(["assay_id", "internal_assay_id", "internal_assay_title"]))
    result = undo_env.rollback(run_dir)
    assert result.status == a.STOPPED and not any(c[0] == "sync" for c in undo_env.calls)
    assert (run_dir / rollback.LABELS_OUTSIDE_UNDO_FILE).exists() and not (run_dir / a.GRAPH_DIR).exists()
    assert sorted(links_of(undo_env.engine)) == sorted(ORIGINAL)


@pytest.mark.django_db
def test_a_rollback_that_died_part_way_closes_the_run_to_apply(undo_env):
    run_dir, _plan = _applied(undo_env)
    assert undo_env.rollback(run_dir).status == a.DONE
    truncate_journal_after(run_dir, "undo", "intent")   # the first undo part started; the crash beat the rest
    assert undo_env.apply(run_dir).status == a.REFUSED
    assert a.graph_step(run_dir, None, "neo4j", approve_label_changes=True).status == a.REFUSED
