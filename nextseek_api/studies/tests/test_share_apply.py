"""A share's apply, one call at a time, and the worker's passes (tool spec 16.4, 16.6; T39, T40). The fixture is
conftest's share_env."""
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from seek.models import Assays_internal_assays

from nextseek_api.graph_sync import paper_studies, targeted
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.studies import apply as apply_mod
from nextseek_api.studies import links, preflight, rollback, share_apply, share_jobs
from nextseek_api.studies.journal import JOURNAL_FILE, read_journal
from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.models_db import SampleShare
from nextseek_api.studies.seek import ADOPT_POLL_S, ADOPT_WAIT_S, SeekError
from nextseek_api.studies.tests.conftest import (PASSWORD, T0, U3, U4, U5, AssayRow, FakeReader, add_sample, links_of,
                                                 outbox_of, projects_of, seed, sqlite_connection, uid)


def _events(row):
    return [(l["step"], l["event"]) for l in read_journal(share_apply.run_dir_of(row) / JOURNAL_FILE)[0]]


def test_the_worker_plans_a_planning_share_into_its_run_directory(share_env):
    row = share_env.planned(U3)
    assert row.state == "planned" and len(row.plan_sha256) == 64 and row.run_dir.endswith("-share")
    assert (share_apply.run_dir_of(row) / "share.json").exists()
    assert row.summary["outcomes"]["shared"] == 1 and row.summary["plan_sha256"] == row.plan_sha256


def test_a_whole_share_refusal_and_a_failed_plan_end_planning(share_env, monkeypatch):
    row = share_jobs.create_share(ShareInput(sample_uids=[U3], source_project_id=3, destination_project_id=3,
                                             destination_study_id=40, created_at="t"), share_env.user)
    share_jobs.claim(row, "w1")
    assert share_apply.plan_job(row, "w1", reader=FakeReader(share_env.world)) == "refused"
    assert SampleShare.objects.get(pk=row.pk).error["code"] == "same_project"
    monkeypatch.setattr(share_apply, "plan_share", lambda *a, **k: 1 / 0)
    row = share_jobs.create_share(ShareInput(sample_uids=[U3], source_project_id=3, destination_project_id=5,
                                             destination_study_id=40, created_at="t"), share_env.user)
    share_jobs.claim(row, "w1")
    assert share_apply.plan_job(row, "w1", reader=FakeReader(share_env.world)) == "plan_failed"


def test_the_wrong_state_answers_409_naming_it(share_env):
    row = share_jobs.create_share(ShareInput(sample_uids=[U3], source_project_id=3, destination_project_id=5,
                                             destination_study_id=40, created_at="t"), share_env.user)
    answer = share_env.step(row, sha="a" * 64)
    assert (answer.status_code, answer.code) == (409, "share_not_applicable") and "planning" in answer.message


def test_a_different_plan_sha_answers_409_plan_changed(share_env):
    answer = share_env.step(share_env.planned(U3), sha="b" * 64)
    assert (answer.status_code, answer.code) == (409, "plan_changed")


def test_a_plan_with_no_work_answers_409_nothing_to_apply(share_env):
    share_env.world.links += [(401, 4, 2), (401, 1, 1)]
    share_env.world.sample_projects[4] |= {5}
    share_env.world.sample_projects[1] |= {5}
    answer = share_env.step(share_env.planned(U4))
    assert (answer.status_code, answer.code) == (409, "nothing_to_apply")


def test_a_busy_run_lock_answers_409_busy(share_env, monkeypatch):
    @contextmanager
    def busy():
        yield False

    monkeypatch.setattr(preflight, "run_lock", busy)
    assert share_env.step(share_env.planned(U3)).code == "busy"


def test_each_call_makes_at_most_one_post_then_maps_and_queues(share_env):
    row = share_env.planned(U3, U5)
    first = share_env.step(row)
    assert (first.status_code, first.state, first.clones_done, first.clones_remaining) == (200, "applying", 1, 1)
    assert len(share_env.session.posts) == 1 and _events(row)[0] == ("run", "start")
    second = share_env.step(row)
    assert (second.status_code, second.clones_remaining, len(share_env.session.posts)) == (200, 0, 2)
    third = share_env.step(row)
    assert (third.status_code, third.state) == (202, "queued") and len(share_env.session.posts) == 2
    assert sorted(Assays_internal_assays.objects.values_list("assay_id", "internal_assay_id")) == [
        (402, 903), (403, 900)]        # the groups in order: "proteomics run" before "rna-seq run"
    assert GraphSyncOutbox.objects.filter(kind="assay_map").exists()


def test_the_clone_takes_the_destination_studys_policy_even_when_seek_hides_it(share_env):
    del share_env.world.study_reps[40]["data"]["attributes"]["policy"]      # SEEK's GET to one who cannot manage D
    share_env.step(share_env.planned(U3))
    [payload] = share_env.session.payloads
    assert payload["data"]["attributes"]["policy"] == share_env.world.policies[40]
    assert payload["data"]["relationships"]["study"]["data"]["id"] == "40"


def test_a_reused_group_makes_no_post(share_env):
    answer = share_env.step(share_env.planned(U4))
    assert (answer.status_code, answer.state) == (202, "queued") and share_env.session.posts == []


def test_a_lost_answer_is_unknown_then_adopted_on_one_lookup(share_env):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = ["late:0"]
    first = share_env.step(row)
    assert (first.status_code, first.code, first.retry_after_s) == (202, share_apply.CLONE_OUTCOME_UNKNOWN,
                                                                    ADOPT_POLL_S)
    second = share_env.step(row)
    assert (second.status_code, second.state) == (202, "queued") and len(share_env.session.posts) == 1
    assert ("clone", "adopted") in _events(row)


def test_nothing_found_waits_and_posts_again_only_after_the_wait(share_env):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = ["lost"]
    share_env.now = datetime.now(timezone.utc)
    assert share_env.step(row).code == share_apply.CLONE_OUTCOME_UNKNOWN
    share_env.now += timedelta(seconds=ADOPT_POLL_S)
    assert share_env.step(row).code == share_apply.CLONE_OUTCOME_UNKNOWN and len(share_env.session.posts) == 1
    share_env.now += timedelta(seconds=ADOPT_WAIT_S)
    answer = share_env.step(row)
    assert (answer.status_code, len(share_env.session.posts)) == (200, 2)


@pytest.mark.parametrize("status, code, http", [(401, "seek_refused", 403), (403, "seek_refused", 403),
                                                (422, "seek_payload_rejected", 422), (503, "seek_error", 502)])
def test_seeks_answers_map_and_journal_a_definite_refusal(share_env, status, code, http):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = [SeekError("x", "SEEK said no", status)]
    answer = share_env.step(row)
    assert (answer.status_code, answer.code, answer.message) == (http, code, "SEEK said no")
    refused = [("clone", "failed")] if status < 500 else []      # a 5xx may still have made it: no outcome
    assert [e for e in _events(row) if e[0] == "clone"] == [("clone", "intent"), *refused]


def test_a_moved_destination_ends_the_share(share_env):
    row = share_env.planned(U3)
    share_env.world.studies[:] = [s for s in share_env.world.studies if s.id != 40]
    answer = share_env.step(row)
    assert (answer.status_code, answer.code) == (409, "destination_changed")
    assert SampleShare.objects.get(pk=row.pk).state == "apply_failed"


def test_the_unit_pass_links_and_ends_applied_with_the_receipt(share_env):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    assert share_env.unit(row) == "applied"
    row.refresh_from_db()
    assert row.state == "applied" and row.receipt["links_inserted"] == 2 and row.receipt["project_rows_added"] == 2
    assert {(a, s) for a, s, _d in links_of(share_env.engine) if a == 402} == {(402, 3), (402, 2)}
    assert {(5, 2), (5, 3)} <= set(projects_of(share_env.engine))
    assert ("samples", "batch:studies:" + row.run_dir + ":1", [2, 3]) in outbox_of(share_env.engine)


def test_a_digest_difference_ends_apply_failed_plan_stale(share_env):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    with share_env.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO projects_samples (project_id, sample_id) VALUES (5, 3)")
    assert share_env.unit(row) == "apply_failed"
    assert SampleShare.objects.get(pk=row.pk).error["code"] == "plan_stale"


def test_a_busy_lock_returns_the_share_to_queued(share_env, monkeypatch):
    row = share_env.planned(U4)
    share_env.step(row)

    @contextmanager
    def busy():
        yield False

    monkeypatch.setattr(preflight, "run_lock", busy)
    assert share_env.unit(row) == "queued"
    assert SampleShare.objects.get(pk=row.pk).state == "queued" and not any(
        a == 401 and s == 4 for a, s, _d in links_of(share_env.engine))


def test_the_password_never_reaches_the_journal_the_run_directory_the_row_or_logs(share_env, caplog):
    caplog.set_level(logging.DEBUG)
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    share_env.unit(row)
    for path in share_apply.run_dir_of(row).rglob("*"):
        if path.is_file():
            assert PASSWORD.encode("utf-8") not in path.read_bytes(), path.name
    stored = SampleShare.objects.get(pk=row.pk)
    assert PASSWORD not in json.dumps([stored.request, stored.summary, stored.receipt, stored.error], default=str)
    assert PASSWORD not in caplog.text


def test_a_share_rolls_back_through_the_tool(share_env, monkeypatch):
    calls = []
    monkeypatch.setattr(targeted, "sync_samples", lambda d, db, ids, **kw: calls.append(list(ids))
                        or {"status": targeted.OK})
    monkeypatch.setattr(paper_studies, "restore_paper_links", lambda *a, **kw: {})
    monkeypatch.setattr(targeted, "preview_labels", lambda d, db, ids: [])
    before = (links_of(share_env.engine), projects_of(share_env.engine))
    row = share_env.planned(U3, U4)
    share_env.step(row)
    share_env.step(row)
    assert share_env.unit(row) == "applied"
    result = rollback.rollback_study_moves(share_apply.run_dir_of(row), share_env.session, None, "neo4j",
                                           confirm=True, reader=FakeReader(share_env.world))
    assert result.status == "done"
    assert (sorted(links_of(share_env.engine)), projects_of(share_env.engine)) == (sorted(before[0]), before[1])
    assert share_env.session.deleted == [("assay", 402)] and calls == [[1, 2, 3, 4]]


def test_the_tools_apply_refuses_a_share_run(share_env):
    from nextseek_api.studies import apply as apply_mod

    row = share_env.planned(U3)
    result = apply_mod.apply_study_moves(share_apply.run_dir_of(row), share_env.session, None, "neo4j",
                                         reader=FakeReader(share_env.world))
    assert result.status == apply_mod.REFUSED and "sample-shares" in result.message
    assert share_env.session.posts == []


def _undo(env, row, monkeypatch):
    monkeypatch.setattr(targeted, "sync_samples", lambda d, db, ids, **kw: {"status": targeted.OK})
    monkeypatch.setattr(paper_studies, "restore_paper_links", lambda *a, **kw: {})
    monkeypatch.setattr(targeted, "preview_labels", lambda d, db, ids: [])
    return rollback.rollback_study_moves(share_apply.run_dir_of(row), env.session, None, "neo4j", confirm=True,
                                         reader=FakeReader(env.world))


def test_a_rolled_back_queued_share_ends_rolled_back_and_is_never_linked(share_env, monkeypatch):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    before = (links_of(share_env.engine), projects_of(share_env.engine))
    assert _undo(share_env, row, monkeypatch).status == "done" and share_env.session.deleted == [("assay", 402)]
    assert SampleShare.objects.get(pk=row.pk).state == "rolled_back"
    SampleShare.objects.filter(pk=row.pk).update(state="queued")      # the row's update lost: the journal decides
    assert share_env.unit(row) == "rolled_back"
    assert (links_of(share_env.engine), projects_of(share_env.engine)) == before
    assert SampleShare.objects.get(pk=row.pk).state == "rolled_back"


def test_an_apply_call_on_a_rolled_back_share_answers_409_and_posts_nothing(share_env, monkeypatch):
    row = share_env.planned(U3, U5)
    share_env.step(row)                                                # one clone of two
    _undo(share_env, row, monkeypatch)
    SampleShare.objects.filter(pk=row.pk).update(state="applying")
    answer = share_env.step(row)
    assert (answer.status_code, answer.code) == (409, "share_rolled_back") and len(share_env.session.posts) == 1
    assert SampleShare.objects.get(pk=row.pk).state == "rolled_back"
    assert (share_env.step(row).code, len(share_env.session.posts)) == ("share_not_applicable", 1)


def test_a_share_failed_for_a_stale_plan_is_never_queued_again(share_env):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    with share_env.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO projects_samples (project_id, sample_id) VALUES (5, 3)")
    assert share_env.unit(row) == "apply_failed"
    row.refresh_from_db()
    assert not share_jobs.to_queued(row) and not share_jobs.to_applying(row)
    answer = share_env.step(row)
    assert (answer.status_code, answer.code) == (409, "share_not_applicable") and "plan_stale" in answer.message
    assert SampleShare.objects.get(pk=row.pk).state == "apply_failed"


def _assays_in_40(env):
    return sorted(i for i, (study, _t) in env.session.assays.items() if study == 40)


def test_two_shares_planned_before_either_applies_make_one_destination_assay(share_env):
    add_sample(share_env.world, 7, assays=((101, 2),))
    seed(share_env.engine, share_env.world)
    first, second = share_env.planned(U3), share_env.planned(uid(7, kind="D.SEQ"))
    share_env.step(first)
    share_env.step(first)
    assert share_env.unit(first) == "applied"
    answer = share_env.step(second)
    assert (answer.status_code, answer.state) == (202, "queued") and len(share_env.session.posts) == 1
    assert ("clone", "adopted") in _events(second)
    assert share_env.unit(second) == "applied" and _assays_in_40(share_env) == [402]
    assert {s for a, s, _d in links_of(share_env.engine) if a == 402} == {2, 3, 7}


def test_a_group_assay_made_by_another_share_but_not_mapped_yet_answers_busy(share_env):
    add_sample(share_env.world, 7, assays=((101, 2),))
    seed(share_env.engine, share_env.world)
    first, second = share_env.planned(U3), share_env.planned(uid(7, kind="D.SEQ"))
    share_env.step(first)                                                  # 402 made, its mapping not yet written
    answer = share_env.step(second)
    assert (answer.status_code, answer.code) == (409, "busy") and len(share_env.session.posts) == 1
    share_env.step(first)
    assert share_env.step(second).state == "queued" and _assays_in_40(share_env) == [402]


def test_an_adopted_assay_already_holding_a_planned_link_ends_the_share_plan_stale(share_env):
    share_env.world.sample_projects[1] |= {5}           # project rows the first share does not change, so the
    share_env.world.sample_projects[2] |= {5}           # second's digest still matches: the clone check refuses it
    seed(share_env.engine, share_env.world)
    first, second = share_env.planned(U3), share_env.planned(uid(2, kind="D.SEQ"))
    share_env.step(first)
    share_env.step(first)
    assert share_env.unit(first) == "applied"
    assert share_env.step(second).state == "queued"                        # adopts 402, which holds sample 2
    assert share_env.unit(second) == "apply_failed"
    assert SampleShare.objects.get(pk=second.pk).error["code"] == "plan_stale"
    assert sorted(s for a, s, _d in links_of(share_env.engine) if a == 402) == [2, 3]
    assert _assays_in_40(share_env) == [402]


def _worker_dies_in_the_unit(env, row, monkeypatch, *, committed: bool):
    """The worker's unit pass with the outbox insert refused in the transaction (the after-commit fallback), killed
    right after its transaction commits or rolls back, before ``links.committed``; then the lease runs out."""
    from django.utils import timezone

    monkeypatch.setattr(links, "enqueue_samples_outbox", lambda conn, key, ids: False)

    @contextmanager
    def dying():
        with env.engine.connect() as conn:
            trans = conn.begin()
            yield conn
            trans.commit() if committed else trans.rollback()
        raise RuntimeError("worker killed")

    monkeypatch.setattr(apply_mod, "_connection", dying)
    with pytest.raises(RuntimeError):
        env.unit(row)
    monkeypatch.setattr(apply_mod, "_connection", lambda: sqlite_connection(env.engine))
    SampleShare.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert ("links", "prepared") in _events(row) and ("links", "committed") not in _events(row)


def _unit_rows(row):
    return sorted(GraphSyncOutbox.objects.filter(kind="samples", key__startswith="batch:studies:" + row.run_dir + ":1")
                  .values_list("key", flat=True))


def test_a_project_only_unit_rolled_back_by_a_crash_runs_again(share_env, monkeypatch):
    share_env.world.links += [(401, 4, 2), (401, 1, 1)]                    # every link there: project rows only
    seed(share_env.engine, share_env.world)
    row = share_env.planned(U4)
    assert share_env.step(row).state == "queued"
    _worker_dies_in_the_unit(share_env, row, monkeypatch, committed=False)
    assert not {(5, 1), (5, 4)} & set(projects_of(share_env.engine))
    assert share_env.unit(row) == "applied"
    assert {(5, 1), (5, 4)} <= set(projects_of(share_env.engine)) and _unit_rows(row) == [
        "batch:studies:" + row.run_dir + ":1"]


def test_a_unit_with_links_rolled_back_by_a_crash_runs_again(share_env, monkeypatch):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    _worker_dies_in_the_unit(share_env, row, monkeypatch, committed=False)
    assert share_env.unit(row) == "applied"
    assert {(a, s) for a, s, _d in links_of(share_env.engine) if a == 402} == {(402, 3), (402, 2)}
    assert {(5, 2), (5, 3)} <= set(projects_of(share_env.engine)) and len(_unit_rows(row)) == 1


def test_a_unit_committed_before_a_crash_is_recovered_and_its_outbox_row_enqueued(share_env, monkeypatch):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    _worker_dies_in_the_unit(share_env, row, monkeypatch, committed=True)
    assert _unit_rows(row) == []
    assert share_env.unit(row) == "applied"
    assert _unit_rows(row) == ["batch:studies:" + row.run_dir + ":1"] and ("links", "committed") in _events(row)


def test_the_rollback_preview_counts_the_project_rows_it_may_delete(share_env, monkeypatch):
    monkeypatch.setattr(targeted, "preview_labels", lambda d, db, ids: [])
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    share_env.unit(row)
    result = rollback.rollback_study_moves(share_apply.run_dir_of(row), share_env.session, None, "neo4j",
                                           confirm=False, reader=FakeReader(share_env.world))
    assert result.counts["would_undo"]["project_rows"] == 2 and SampleShare.objects.get(pk=row.pk).state == "applied"


# --- the smaller rules of one apply call and the worker ---------------------------------------------------------------

def test_a_clone_whose_answers_keep_getting_lost_stops_after_three_posts(share_env):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = ["lost"] * 5
    share_env.now = datetime.now(timezone.utc)
    answers = []
    for _ in range(4):
        answers.append(share_env.step(row))
        share_env.now += timedelta(seconds=ADOPT_WAIT_S + 1)
    assert [a.code for a in answers[:3]] == [share_apply.CLONE_OUTCOME_UNKNOWN] * 3
    assert (answers[3].status_code, answers[3].code, len(share_env.session.posts)) == (502, "seek_error", 3)
    assert "look in SEEK" in answers[3].message


def test_a_call_decides_on_the_row_it_reads_under_the_run_lock(share_env):
    row = share_env.planned(U4)
    stale = SampleShare.objects.get(pk=row.pk)
    assert share_jobs.to_applying(SampleShare.objects.get(pk=row.pk))          # another call moved it meanwhile
    answer = share_apply.apply_step(stale, share_env.session, None, "neo4j", plan_sha256=row.plan_sha256,
                                    reader=FakeReader(share_env.world), now=share_env.now)
    assert (answer.status_code, answer.state) == (202, "queued") and SampleShare.objects.get(pk=row.pk).state == "queued"


def test_a_definite_refusal_lets_the_next_call_post_at_once(share_env):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = [SeekError("forbidden", "no", 403)]
    assert share_env.step(row).status_code == 403
    answer = share_env.step(row)
    assert (answer.status_code, len(share_env.session.posts)) == (200, 2)
    assert [e for e in _events(row) if e[0] == "clone"] == [("clone", "intent"), ("clone", "failed"),
                                                           ("clone", "intent"), ("clone", "done")]


def test_a_plan_made_by_other_code_is_refused(share_env, monkeypatch):
    from nextseek_api.studies import planner

    row = share_env.planned(U3)
    monkeypatch.setattr(planner, "code_sha", lambda: "c" * 64)
    answer = share_env.step(row)
    assert (answer.status_code, answer.code) == (409, "plan_changed") and "other code" in answer.message
    assert share_env.session.posts == []


def test_assay_map_is_enqueued_again_on_a_resume(share_env):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    GraphSyncOutbox.objects.filter(kind="assay_map").delete()     # lost to a crash after the mapping was written
    SampleShare.objects.filter(pk=row.pk).update(state="applying")
    assert share_env.step(row).state == "queued" and GraphSyncOutbox.objects.filter(kind="assay_map").exists()


def test_a_box_not_ready_answers_409_not_ready(share_env, monkeypatch):
    monkeypatch.setattr(preflight, "_switch_follows", lambda: False)
    answer = share_env.step(share_env.planned(U3))
    assert (answer.status_code, answer.code) == (409, "not_ready") and share_env.session.posts == []


def test_two_assays_matching_a_lost_create_answer_409_ambiguous(share_env):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = ["lost"]
    share_env.step(row)
    share_env.session.assays.update({410: (40, "RNA-seq run"), 411: (40, "RNA-SEQ run")})
    answer = share_env.step(row)
    assert (answer.status_code, answer.code) == (409, "clone_outcome_ambiguous") and "410" in answer.message


def test_a_source_assay_seek_cannot_describe_answers_422_clone_payload_invalid(share_env):
    row = share_env.planned(U3)
    share_env.world.assay_reps[101] = {"data": {"attributes": {"title": None}}}
    answer = share_env.step(row)
    assert (answer.status_code, answer.code) == (422, "clone_payload_invalid") and share_env.session.posts == []


def test_a_planned_assay_of_the_study_is_never_adopted_for_a_lost_create(share_env):
    share_env.world.assays[404] = AssayRow(404, 40, "RNA-seq run")       # same title, other internal assays
    share_env.world.mapping[404] = [999]
    row = share_env.planned(U3)
    share_env.session.assays[404] = (40, "RNA-seq run")
    share_env.session.script["assay"] = ["lost"]
    share_env.step(row)
    assert share_env.step(row).code == share_apply.CLONE_OUTCOME_UNKNOWN and ("clone", "adopted") not in _events(row)


def test_two_run_directories_chosen_in_one_second_differ_and_a_refused_plan_leaves_none(share_env):
    first, second = share_apply._new_run_dir(T0), share_apply._new_run_dir(T0)
    assert first != second and first.is_dir() and second.is_dir()
    row = share_jobs.create_share(ShareInput(sample_uids=[U3], source_project_id=3, destination_project_id=3,
                                             destination_study_id=40, created_at="t"), share_env.user)
    share_jobs.claim(row, "w1")
    before = sorted(share_apply.share_root().iterdir())
    assert share_apply.plan_job(row, "w1", reader=FakeReader(share_env.world), now=T0) == "refused"
    assert sorted(share_apply.share_root().iterdir()) == before


def test_the_worker_keeps_its_lease_through_a_plan_and_a_unit(share_env, monkeypatch):
    beats = []
    real = share_jobs.heartbeat
    monkeypatch.setattr(share_jobs, "heartbeat", lambda share, owner: beats.append(owner) or real(share, owner))
    row = share_env.planned(U4)
    share_env.step(row)
    assert share_env.unit(row) == "applied" and beats == ["w1", "w2"]


def test_rollback_recovers_a_share_unit_by_the_shares_rules(share_env, monkeypatch):
    row = share_env.planned(U3)
    share_env.step(row)
    share_env.step(row)
    _worker_dies_in_the_unit(share_env, row, monkeypatch, committed=False)    # prepared, rolled back, not journaled
    monkeypatch.setattr(targeted, "preview_labels", lambda d, db, ids: [])
    dry = rollback.rollback_study_moves(share_apply.run_dir_of(row), share_env.session, None, "neo4j",
                                        confirm=False, reader=FakeReader(share_env.world))
    assert (dry.counts["would_undo"]["units_unknown"], dry.counts["would_undo"]["units"]) == ([], [])
    assert _undo(share_env, row, monkeypatch).status == "done"
    assert SampleShare.objects.get(pk=row.pk).state == "rolled_back" and share_env.session.deleted == [("assay", 402)]


def test_an_undo_that_died_after_its_first_intent_still_ends_the_share(share_env):
    from nextseek_api.studies.journal import Journal

    row = share_env.planned(U3, U5)
    share_env.step(row)
    Journal(share_apply.run_dir_of(row) / JOURNAL_FILE, run_id=row.run_dir).append("undo", "intent", part="unit",
                                                                                   unit=1, investigation=None)
    answer = share_env.step(row)
    assert (answer.status_code, answer.code, len(share_env.session.posts)) == (409, "share_rolled_back", 1)
