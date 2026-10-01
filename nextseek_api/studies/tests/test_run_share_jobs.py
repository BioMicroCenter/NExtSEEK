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
