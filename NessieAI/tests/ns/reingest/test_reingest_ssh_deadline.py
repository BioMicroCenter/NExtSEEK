"""run-harvest's and run-checksum's SSH calls end inside the op's limit (PR 135 SF-5).

Both ops run for at most 60 s (op_limits.py), but their SSH calls used a fixed 150 s
timeout, so a stalled cluster kept the op running long after the agent had been told
it failed. The SSH timeout is now what is left of the op's limit less the answer
margin, nothing starts when too little is left, and an SSH timeout is a deadline
failure, as run-ls's already is.
"""
from __future__ import annotations

import subprocess

import pytest

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g
from chat_nextseek import call_scope
from NessieAI.ns.op_limits import OP_LIMITS_S
from NessieAI.tests.ns.reingest.test_run_checksum_op import (
    _cfg_for, _dispatch, _run_checksum_script_locally,
)
from NessieAI.tests.ns.reingest.test_run_harvest_op import _Cfg, _run_stage_script_locally

MARGIN_S = 5.0


def _stage(tmp_path):
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    (run_dir / "params.json").write_text("{}")
    staged = tmp_path / "staged"
    staged.mkdir()
    return run_dir, staged


def test_the_harvest_ssh_call_ends_before_the_ops_limit(tmp_path, monkeypatch):
    run_dir, staged = _stage(tmp_path)
    seen = {}

    def fake(env, cmd, *, key_path, timeout=None):
        seen["timeout"] = timeout
        return _run_stage_script_locally(str(run_dir), ["params.json"])

    monkeypatch.setattr(ssh, "ssh_run_bytes", fake)
    with call_scope.scope(deadline_s=OP_LIMITS_S["run-harvest"], op=True):
        g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")
    assert 0 < seen["timeout"] <= OP_LIMITS_S["run-harvest"] - MARGIN_S


def test_with_no_time_left_the_harvest_sends_nothing(tmp_path, monkeypatch):
    run_dir, staged = _stage(tmp_path)
    calls = []
    monkeypatch.setattr(ssh, "ssh_run_bytes", lambda *a, **k: calls.append("ssh") or b"")
    with call_scope.scope(deadline_s=4, op=True), pytest.raises(g.OpDeadlineError):
        g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")
    assert calls == []


def test_a_harvest_ssh_timeout_is_a_deadline_failure(tmp_path, monkeypatch):
    from nextseek_api.assistant.op_errors import failure_reason

    run_dir, staged = _stage(tmp_path)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(ssh.subprocess, "run", fake_run)
    with pytest.raises(g.OpDeadlineError) as exc:
        g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")
    assert failure_reason(exc.value) == "deadline"


def _checksum_dir(tmp_path):
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    (run_dir / "sample.bam").write_text("reads\n")
    return run_dir


def test_the_checksum_ssh_call_ends_before_the_ops_limit(tmp_path, monkeypatch):
    run_dir = _checksum_dir(tmp_path)
    seen = {}

    def fake(env, cmd, *, key_path, timeout=None):
        seen["timeout"] = timeout
        return _run_checksum_script_locally(str(run_dir), ["sample.bam"])

    monkeypatch.setattr(ssh, "ssh_run", fake)
    _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"}, _cfg_for(tmp_path))
    assert 0 < seen["timeout"] <= OP_LIMITS_S["run-checksum"] - MARGIN_S


def test_with_no_time_left_the_checksum_sends_nothing(tmp_path, monkeypatch):
    run_dir = _checksum_dir(tmp_path)
    calls = []
    monkeypatch.setattr(ssh, "ssh_run", lambda *a, **k: calls.append("ssh") or "{}")
    with call_scope.scope(deadline_s=4), pytest.raises(g.OpDeadlineError):
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"}, _cfg_for(tmp_path))
    assert calls == []


def test_a_checksum_ssh_timeout_is_a_deadline_failure(tmp_path, monkeypatch):
    run_dir = _checksum_dir(tmp_path)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(ssh.subprocess, "run", fake_run)
    with pytest.raises(g.OpDeadlineError):
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"}, _cfg_for(tmp_path))
