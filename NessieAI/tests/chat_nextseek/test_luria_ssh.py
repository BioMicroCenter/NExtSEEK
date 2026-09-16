import os
import subprocess

import pytest

from chat_nextseek.luria import ssh as ssh_mod

LE = {"user": "cdemu", "host": "luria.mit.edu", "key": "/k", "working_path": "/net/x"}


def test_prepare_key_copies_and_chmods_600(tmp_path):
    src = tmp_path / "id_luria"
    src.write_text("PRIVATEKEY")
    out = ssh_mod.prepare_key(str(src))
    try:
        assert open(out).read() == "PRIVATEKEY"
        assert (os.stat(out).st_mode & 0o777) == 0o600
    finally:
        os.remove(out)


def test_ssh_run_builds_command_and_returns_stdout(monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stdout = "Submitted batch job 4821\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout=None):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = ssh_mod.ssh_run(LE, "sbatch run.sh", key_path="/tmp/k")
    assert "Submitted batch job 4821" in out
    assert seen["cmd"][0] == "ssh"
    assert "cdemu@luria.mit.edu" in seen["cmd"]
    assert "sbatch run.sh" == seen["cmd"][-1]
    assert "BatchMode=yes" in seen["cmd"]


def test_ssh_run_raises_on_nonzero(monkeypatch):
    class R:
        returncode = 255
        stdout = ""
        stderr = "Permission denied"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    try:
        ssh_mod.ssh_run(LE, "true", key_path="/tmp/k")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Permission denied" in str(e)


def test_ssh_run_bytes_builds_command_and_returns_raw_stdout(monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stdout = b"\x1f\x8b\x00binary"
        stderr = b""

    def fake_run(cmd, capture_output, timeout=None):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = ssh_mod.ssh_run_bytes(LE, "cat run.tar", key_path="/tmp/k")
    assert out == b"\x1f\x8b\x00binary"
    assert seen["cmd"][0] == "ssh"
    assert "cdemu@luria.mit.edu" in seen["cmd"]
    assert seen["timeout"] is None


def test_ssh_run_bytes_passes_a_timeout_through_to_subprocess_run(monkeypatch):
    """Important 2 (2026-09-16 whole-branch review): ssh_run_bytes had no
    timeout kwarg at all -- a stalled shared filesystem on the run-harvest
    staging call hung the whole CC turn indefinitely, unlike run-checksum's
    ssh_run call, which already had one. Proves the kwarg actually reaches
    subprocess.run rather than being swallowed."""
    seen = {}

    def fake_run(cmd, capture_output, timeout=None):
        seen["timeout"] = timeout
        class R:
            returncode = 0
            stdout = b"ok"
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ssh_mod.ssh_run_bytes(LE, "cat run.tar", key_path="/tmp/k", timeout=150)
    assert seen["timeout"] == 150


def test_ssh_run_bytes_propagates_timeout_expired(monkeypatch):
    def fake_run(cmd, capture_output, timeout=None):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        ssh_mod.ssh_run_bytes(LE, "cat run.tar", key_path="/tmp/k", timeout=1)


def test_ssh_run_bytes_raises_on_nonzero(monkeypatch):
    class R:
        returncode = 255
        stdout = b""
        stderr = b"Permission denied"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    try:
        ssh_mod.ssh_run_bytes(LE, "true", key_path="/tmp/k")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Permission denied" in str(e)


def test_scp_file_targets_full_remote_path(monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, capture_output, text):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ssh_mod.scp_file(LE, "/local/run.sh", "/net/x/runs/r/run.sh", key_path="/tmp/k")
    assert seen["cmd"][0] == "scp"
    assert seen["cmd"][-1] == "cdemu@luria.mit.edu:/net/x/runs/r/run.sh"
    assert "/local/run.sh" in seen["cmd"]
