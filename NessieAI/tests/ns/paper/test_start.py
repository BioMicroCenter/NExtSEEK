from types import SimpleNamespace

import pytest

from NessieAI.ns.paper.launch import LaunchOutcome
from NessieAI.ns.paper.models import PaperArtifact, PaperRef, PaperText
from NessieAI.ns.paper.start import StartRefused, start


def _session(outcome=None, superuser=True, enabled=True, existing=None):
    made, queued = [], []
    run = SimpleNamespace(run_id="r1", state="queued")
    session = SimpleNamespace(
        user=SimpleNamespace(is_superuser=superuser), enabled=lambda: enabled,
        launch_seams=lambda: "seams", unfinished_run=lambda doi, raw: existing,
        create_run=lambda **kw: made.append(kw) or run, enqueue_acquire=queued.append)
    return session, made, queued


def _ok(mode="new", projects=()):
    return LaunchOutcome(status="ok", paper=PaperArtifact(
        ref=PaperRef(pmid="1", doi="10.1/x", title="T"), text=PaperText(), text_source="europepmc",
        mode=mode, existing_project_ids=list(projects)))


def test_non_superuser_refused():
    session, _, _ = _session(superuser=False)
    with pytest.raises(StartRefused) as err:
        start("1", None, session)
    assert err.value.error_type == "superusers_only"


def test_feature_off_refused():
    session, _, _ = _session(enabled=False)
    with pytest.raises(StartRefused) as err:
        start("1", None, session)
    assert err.value.error_type == "feature_off"


def test_ok_creates_and_queues(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.start.launch_check", lambda raw, seams: _ok())
    session, made, queued = _session()
    out = start("1", 4, session)
    assert (out["run_id"], out["status"], out["mode"]) == ("r1", "ok", "new")
    assert made[0]["project_hint"] == 4 and made[0]["paper_input"] == "1"
    assert [r.run_id for r in queued] == ["r1"]
    assert "“T”" in out["reply"]


def test_update_mode_is_said(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.start.launch_check", lambda raw, seams: _ok("update", [7]))
    out = start("1", None, _session()[0])
    assert out["mode"] == "update" and "projects 7" in out["reply"]


def test_needs_pdf_queues_nothing(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.start.launch_check",
                        lambda raw, seams: LaunchOutcome(status="needs_pdf", reply="Attach the PDF."))
    session, made, queued = _session()
    out = start("1", None, session)
    assert (out["run_id"], out["status"], out["reply"]) == (None, "needs_pdf", "Attach the PDF.")
    assert made == [] and queued == []


def test_existing_run_is_returned_not_duplicated(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.start.launch_check", lambda raw, seams: _ok())
    session, made, _ = _session(existing=SimpleNamespace(run_id="old", state="paused"))
    out = start("1", None, session)
    assert (out["run_id"], out["status"], out["state"]) == ("old", "already_running", "paused")
    assert made == []


def test_unrecognised_paper(monkeypatch):
    from NessieAI.ns.paper.identify import IdentifyError

    def bad(raw, seams):
        raise IdentifyError("could not find a PMID")
    monkeypatch.setattr("NessieAI.ns.paper.start.launch_check", bad)
    with pytest.raises(StartRefused) as err:
        start("a paper", None, _session()[0])
    assert err.value.error_type == "unrecognised_paper"
