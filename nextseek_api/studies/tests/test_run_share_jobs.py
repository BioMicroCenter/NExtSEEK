"""manage.py run_share_jobs: one pass plans a planning share and links a queued one (tool spec 16.4)."""
import contextlib

import pytest
from django.core.management import call_command

from nextseek_api.management.commands import run_share_jobs as cmd
from nextseek_api.studies import share_jobs
from nextseek_api.studies.models_db import SampleShare
from nextseek_api.studies.tests.conftest import U3, U4, FakeReader


@pytest.fixture
def worker(share_env, monkeypatch):
    monkeypatch.setattr(cmd, "_graph", lambda: contextlib.nullcontext((None, "neo4j")))
    monkeypatch.setattr(cmd, "SnapshotReader", lambda session, driver, db: FakeReader(share_env.world))
    return share_env


def _new(env, *uids):
    from nextseek_api.studies.models import ShareInput

    return share_jobs.create_share(ShareInput(sample_uids=list(uids), source_project_id=3, destination_project_id=5,
                                              destination_study_id=40, created_at="t"), env.user)


def test_a_pass_plans_a_planning_share(worker):
    row = _new(worker, U3)
    assert cmd.run_pass("w", 10) == 1
    assert SampleShare.objects.get(pk=row.pk).state == "planned"
    assert cmd.run_pass("w", 10) == 0


def test_a_pass_links_a_queued_share(worker):
    row = _new(worker, U4)
    cmd.run_pass("w", 10)
    row.refresh_from_db()
    assert worker.step(row).state == "queued"
    assert cmd.run_pass("w", 10) == 1
    assert SampleShare.objects.get(pk=row.pk).state == "applied"


def test_once_runs_one_pass(worker):
    _new(worker, U3)
    call_command("run_share_jobs", "--once")
    assert SampleShare.objects.get().state == "planned"


def _expire(row):
    from datetime import timedelta

    from django.utils import timezone

    SampleShare.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))


def test_a_share_the_worker_keeps_failing_on_ends_with_the_error(worker, monkeypatch):
    row = _new(worker, U4)
    cmd.run_pass("w", 10)
    row.refresh_from_db()
    worker.step(row)
    monkeypatch.setattr(cmd.share_apply, "run_share_unit", lambda share, owner: 1 / 0)
    for attempt in (1, 2):
        assert cmd.run_pass("w", 10) == 1                       # taken once a pass: its lease holds it till then
        got = SampleShare.objects.get(pk=row.pk)
        assert (got.state, got.error["code"], got.error["attempts"]) == ("running", "worker_error", attempt)
        _expire(row)
    cmd.run_pass("w", 10)
    got = SampleShare.objects.get(pk=row.pk)
    assert (got.state, got.error["code"], got.error["attempts"], got.claim_owner) == (
        "apply_failed", "worker_error", 3, None)
    assert "ZeroDivisionError" in got.error["detail"]


def test_a_planning_share_the_worker_keeps_failing_on_ends_plan_failed(worker, monkeypatch):
    row = _new(worker, U3)
    monkeypatch.setattr(cmd.share_apply, "plan_job", lambda share, owner, reader: 1 / 0)
    for _ in range(3):
        cmd.run_pass("w", 10)
        _expire(row)
    got = SampleShare.objects.get(pk=row.pk)
    assert (got.state, got.error["code"], got.error["attempts"]) == ("plan_failed", "worker_error", 3)


def test_a_queued_share_behind_a_busy_lock_does_not_hold_up_planning(worker, monkeypatch):
    from nextseek_api.studies import preflight

    queued = _new(worker, U4)
    cmd.run_pass("w", 10)
    queued.refresh_from_db()
    worker.step(queued)
    version = SampleShare.objects.get(pk=queued.pk).state_version
    newer = _new(worker, U3)

    @contextlib.contextmanager
    def busy():
        yield False

    monkeypatch.setattr(preflight, "run_lock", busy)
    assert cmd.run_pass("w", 10) == 2
    assert SampleShare.objects.get(pk=newer.pk).state == "planned"
    got = SampleShare.objects.get(pk=queued.pk)
    assert (got.state, got.state_version) == ("queued", version + 2)
