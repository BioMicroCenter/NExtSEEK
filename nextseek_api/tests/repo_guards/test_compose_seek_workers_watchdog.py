"""seek_workers exits when its delayed_job workers lose MySQL, so docker restarts them.

On 2026-10-09 a `docker restart seek-mysql` left the workers alive but
disconnected: they logged "MySQL client is not connected" on every poll and
909 jobs sat unrun, while the image's start_workers.sh kept the container up
with `tail -f`. The compose command watches the log and exits on that line.

These tests EXECUTE the compose command under bash in a scratch directory,
with docker/start_workers.sh stubbed (no Docker, no Rails, no MySQL).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
LOST = "Error while reserving job: MySQL client is not connected"

# Stands in for the image's script: makes the log, then stays up the way its
# `tail -f` does. The test writes the workers' lines into the log itself.
_START_WORKERS_STUB = """#!/usr/bin/env bash
mkdir -p log
touch log/production.log
echo started >> started.txt
exec sleep 60
"""


def _command() -> list[str]:
    return yaml.safe_load(COMPOSE.read_text())["services"]["seek_workers"]["command"]


def _start(tmp_path: Path, old_log: str = "") -> subprocess.Popen:
    (tmp_path / "docker").mkdir()
    stub = tmp_path / "docker" / "start_workers.sh"
    stub.write_text(_START_WORKERS_STUB)
    stub.chmod(0o755)
    if old_log:
        (tmp_path / "log").mkdir()
        (tmp_path / "log" / "production.log").write_text(old_log)
    proc = subprocess.Popen(
        _command(), cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not (tmp_path / "started.txt").exists():
        assert time.monotonic() < deadline, "start_workers.sh never ran"
        time.sleep(0.05)
    time.sleep(1.5)  # tail -F opens the file and starts following
    return proc


def _log(tmp_path: Path, line: str) -> None:
    with open(tmp_path / "log" / "production.log", "a") as fh:
        fh.write(line + "\n")


def _stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def test_the_command_still_starts_the_image_script():
    command = _command()
    assert command[:2] == ["bash", "-c"]
    assert command[2].startswith("docker/start_workers.sh &")
    assert "$" not in command[2], "compose would interpolate a dollar sign"


def test_exits_nonzero_on_the_first_lost_mysql_line(tmp_path):
    proc = _start(tmp_path)
    try:
        _log(tmp_path, "INFO -- : [Worker(delayed_job.1)] Job ran fine")
        time.sleep(1.5)
        assert proc.poll() is None, "exited on an ordinary line"
        _log(tmp_path, f"INFO -- : [Worker(delayed_job.1)] {LOST}")
        assert proc.wait(timeout=10) == 1
    finally:
        _stop(proc)
    # Only now: the stub's orphaned sleep holds stderr open until the group dies.
    assert "lost MySQL" in proc.stderr.read()


def test_a_lost_mysql_line_from_before_this_start_is_ignored(tmp_path):
    """After the restart the log still holds the line that caused it."""
    proc = _start(tmp_path, old_log=f"INFO -- : [Worker(delayed_job.1)] {LOST}\n")
    try:
        time.sleep(1.5)
        assert proc.poll() is None, "re-triggered on the previous start's line"
    finally:
        _stop(proc)
