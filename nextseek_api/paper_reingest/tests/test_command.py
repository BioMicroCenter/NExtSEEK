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


def _launch_ok(monkeypatch, doi="10.1/x"):
    from NessieAI.ns.paper.launch import LaunchOutcome

    paper = PaperArtifact(ref=PaperRef(pmid="1", doi=doi, title="T"), text=PaperText(), text_source="pdf", mode="new")
    monkeypatch.setattr("nextseek_api.management.commands.paper_reingest.launch_check",
                        lambda raw, seams: LaunchOutcome(status="ok", paper=paper))
    return paper


@pytest.mark.django_db
def test_start_with_the_queue_down_fails_the_run_at_queue_and_says_so(django_user_model, tmp_path, monkeypatch):
    from django.core.management.base import CommandError

    from nextseek_api.paper_reingest.models_db import PaperReingestRun

    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    django_user_model.objects.create(username="curator", is_superuser=True)
    _launch_ok(monkeypatch)

    def down(run):
        raise ConnectionError("broker refused the connection")
    monkeypatch.setattr(service, "enqueue_acquire", down)
    with pytest.raises(CommandError) as err:
        call_command("paper_reingest", "start", "--user", "curator", "--paper", "1")
    row = PaperReingestRun.objects.get()
    assert (row.state, row.failed_stage) == ("failed", "queue")
    assert row.run_id in str(err.value) and "could not be queued" in str(err.value)


@pytest.mark.django_db
def test_start_queues_stage_1(django_user_model, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    django_user_model.objects.create(username="curator", is_superuser=True)
    _launch_ok(monkeypatch)
    queued = []
    monkeypatch.setattr(service, "enqueue_acquire", lambda r: queued.append(r.run_id))
    call_command("paper_reingest", "start", "--user", "curator", "--paper", "1")
    assert capsys.readouterr().out.strip() == f"{queued[0]}  queued"


@pytest.mark.django_db
def test_start_refuses_a_paper_another_curator_is_running(django_user_model, tmp_path, monkeypatch):
    from django.core.management.base import CommandError

    from nextseek_api.paper_reingest.models_db import PaperReingestRun

    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    django_user_model.objects.create(username="curator", is_superuser=True)
    paper = _launch_ok(monkeypatch)
    theirs = service.create_run(user=django_user_model.objects.create(username="other", is_superuser=True),
                                paper_input="10.1/x", paper=paper)
    monkeypatch.setattr(service, "enqueue_acquire", lambda r: pytest.fail("a duplicate run was queued"))
    with pytest.raises(CommandError) as err:
        call_command("paper_reingest", "start", "--user", "curator", "--paper", "1")
    assert theirs.run_id in str(err.value) and "already in progress" in str(err.value)
    assert PaperReingestRun.objects.count() == 1


@pytest.mark.django_db
def test_cancel_refuses_a_run_that_finished_meanwhile(django_user_model, tmp_path, monkeypatch):
    """The cancel's write is conditional too: a run the worker failed a moment ago keeps its own reason."""
    from django.core.management.base import CommandError

    from nextseek_api.paper_reingest.models_db import PaperReingestRun

    run = _a_run(django_user_model, tmp_path, monkeypatch, "acquiring")

    def stale_read(run_id):
        # The worker fails the run just after the cancel read it as acquiring.
        PaperReingestRun.objects.filter(pk=run.pk).update(state="failed", failed_stage="acquire",
                                                          reason="KeyError: x", live_key=None)
        return run
    monkeypatch.setattr("nextseek_api.management.commands.paper_reingest._run", stale_read)
    with pytest.raises(CommandError):
        call_command("paper_reingest", "cancel", run.run_id)
    run.refresh_from_db()
    assert (run.state, run.failed_stage, run.reason) == ("failed", "acquire", "KeyError: x")
