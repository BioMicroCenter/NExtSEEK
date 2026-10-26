from types import SimpleNamespace

import pytest

from NessieAI.ns.paper.launch import LaunchOutcome
from NessieAI.ns.paper.models import PaperArtifact, PaperRef, PaperText
from NessieAI.ns.paper.start import StartRefused, start


def _session(outcome=None, superuser=True, enabled=True, existing=None, seams="seams"):
    made, queued = [], []
    run = SimpleNamespace(run_id="r1", state="queued")
    session = SimpleNamespace(
        user=SimpleNamespace(is_superuser=superuser), enabled=lambda: enabled,
        launch_seams=lambda paper: seams, unfinished_run=lambda doi, raw: existing,
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


# Every expected launch failure is a refusal with its own error_type, run through the real launch check.

def _seams(routes=None, files=None, read=None, projects=None):
    from NessieAI.ns.paper.launch import LaunchSeams
    from NessieAI.tests.ns.paper.fakes import fetcher

    f, _ = fetcher(routes or {}, retries=0)
    files = files or {}
    return LaunchSeams(fetcher=f, input_files=set(files), read_input_file=read or (lambda name: files[name]),
                       doi_project_ids=projects or (lambda doi: []))


def _refusal(paper, seams):
    with pytest.raises(StartRefused) as err:
        start(paper, None, _session(seams=seams)[0])
    assert "/srv" not in str(err.value) and "secret" not in str(err.value)
    return err.value


def _pmid_search():
    from NessieAI.ns.paper import europepmc
    from NessieAI.tests.ns.paper.fakes import key

    return key(f"{europepmc.BASE}/search", {"query": "EXT_ID:38000001 AND SRC:MED", "format": "json",
                                            "resultType": "core", "pageSize": "1"})


def test_source_down_is_source_unavailable():
    from NessieAI.tests.ns.paper.fakes import FakeResponse

    err = _refusal("38000001", _seams({_pmid_search(): FakeResponse(503)}))
    assert err.error_type == "source_unavailable"
    assert "www.ebi.ac.uk" in str(err) and "try again later" in str(err)


def test_redirect_off_the_allowlist_is_source_not_allowed():
    from NessieAI.tests.ns.paper.fakes import FakeResponse

    moved = FakeResponse(302, headers={"Location": "https://elsewhere.example/secret"})
    assert _refusal("38000001", _seams({_pmid_search(): moved})).error_type == "source_not_allowed"


def test_oversized_attachment_is_pdf_too_large():
    from NessieAI.ns.paper.launch import AttachmentTooLarge

    def big(name):
        raise AttachmentTooLarge(f"{name} is larger than 100 MB")
    err = _refusal("paper.pdf", _seams(files={"paper.pdf": b""}, read=big))
    assert err.error_type == "pdf_too_large" and "100 MB" in str(err)


def test_attachment_that_cannot_be_opened_is_pdf_unreadable():
    def link(name):
        raise OSError(40, "Too many levels of symbolic links", f"/srv/users/p/u/input/{name}")
    assert _refusal("paper.pdf", _seams(files={"paper.pdf": b""}, read=link)).error_type == "pdf_unreadable"


def test_attachment_that_is_not_a_pdf_is_pdf_unreadable():
    err = _refusal("paper.pdf", _seams(files={"paper.pdf": b"this is not a PDF"}))
    assert err.error_type == "pdf_unreadable"


def test_catalog_outage_is_catalog_unavailable():
    from NessieAI.ns.paper import europepmc
    from NessieAI.tests.ns.paper.fakes import FakeResponse, js, key
    from NessieAI.tests.ns.paper.fixtures import EPMC_HIT, JATS_XML, epmc_search

    def down(doi):
        raise RuntimeError("sample catalog unreachable: db password=secret")
    routes = {_pmid_search(): js(epmc_search([EPMC_HIT])),
              key(f"{europepmc.BASE}/PMC10000001/fullTextXML"): FakeResponse(200, JATS_XML)}
    err = _refusal("38000001", _seams(routes, projects=down))
    assert err.error_type == "catalog_unavailable" and "try again later" in str(err)
