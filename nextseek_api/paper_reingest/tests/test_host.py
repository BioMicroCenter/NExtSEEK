import os
from types import SimpleNamespace

import pytest

from nextseek_api.paper_reingest import host


def test_read_input_refuses_symlink_and_traversal(tmp_path):
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4")
    os.symlink("/etc/hostname", tmp_path / "link.pdf")
    assert host.read_input(str(tmp_path), "paper.pdf") == b"%PDF-1.4"
    with pytest.raises(OSError):
        host.read_input(str(tmp_path), "link.pdf")
    with pytest.raises(ValueError):
        host.read_input(str(tmp_path), "../paper.pdf")


def test_read_input_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "MAX_PDF_BYTES", 4)
    (tmp_path / "big.pdf").write_bytes(b"123456")
    with pytest.raises(ValueError):
        host.read_input(str(tmp_path), "big.pdf")


def test_enabled_reads_flag(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST", "1")
    assert host.enabled()
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST", "0")
    assert not host.enabled()


@pytest.mark.django_db
def test_enqueue_waits_for_the_run_row_to_commit(monkeypatch, django_capture_on_commit_callbacks):
    from nextseek_api.paper_reingest import service

    queued = []
    monkeypatch.setattr(service, "enqueue_acquire", queued.append)
    session = host.start_session(SimpleNamespace(user=SimpleNamespace(username="curator")))
    run = SimpleNamespace(run_id="r1")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        session.enqueue_acquire(run)
        assert queued == []
    assert len(callbacks) == 1
    callbacks[0]()
    assert queued == [run]


_REQ = SimpleNamespace(user=SimpleNamespace(username="curator"))


def _no_folder_lookup(request):
    raise AssertionError("a PMID must not look up the input folder")


def test_the_launch_fetcher_and_the_session_share_the_op_deadline(monkeypatch):
    from chat_nextseek import call_scope

    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    session = host.start_session(_REQ)
    with call_scope.scope(deadline_s=12.0):
        fetcher = session.launch_seams("38000001").fetcher
        connect, read = fetcher._request_timeout()
        assert read <= 12.0 - host.ANSWER_MARGIN_S and connect <= read
        assert session.time_left() <= 12.0 - host.ANSWER_MARGIN_S
    assert fetcher._retries == 0
    # Outside any scope the op's own limit is the budget.
    assert session.time_left() == 60.0 - host.ANSWER_MARGIN_S


def test_with_no_time_left_no_request_starts(monkeypatch):
    from chat_nextseek import call_scope

    from NessieAI.ns.paper.fetch import OutOfTime

    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    session = host.start_session(_REQ)
    with call_scope.scope(deadline_s=4.0):
        fetcher = session.launch_seams("38000001").fetcher
        fetcher._session = None  # any request would fail on this, not with OutOfTime
        with pytest.raises(OutOfTime):
            fetcher.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search")
        left = session.time_left()
        assert left is None or left < 1.0


def test_only_a_possible_file_name_looks_up_the_input_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    for paper in ("38000001", "PMC1234567", "10.1000/xyz123", "https://doi.org/10.1000/xyz123"):
        assert host.start_session(_REQ).launch_seams(paper).input_files == set()
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(host, "_input_dir", lambda request: str(tmp_path))
    assert host.start_session(_REQ).launch_seams("paper.pdf").input_files == {"paper.pdf"}


def _run(user, tmp_path, monkeypatch):
    from NessieAI.ns.paper.models import PaperArtifact, PaperRef, PaperText
    from nextseek_api.paper_reingest import service

    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    paper = PaperArtifact(ref=PaperRef(pmid="1", doi="10.1/x", title="T"), text=PaperText(),
                          text_source="europepmc", mode="new")
    return service.create_run(user=user, paper_input="1", paper=paper), paper


def _broker_down(monkeypatch):
    from nextseek_api.paper_reingest import service

    def down(run):
        raise ConnectionError("broker refused the connection")
    monkeypatch.setattr(service, "enqueue_acquire", down)


@pytest.mark.django_db(transaction=True)
def test_a_queue_that_is_down_fails_the_run_and_refuses_the_start(django_user_model, tmp_path, monkeypatch):
    """Autocommit, as in the op view: on_commit runs at once, so the caller hears queue_unavailable."""
    from NessieAI.ns.paper.launch import LaunchOutcome
    from NessieAI.ns.paper.start import StartRefused, start
    from nextseek_api.assistant import chat_turns

    user = django_user_model.objects.create(username="curator", is_superuser=True)
    run, paper = _run(user, tmp_path, monkeypatch)
    run.delete()
    _broker_down(monkeypatch)
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST", "1")
    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    monkeypatch.setattr(chat_turns, "running_cc_turn", lambda user: None)
    monkeypatch.setattr("NessieAI.ns.paper.start.launch_check", lambda raw, seams: LaunchOutcome(status="ok", paper=paper))
    with pytest.raises(StartRefused) as err:
        start("1", None, host.start_session(SimpleNamespace(user=user)))
    assert err.value.error_type == "queue_unavailable"
    assert str(err.value) == "The run could not be queued; try again shortly."
    from nextseek_api.paper_reingest.models_db import PaperReingestRun

    row = PaperReingestRun.objects.get(user=user)
    assert (row.state, row.failed_stage, row.reason) == ("failed", "queue", "could not queue: ConnectionError")


@pytest.mark.django_db
def test_a_queue_that_is_down_after_the_commit_still_fails_the_run(django_user_model, tmp_path, monkeypatch,
                                                                  django_capture_on_commit_callbacks):
    """Inside a transaction the enqueue waits for the commit; nobody is left to hear a refusal, so the
    callback raises nothing into the committer, and the row still ends failed."""
    user = django_user_model.objects.create(username="curator", is_superuser=True)
    run, _ = _run(user, tmp_path, monkeypatch)
    _broker_down(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        host.start_session(SimpleNamespace(user=user)).enqueue_acquire(run)
    assert len(callbacks) == 1
    run.refresh_from_db()
    assert (run.state, run.failed_stage, run.reason) == ("failed", "queue", "could not queue: ConnectionError")
