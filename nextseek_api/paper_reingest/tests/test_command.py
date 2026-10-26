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
