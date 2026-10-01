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
from nextseek_api.studies import preflight, rollback, share_apply, share_jobs
from nextseek_api.studies.journal import JOURNAL_FILE, read_journal
from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.models_db import SampleShare
from nextseek_api.studies.seek import ADOPT_POLL_S, ADOPT_WAIT_S, SeekError
from nextseek_api.studies.tests.conftest import (PASSWORD, U3, U4, U5, FakeReader, links_of, outbox_of,
                                                 projects_of)


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


def test_the_clone_takes_the_destination_studys_policy(share_env):
    share_env.step(share_env.planned(U3))
    [payload] = share_env.session.payloads
    assert payload["data"]["attributes"]["policy"] == share_env.world.study_reps[40]["data"]["attributes"]["policy"]
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
def test_seeks_answers_map_and_journal_no_outcome(share_env, status, code, http):
    row = share_env.planned(U3)
    share_env.session.script["assay"] = [SeekError("x", "SEEK said no", status)]
    answer = share_env.step(row)
    assert (answer.status_code, answer.code, answer.message) == (http, code, "SEEK said no")
    assert [e for e in _events(row) if e[0] == "clone"] == [("clone", "intent")]


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
    monkeypatch.setattr(paper_studies, "restore_paper_links", lambda *a: {})
    before = (links_of(share_env.engine), projects_of(share_env.engine))
    row = share_env.planned(U3, U4)
    share_env.step(row)
    share_env.step(row)
    assert share_env.unit(row) == "applied"
    result = rollback.rollback_study_moves(share_apply.run_dir_of(row), share_env.session, None, "neo4j",
                                           confirm=True)
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
    monkeypatch.setattr(paper_studies, "restore_paper_links", lambda *a: {})
    return rollback.rollback_study_moves(share_apply.run_dir_of(row), env.session, None, "neo4j", confirm=True)


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
