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


def test_launch_seams_keep_to_the_op_budget(monkeypatch):
    from chat_nextseek import call_scope

    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    with call_scope.scope(deadline_s=12.0):
        fetcher = host.start_session(_REQ).launch_seams("38000001").fetcher
    connect, read = fetcher._timeout
    assert read <= 12.0 - host.ANSWER_MARGIN_S and connect <= read
    assert fetcher._retries == 0
    # Outside any scope the op's own limit is the budget.
    assert host.start_session(_REQ).launch_seams("38000001").fetcher._timeout[1] <= 60.0 - host.ANSWER_MARGIN_S


def test_launch_seams_with_no_time_left_is_a_deadline(monkeypatch):
    from chat_nextseek import call_scope

    from NessieAI.ns.granular import OpDeadlineError

    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    with call_scope.scope(deadline_s=4.0), pytest.raises(OpDeadlineError):
        host.start_session(_REQ).launch_seams("38000001")


def test_only_a_possible_file_name_looks_up_the_input_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "_input_dir", _no_folder_lookup)
    for paper in ("38000001", "PMC1234567", "10.1000/xyz123", "https://doi.org/10.1000/xyz123"):
        assert host.start_session(_REQ).launch_seams(paper).input_files == set()
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(host, "_input_dir", lambda request: str(tmp_path))
    assert host.start_session(_REQ).launch_seams("paper.pdf").input_files == {"paper.pdf"}
