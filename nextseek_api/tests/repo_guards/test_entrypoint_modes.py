"""The app image's two entrypoint modes: ``worker`` (compose service ``nextseek``) and ``web``.

The worker is the only migrator and runs the background loops; it serves nothing. The web mode runs collectstatic,
waits (bounded) for the worker's migrations without ever migrating, starts the server, and drains on a stop: it
raises the ``.draining`` flag, waits for running chat turns (their marker files) up to ``NEXTSEEK_DRAIN_SECONDS``,
ends the rest with ``end_cut_turns`` and only then stops the server.

Like its neighbours these tests EXECUTE the script with a stubbed ``uv`` on PATH (no Docker, no network).
"""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "docker" / "scripts" / "entrypoint.sh"

# The server stub stays up until it is told to stop, like gunicorn; every other command logs and exits.
_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "$CALL_LOG"
case "$*" in
  *"MySQLdb.connect"*) exit 0 ;;
  *"migrate --check"*) exit "${MIGRATE_CHECK_EXIT:-0}" ;;
  *"manage.py migrate"*) exit 0 ;;
  *gunicorn*)
    trap 'echo server-stopped >> "$CALL_LOG"; exit 0' TERM
    while :; do sleep 0.05; done ;;
  *) exit 0 ;;
esac
"""


def _env(tmp_path, **overrides):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "uv"
    stub.write_text(_UV_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    call_log = tmp_path / "calls.log"
    call_log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CALL_LOG": str(call_log),
        "DB_WAIT_ATTEMPTS": "2",
        "DB_WAIT_INTERVAL": "0",
        "NEXTSEEK_SERVER": "gunicorn",
        "NEXTSEEK_GRAPH_SYNC_LOOP": "0",
        "NEXTSEEK_TURN_DIR": str(tmp_path / "turns"),
        "NEXTSEEK_DRAIN_POLL": "0.1",
        **{k: str(v) for k, v in overrides.items()},
    }
    return env, call_log


def _run(tmp_path, *args, **overrides):
    env, call_log = _env(tmp_path, **overrides)
    proc = subprocess.run(["bash", str(ENTRYPOINT), *args], env=env, capture_output=True, text=True, timeout=30)
    return proc, call_log.read_text()


def _start_web(tmp_path, **overrides):
    env, call_log = _env(tmp_path, **overrides)
    proc = subprocess.Popen(["bash", str(ENTRYPOINT), "web"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    deadline = time.monotonic() + 20
    while "gunicorn" not in call_log.read_text() or not (tmp_path / "turns").is_dir():
        assert time.monotonic() < deadline, "the web server never started"
        time.sleep(0.05)
    time.sleep(0.2)  # the trap is set right after the server starts
    return proc, call_log


def _stop(proc):
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=30)
    return proc.returncode, err


class TestModes:
    def test_worker_is_the_default_and_does_not_serve_or_collectstatic(self, tmp_path):
        proc, calls = _run(tmp_path)
        assert proc.returncode == 0
        assert "manage.py migrate --noinput" in calls
        assert "mark_interrupted_batch_jobs" in calls
        assert "celery" in calls
        assert "collectstatic" not in calls
        assert "gunicorn" not in calls and "daphne" not in calls

    def test_web_collects_static_and_serves_but_never_migrates_or_runs_loops(self, tmp_path):
        env, call_log = _env(tmp_path)
        proc = subprocess.Popen(["bash", str(ENTRYPOINT), "web"], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        deadline = time.monotonic() + 20
        while "gunicorn" not in call_log.read_text():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        time.sleep(0.2)
        _stop(proc)
        calls = call_log.read_text()
        assert "manage.py collectstatic" in calls
        assert "migrate --check" in calls
        assert "migrate --noinput" not in calls
        assert "celery" not in calls and "mark_interrupted" not in calls

    def test_web_gives_up_when_migrations_stay_pending(self, tmp_path):
        proc, calls = _run(tmp_path, "web", MIGRATE_CHECK_EXIT=1,
                           NEXTSEEK_MIGRATE_WAIT_SECONDS=1, NEXTSEEK_MIGRATE_WAIT_INTERVAL=0.2)
        assert proc.returncode == 1
        assert "[MIGRATE-WAIT-FAILED]" in proc.stderr
        assert "gunicorn" not in calls
        assert "migrate --noinput" not in calls

    def test_a_stop_before_the_server_starts_exits_at_once(self, tmp_path):
        """In a container bash is PID 1 and ignores a TERM it has no trap for, so a stop during collectstatic or the
        migrate wait used to sit out the whole grace period. Web mode traps TERM from its first line and exits 143
        (here, outside a container, an untrapped TERM would kill bash instead: returncode -15)."""
        env, call_log = _env(tmp_path, MIGRATE_CHECK_EXIT=1, NEXTSEEK_MIGRATE_WAIT_SECONDS=60,
                             NEXTSEEK_MIGRATE_WAIT_INTERVAL=0.2)
        proc = subprocess.Popen(["bash", str(ENTRYPOINT), "web"], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        deadline = time.monotonic() + 20
        while "migrate --check" not in call_log.read_text():
            assert time.monotonic() < deadline, "the migrate wait never started"
            time.sleep(0.05)
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
        assert proc.returncode == 143
        assert time.monotonic() - t0 < 3
        assert "gunicorn" not in call_log.read_text()

    def test_unknown_mode_is_refused(self, tmp_path):
        proc, calls = _run(tmp_path, "sidecar")
        assert proc.returncode == 2
        assert calls == ""


class TestWebDrain:
    def test_stop_with_no_turns_is_immediate(self, tmp_path):
        proc, call_log = _start_web(tmp_path)
        t0 = time.monotonic()
        code, err = _stop(proc)
        assert time.monotonic() - t0 < 3
        assert code == 143
        assert (tmp_path / "turns" / ".draining").exists()
        calls = call_log.read_text()
        assert "server-stopped" in calls
        assert "end_cut_turns" not in calls

    def test_stop_waits_for_a_running_turn_then_stops_without_cutting(self, tmp_path):
        proc, call_log = _start_web(tmp_path)
        marker = tmp_path / "turns" / "task-1"
        marker.write_text("")
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.6)
        assert proc.poll() is None, "the server was stopped while a turn was still running"
        assert "server-stopped" not in call_log.read_text()
        marker.unlink()
        out, err = proc.communicate(timeout=30)
        assert "[drain] 1 turns running" in err
        calls = call_log.read_text()
        assert "server-stopped" in calls and "end_cut_turns" not in calls

    @pytest.mark.parametrize("bad", ["4m", "300.5"])
    def test_an_unparseable_drain_falls_back_to_the_default_and_still_stops_cleanly(self, tmp_path, bad):
        """A value test(1) cannot compare used to abort the stop handler: no clamp, no end_cut_turns, no clean stop."""
        proc, call_log = _start_web(tmp_path, NEXTSEEK_DRAIN_SECONDS=bad)
        code, err = _stop(proc)
        assert code == 143
        assert f"NEXTSEEK_DRAIN_SECONDS={bad}" in err and "using 240" in err
        assert "server-stopped" in call_log.read_text()

    def test_turns_past_the_cap_are_ended_before_the_server_stops(self, tmp_path):
        proc, call_log = _start_web(tmp_path, NEXTSEEK_DRAIN_SECONDS=1)
        (tmp_path / "turns" / "task-a").write_text("")
        (tmp_path / "turns" / "task-b").write_text("")
        code, err = _stop(proc)
        assert code == 143
        calls = call_log.read_text().splitlines()
        cut = [i for i, line in enumerate(calls) if "manage.py end_cut_turns" in line]
        assert len(cut) == 1
        assert "task-a" in calls[cut[0]] and "task-b" in calls[cut[0]]
        assert calls.index("server-stopped") > cut[0]
