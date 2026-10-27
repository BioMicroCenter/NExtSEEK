from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from NessieAI.ns.paper.models import PaperArtifact, PaperRef, PaperText
from nextseek_api.management.commands import run_paper_curation as loop
from nextseek_api.paper_reingest import launcher, service
from nextseek_api.paper_reingest.models_db import PaperReingestRun

SETTINGS = launcher.LaunchSettings(timeout_s=60, max_turns=10, budget_usd=1.0)


class Stopper:
    def __init__(self):
        self.stopped = []

    def stop_turn(self, run_id):
        self.stopped.append(run_id)
        return True


@pytest.fixture
def make_run(django_user_model, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("NEXTSEEK_TURN_DIR", str(tmp_path / "turns"))
    (tmp_path / "turns").mkdir()
    user = django_user_model.objects.create(username="c", is_superuser=True)
    count = iter(range(100))

    def make(state, **fields):
        i = next(count)
        paper = PaperArtifact(ref=PaperRef(pmid=str(i), doi=f"10.1/{i}"), text=PaperText(), text_source="pdf",
                              mode="new")
        run = service.create_run(user=user, paper_input=str(i), paper=paper)
        service.transition(run, ("queued",), state, **fields)
        return run
    return make


@pytest.mark.django_db
def test_idle_busy_and_draining(make_run, tmp_path, monkeypatch):
    claimed = []
    monkeypatch.setattr(launcher, "curate", lambda run_id, **kw: claimed.append(run_id) or "building")
    assert loop.tick(client=Stopper(), settings=SETTINGS) == "idle"
    busy = make_run("curating")
    pending = make_run("curation_pending")
    assert loop.tick(client=Stopper(), settings=SETTINGS) == "busy" and claimed == []
    service.transition(busy, ("curating",), "failed", failed_stage="cancelled")
    (tmp_path / "turns" / ".draining").touch()
    assert loop.tick(client=Stopper(), settings=SETTINGS) == "draining"
    (tmp_path / "turns" / ".draining").unlink()
    assert loop.tick(client=Stopper(), settings=SETTINGS) == "building" and claimed == [pending.run_id]


@pytest.mark.django_db
def test_the_oldest_pending_run_goes_first(make_run, monkeypatch):
    first, second = make_run("curation_pending"), make_run("curation_pending")
    PaperReingestRun.objects.filter(pk=second.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    claimed = []
    monkeypatch.setattr(launcher, "curate", lambda run_id, **kw: claimed.append(run_id) or "building")
    loop.tick(client=Stopper(), settings=SETTINGS)
    assert claimed == [second.run_id]


@pytest.mark.django_db
def test_a_stale_curation_is_stopped_and_queued_again_or_failed(make_run):
    old = timezone.now() - timedelta(hours=3)
    again, spent = make_run("curating", curation_attempts=1), make_run("verifying", curation_attempts=3)
    fresh = make_run("curating", curation_attempts=1)
    PaperReingestRun.objects.filter(pk__in=[again.pk, spent.pk]).update(updated_at=old)
    stopper = Stopper()
    assert loop.sweep_stale(settings=SETTINGS, client=stopper) == 2
    assert PaperReingestRun.objects.get(pk=again.pk).state == "curation_pending"
    spent_row = PaperReingestRun.objects.get(pk=spent.pk)
    assert (spent_row.state, spent_row.failed_stage) == ("failed", "curate")
    assert PaperReingestRun.objects.get(pk=fresh.pk).state == "curating"
    assert launcher.launch_id(again.run_id, 1) in stopper.stopped


@pytest.mark.django_db
def test_the_command_does_nothing_with_the_flag_off(make_run, monkeypatch, capsys):
    monkeypatch.delenv("NEXTSEEK_PAPER_REINGEST", raising=False)
    monkeypatch.setattr(loop, "tick", lambda **kw: pytest.fail("ticked with the flag off"))
    call_command("run_paper_curation", "--once")
    assert "off" in capsys.readouterr().out


@pytest.mark.django_db
def test_the_command_ticks_once(make_run, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST", "1")
    ticks = []
    monkeypatch.setattr(loop, "tick", lambda **kw: ticks.append(1) or "idle")
    call_command("run_paper_curation", "--once")
    assert ticks == [1]
