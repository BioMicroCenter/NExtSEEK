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
