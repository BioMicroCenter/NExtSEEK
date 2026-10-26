import json

import pytest
from django.core.management import call_command

from NessieAI.ns.paper.models import EvidenceArtifact, PaperArtifact, PaperRef, PaperText
from nextseek_api.paper_reingest import service


@pytest.mark.django_db
def test_status_and_export(django_user_model, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path / "runs"))
    paper = PaperArtifact(ref=PaperRef(pmid="1", title="T"), text=PaperText(), text_source="pdf", mode="new")
    run = service.create_run(user=django_user_model.objects.create(username="c"), paper_input="1", paper=paper)
    call_command("paper_reingest", "status", run.run_id)
    assert f"{run.run_id}  queued  new" in capsys.readouterr().out
    out = tmp_path / "paper.json"
    call_command("paper_reingest", "export", run.run_id, "paper", "--out", str(out))
    assert json.loads(out.read_text())["ref"]["title"] == "T"


@pytest.mark.django_db
def test_start_inline_runs_acquire(django_user_model, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    django_user_model.objects.create(username="curator", is_superuser=True)
    paper = PaperArtifact(ref=PaperRef(pmid="1", title="T"), text=PaperText(), text_source="pdf", mode="new")
    from NessieAI.ns.paper.launch import LaunchOutcome
    monkeypatch.setattr("nextseek_api.management.commands.paper_reingest.launch_check",
                        lambda raw, seams: LaunchOutcome(status="ok", paper=paper))
    monkeypatch.setattr("nextseek_api.paper_reingest.tasks.acquire",
                        lambda p, f: EvidenceArtifact(paper=p.ref, items=[], deposits=[], skeleton=[], gaps=[]))
    call_command("paper_reingest", "start", "--user", "curator", "--paper", "1", "--inline")
    assert "context" in capsys.readouterr().out


def _a_run(django_user_model, tmp_path, monkeypatch, state="queued"):
    from nextseek_api.paper_reingest.service import set_state

    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    paper = PaperArtifact(ref=PaperRef(pmid="1", title="T"), text=PaperText(), text_source="pdf", mode="new")
    run = service.create_run(user=django_user_model.objects.create(username="c"), paper_input="1", paper=paper)
    if state != "queued":
        set_state(run, state, reason="waiting for www.ebi.ac.uk: HTTP 503")
    return run


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["queued", "acquiring", "paused", "context"])
def test_cancel_fails_a_live_run(django_user_model, tmp_path, monkeypatch, capsys, state):
    run = _a_run(django_user_model, tmp_path, monkeypatch, state)
    call_command("paper_reingest", "cancel", run.run_id)
    run.refresh_from_db()
    assert (run.state, run.failed_stage, run.reason) == ("failed", "cancelled", "cancelled by operator")
    assert f"{run.run_id}  failed" in capsys.readouterr().out


@pytest.mark.django_db
def test_cancel_refuses_a_finished_run(django_user_model, tmp_path, monkeypatch):
    from django.core.management.base import CommandError

    run = _a_run(django_user_model, tmp_path, monkeypatch, "failed")
    with pytest.raises(CommandError):
        call_command("paper_reingest", "cancel", run.run_id)
    with pytest.raises(CommandError):
        call_command("paper_reingest", "cancel", "no-such-run")


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["queued", "paused", "acquiring"])
def test_requeue_puts_a_stuck_run_back_on_the_queue(django_user_model, tmp_path, monkeypatch, capsys, state):
    run = _a_run(django_user_model, tmp_path, monkeypatch, state)
    queued = []
    monkeypatch.setattr(service, "enqueue_acquire", lambda r: queued.append(r.run_id))
    call_command("paper_reingest", "requeue", run.run_id)
    run.refresh_from_db()
    assert (run.state, run.reason) == ("queued", "")
    assert queued == [run.run_id]
    assert f"{run.run_id}  queued" in capsys.readouterr().out


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["context", "failed", "applied"])
def test_requeue_refuses_any_other_state(django_user_model, tmp_path, monkeypatch, state):
    from django.core.management.base import CommandError

    run = _a_run(django_user_model, tmp_path, monkeypatch, state)
    queued = []
    monkeypatch.setattr(service, "enqueue_acquire", lambda r: queued.append(r.run_id))
    with pytest.raises(CommandError):
        call_command("paper_reingest", "requeue", run.run_id)
    run.refresh_from_db()
    assert run.state == state and queued == []
