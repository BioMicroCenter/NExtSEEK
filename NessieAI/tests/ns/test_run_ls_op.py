"""granular._run_ls: runs-root path guard + read-only SSH ls (ssh_run mocked)."""
import pytest

from chat_nextseek import call_scope

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g


class _Cfg:
    LURIA_ENV = {"working_path": "/net/bmc-pub10/data1/bmc/pipeline_cd",
                 "key": "/keys/luria", "user": "alice", "host": "luria.mit.edu"}


def _call(monkeypatch, run_dir, ssh_out="total 0\n-rw-r--r-- 1 u u 10 matrix.h5\n", **op_ctx):
    monkeypatch.setattr(ssh, "prepare_key", lambda k: "/tmp/key")
    seen = {}

    def fake_ssh_run(env, cmd, *, key_path, timeout=None):
        seen["cmd"] = cmd
        seen["key_path"] = key_path
        seen["timeout"] = timeout
        return ssh_out

    monkeypatch.setattr(ssh, "ssh_run", fake_ssh_run)
    result = g._run_ls({"run_dir": run_dir}, _Cfg(), None, None, None, None, **op_ctx)
    return result, seen


def test_valid_run_dir_lists_recursively(monkeypatch):
    result, seen = _call(monkeypatch, "/net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_gideon_1")
    assert "matrix.h5" in result["tree"]
    assert result["truncated"] is False
    assert seen["cmd"].startswith("ls -laR ")
    assert "runs/nfcore_gideon_1" in seen["cmd"]
    assert seen["timeout"] == 60  # outside an op: the table's limit


def test_traversal_out_of_runs_root_rejected(monkeypatch):
    with pytest.raises(g.OpValidationError):
        _call(monkeypatch, "/net/bmc-pub10/data1/bmc/pipeline_cd/runs/../../../etc")


def test_dir_outside_runs_root_rejected(monkeypatch):
    with pytest.raises(g.OpValidationError):
        _call(monkeypatch, "/etc/passwd")


def test_truncation_flag(monkeypatch):
    big = "x" * (g._RUN_LS_CAP + 100)
    result, _ = _call(monkeypatch, "/net/bmc-pub10/data1/bmc/pipeline_cd/runs/big", ssh_out=big)
    assert result["truncated"] is True
    assert len(result["tree"]) == g._RUN_LS_CAP


def test_unconfigured_luria_rejected(monkeypatch):
    monkeypatch.setattr(ssh, "prepare_key", lambda k: "/tmp/key")
    monkeypatch.setattr(ssh, "ssh_run", lambda *a, **k: "")

    class _NoLuria:
        LURIA_ENV = {"working_path": "", "key": ""}

    with pytest.raises(g.OpValidationError):
        g._run_ls({"run_dir": "/whatever"}, _NoLuria(), None, None, None, None)


RUN = "/net/bmc-pub10/data1/bmc/pipeline_cd/runs/r1"


def test_inside_an_op_the_listing_waits_at_most_the_time_left(monkeypatch):
    with call_scope.scope(deadline_s=20):
        _, seen = _call(monkeypatch, RUN, limit_s=60)
    assert 0 < seen["timeout"] <= 20


def test_with_no_time_left_nothing_is_sent(monkeypatch):
    calls = []
    monkeypatch.setattr(ssh, "prepare_key", lambda k: calls.append("key") or "/tmp/key")
    monkeypatch.setattr(ssh, "ssh_run", lambda *a, **k: calls.append("ssh") or "")
    with call_scope.scope(deadline_s=1), pytest.raises(RuntimeError):
        g._run_ls({"run_dir": RUN}, _Cfg(), None, None, None, None, limit_s=60)
    assert calls == []
