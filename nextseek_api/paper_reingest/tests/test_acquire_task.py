import pytest

from NessieAI.ns.paper.fetch import SourceUnavailable
from NessieAI.ns.paper.models import EvidenceArtifact, PaperArtifact, PaperRef, PaperText
from NessieAI.ns.paper.store import load_artifact
from nextseek_api.paper_reingest import service, tasks
from nextseek_api.paper_reingest.models_db import PaperReingestRun


@pytest.fixture
def run(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    paper = PaperArtifact(ref=PaperRef(pmid="1"), text=PaperText(), text_source="pdf", mode="new")
    return service.create_run(user=django_user_model.objects.create(username="c"), paper_input="1", paper=paper)


def _state(run):
    return PaperReingestRun.objects.get(pk=run.pk)


@pytest.mark.django_db
def test_success_saves_evidence_and_moves_to_context(run, monkeypatch):
    evidence = EvidenceArtifact(paper=PaperRef(pmid="1"), items=[], deposits=[], skeleton=[], gaps=[])
    monkeypatch.setattr(tasks, "acquire", lambda paper, fetcher: evidence)
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=3) == "context"
    row = _state(run)
    assert row.state == "context" and row.attempts == 1
    assert load_artifact(run.run_id, row.artifacts["evidence"], EvidenceArtifact) == evidence


@pytest.mark.django_db
def test_source_down_pauses_then_fails_when_retries_run_out(run, monkeypatch):
    def down(paper, fetcher):
        raise SourceUnavailable("www.ebi.ac.uk", "HTTP 503")
    monkeypatch.setattr(tasks, "acquire", down)
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "paused"
    assert _state(run).state == "paused" and "www.ebi.ac.uk" in _state(run).reason
    assert tasks.run_acquire(run.run_id, retries_done=2, max_retries=2) == "failed"
    row = _state(run)
    assert (row.state, row.failed_stage) == ("failed", "acquire")


@pytest.mark.django_db
def test_unexpected_error_fails_the_run(run, monkeypatch):
    def broken(paper, fetcher):
        raise KeyError("x")
    monkeypatch.setattr(tasks, "acquire", broken)
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "failed"
    assert _state(run).reason.startswith("KeyError")


@pytest.mark.django_db
def test_run_past_acquire_is_skipped(run):
    service.set_state(run, "context")
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "skipped:context"


def test_queue_and_worker_are_wired():
    from pathlib import Path

    from nextseek_api.batch_upload.celery_app import app

    assert app.conf.task_routes["paper_reingest.*"] == {"queue": "paper_reingest"}
    assert "paper_reingest.acquire" in app.tasks
    entrypoint = Path("docker/scripts/entrypoint.sh").read_text()
    assert "-Q paper_reingest" in entrypoint and "--hostname=paper_reingest@%h" in entrypoint


def _evidence():
    return EvidenceArtifact(paper=PaperRef(pmid="1"), items=[], deposits=[], skeleton=[], gaps=[])


@pytest.mark.django_db
def test_failure_saving_the_evidence_fails_the_run_not_leaves_it_acquiring(run, monkeypatch):
    monkeypatch.setattr(tasks, "acquire", lambda paper, fetcher: _evidence())

    def full_disk(run_id, name, model):
        raise OSError("No space left on device")
    monkeypatch.setattr(tasks, "save_artifact", full_disk)
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "failed"
    row = _state(run)
    assert (row.state, row.failed_stage) == ("failed", "acquire")
    assert row.reason.startswith("OSError")


@pytest.mark.django_db
def test_a_paused_run_resumes_to_context(run, monkeypatch):
    service.set_state(run, "paused", reason="waiting for www.ebi.ac.uk: HTTP 503")
    monkeypatch.setattr(tasks, "acquire", lambda paper, fetcher: _evidence())
    assert tasks.run_acquire(run.run_id, retries_done=1, max_retries=3) == "context"
    row = _state(run)
    assert row.state == "context" and row.reason == "" and row.attempts == 1


class _Retried(Exception):
    def __init__(self, kwargs):
        self.kwargs = kwargs


def test_task_retries_with_the_pause_countdown_when_paused(monkeypatch):
    monkeypatch.setattr(tasks, "run_acquire", lambda run_id, **kw: "paused")

    def fake_retry(**kwargs):
        raise _Retried(kwargs)
    monkeypatch.setattr(tasks.acquire_task, "retry", fake_retry)
    with pytest.raises(_Retried) as caught:
        tasks.acquire_task.run(run_id="x")
    assert caught.value.kwargs == {"countdown": tasks.PAUSE_COUNTDOWN_S}


def test_task_returns_the_outcome_when_not_paused(monkeypatch):
    seen = {}

    def fake_run_acquire(run_id, *, retries_done, max_retries):
        seen.update(run_id=run_id, retries_done=retries_done, max_retries=max_retries)
        return "context"
    monkeypatch.setattr(tasks, "run_acquire", fake_run_acquire)
    assert tasks.acquire_task.run(run_id="r1") == {"run_id": "r1", "outcome": "context"}
    assert seen == {"run_id": "r1", "retries_done": 0, "max_retries": tasks.PAUSE_RETRIES}


@pytest.mark.parametrize("raw", ["10m", "", "-5", "1.5"])
def test_bad_env_value_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("PAPER_REINGEST_PAUSE_S", raw)
    assert tasks._env_int("PAPER_REINGEST_PAUSE_S", 600) == 600


def test_good_and_missing_env_values(monkeypatch):
    monkeypatch.setenv("PAPER_REINGEST_PAUSE_S", "30")
    assert tasks._env_int("PAPER_REINGEST_PAUSE_S", 600) == 30
    monkeypatch.delenv("PAPER_REINGEST_PAUSE_S")
    assert tasks._env_int("PAPER_REINGEST_PAUSE_S", 600) == 600


def _cancel_during(outcome):
    """An acquire that the operator cancels while it runs, then ends with ``outcome``."""
    from django.core.management import call_command

    def run_and_cancel(paper, fetcher):
        call_command("paper_reingest", "cancel", PaperReingestRun.objects.get().run_id)
        if outcome == "down":
            raise SourceUnavailable("www.ebi.ac.uk", "HTTP 503")
        if outcome == "broken":
            raise KeyError("x")
        return _evidence()
    return run_and_cancel


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", ["evidence", "down", "broken"])
def test_a_run_cancelled_while_it_acquires_stays_cancelled(run, monkeypatch, outcome):
    monkeypatch.setattr(tasks, "acquire", _cancel_during(outcome))
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "skipped:failed"
    row = _state(run)
    assert (row.state, row.failed_stage, row.reason) == ("failed", "cancelled", "cancelled by operator")
    assert "evidence" not in row.artifacts and row.live_key is None


@pytest.mark.django_db
def test_a_second_message_for_a_run_already_acquiring_does_nothing(run, monkeypatch):
    service.set_state(run, "acquiring")
    monkeypatch.setattr(tasks, "acquire", lambda paper, fetcher: pytest.fail("a claimed run was run twice"))
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "skipped:acquiring"
    assert _state(run).attempts == 0


@pytest.mark.django_db
def test_the_claim_is_one_conditional_update(run, monkeypatch):
    """A cancel that lands between the read and the claim wins: the stale read must not start the run."""
    real_get = PaperReingestRun.objects.get

    def get_then_cancel(**kwargs):
        row = real_get(**kwargs)
        PaperReingestRun.objects.filter(pk=row.pk).update(state="failed", failed_stage="cancelled", live_key=None)
        return row
    monkeypatch.setattr(PaperReingestRun.objects, "get", get_then_cancel)
    monkeypatch.setattr(tasks, "acquire", lambda paper, fetcher: pytest.fail("a cancelled run was started"))
    assert tasks.run_acquire(run.run_id, retries_done=0, max_retries=2) == "skipped:failed"
    assert _state(run).state == "failed"
