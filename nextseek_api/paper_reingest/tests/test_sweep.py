"""The boot sweep: no paper worker is running yet, so the runs a restart cut are put right."""
import re
from pathlib import Path

import pytest
from django.core.management import call_command

from NessieAI.ns.paper.models import PaperArtifact, PaperRef, PaperText
from nextseek_api.paper_reingest import service
from nextseek_api.paper_reingest.models_db import PaperReingestRun


def _runs(django_user_model, tmp_path, monkeypatch, states):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    user = django_user_model.objects.create(username="curator", is_superuser=True)
    runs = {}
    for i, state in enumerate(states):
        paper = PaperArtifact(ref=PaperRef(pmid=str(i), doi=f"10.1/{i}"), text=PaperText(), text_source="pdf",
                              mode="new")
        run = service.create_run(user=user, paper_input=str(i), paper=paper)
        if state != "queued":
            service.set_state(run, state, reason="waiting for www.ebi.ac.uk: HTTP 503" if state == "paused" else "")
        runs[state] = run
    return runs


def _row(run):
    return PaperReingestRun.objects.get(pk=run.pk)


@pytest.mark.django_db
def test_the_sweep_fails_cut_runs_and_queues_waiting_ones(django_user_model, tmp_path, monkeypatch, capsys):
    runs = _runs(django_user_model, tmp_path, monkeypatch, ["queued", "paused", "acquiring", "context", "failed"])
    queued = []
    monkeypatch.setattr(service, "enqueue_acquire", lambda run: queued.append(run.run_id))
    call_command("sweep_paper_reingest_runs")
    cut = _row(runs["acquiring"])
    assert (cut.state, cut.failed_stage) == ("failed", "acquire")
    assert cut.reason.startswith("interrupted by a server restart") and cut.live_key is None
    assert sorted(queued) == sorted([runs["queued"].run_id, runs["paused"].run_id])
    assert _row(runs["paused"]).state == "paused"  # the claim moves it on, not the sweep
    assert (_row(runs["context"]).state, _row(runs["failed"]).state) == ("context", "failed")
    assert "failed 1 interrupted paper reingest run(s); queued 2 again; 0 could not be queued" in capsys.readouterr().out


@pytest.mark.django_db
def test_a_queue_that_is_down_leaves_the_runs_waiting_and_the_boot_going(django_user_model, tmp_path, monkeypatch,
                                                                       capsys):
    runs = _runs(django_user_model, tmp_path, monkeypatch, ["queued", "acquiring"])

    def down(run):
        raise ConnectionError("broker refused the connection")
    monkeypatch.setattr(service, "enqueue_acquire", down)
    call_command("sweep_paper_reingest_runs")
    assert _row(runs["queued"]).state == "queued"
    assert _row(runs["acquiring"]).state == "failed"
    assert "queued 0 again; 1 could not be queued" in capsys.readouterr().out


@pytest.mark.django_db
def test_a_requeued_duplicate_message_is_skipped_by_the_claim(django_user_model, tmp_path, monkeypatch):
    """The broker may still hold the run's first message; the second one to run finds it claimed or done."""
    from nextseek_api.paper_reingest import tasks
    from NessieAI.ns.paper.models import EvidenceArtifact

    runs = _runs(django_user_model, tmp_path, monkeypatch, ["queued"])
    monkeypatch.setattr(tasks, "acquire", lambda paper, fetcher: EvidenceArtifact(
        paper=paper.ref, items=[], deposits=[], skeleton=[], gaps=[]))
    run_id = runs["queued"].run_id
    assert tasks.run_acquire(run_id, retries_done=0, max_retries=2) == "context"
    assert tasks.run_acquire(run_id, retries_done=0, max_retries=2) == "skipped:context"
    assert _row(runs["queued"]).attempts == 1


def test_the_entrypoint_sweeps_before_the_paper_worker_starts_and_never_stops_the_boot():
    entrypoint = Path("docker/scripts/entrypoint.sh").read_text()
    sweep = re.search(r"^uv run --no-sync manage\.py sweep_paper_reingest_runs \|\| echo .*>&2$", entrypoint, re.M)
    assert sweep, "the sweep is missing, or a failure of it would stop the boot"
    assert sweep.start() < entrypoint.index("-Q paper_reingest")
    # The worker branch only: the web server's branch has exited by then.
    assert sweep.start() > entrypoint.index("mark_interrupted_batch_jobs")
