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
