"""seek recycles a puma worker before the container's memory cap does.

On 2026-10-12 two of SEEK's puma workers grew to about 7 GB each on a dev box,
the cgroup sat at its 20 G cap, and every process stalled in memory reclaim: SEEK
answered nothing for 7 minutes until the kernel OOM-killed a worker. The compose
command now runs the image's entrypoint in the background and sends SIGTERM to
any puma worker whose RSS passes SEEK_WORKER_MAX_MB; puma lets it finish its
request and forks a fresh one from the preloaded app.

These tests EXECUTE the compose command under bash in a scratch directory, with
docker/entrypoint.sh stubbed by processes titled like puma workers that hold real
memory (no Docker, no Ruby, no SEEK).
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Two fake workers titled the way puma 5 titles them, one holding BIG_MB of
# resident memory, then the foreground process the real entrypoint keeps (nginx).
_ENTRYPOINT_STUB = """#!/usr/bin/env bash
( exec -a "puma: cluster worker 0: 13 [seek]" "$PY" -c "import time; x = b'x' * ($BIG_MB << 20); time.sleep(60)" ) &
echo $! > big.pid
( exec -a "puma: cluster worker 1: 13 [seek]" "$PY" -c "import time; time.sleep(60)" ) &
echo $! > small.pid
echo $$ > entrypoint.pid
exec sleep 60
"""


def _service():
    return yaml.safe_load(COMPOSE.read_text())["services"]["seek"]


def _command() -> list[str]:
    # Compose turns `$$` into `$` before the container sees the command.
    return [part.replace("$$", "$") for part in _service()["command"]]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child that nobody reaped yet is a zombie: dead for our purposes.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


def _wait_for(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.1)


def _start(tmp_path: Path, big_mb: int, max_mb: int) -> subprocess.Popen:
    (tmp_path / "docker").mkdir()
    stub = tmp_path / "docker" / "entrypoint.sh"
    stub.write_text(_ENTRYPOINT_STUB)
    stub.chmod(0o755)
    env = {**os.environ, "PY": sys.executable, "BIG_MB": str(big_mb),
           "SEEK_WORKER_MAX_MB": str(max_mb), "SEEK_WORKER_CHECK_S": "1"}
    proc = subprocess.Popen(_command(), cwd=tmp_path, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    _wait_for(lambda: all((tmp_path / f).exists() for f in ("big.pid", "small.pid", "entrypoint.pid")), 10, "the stub")
    return proc


def _stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def test_the_command_runs_the_image_entrypoint_and_compose_interpolates_nothing():
    command = _service()["command"]
    assert command[:2] == ["bash", "-c"]
    assert command[2].startswith("docker/entrypoint.sh &")
    # Every `$` is doubled, so compose leaves the script to bash in the container.
    assert not re.search(r"(?<!\$)\$(?!\$)", command[2].replace("$$", ""))
    assert _service()["environment"]["SEEK_WORKER_MAX_MB"] == "${SEEK_WORKER_MAX_MB:-2500}"


def test_a_worker_over_the_ceiling_is_recycled_and_a_small_one_is_left(tmp_path):
    proc = _start(tmp_path, big_mb=200, max_mb=100)
    try:
        big = int((tmp_path / "big.pid").read_text())
        small = int((tmp_path / "small.pid").read_text())
        _wait_for(lambda: not _alive(big), 15, "the big worker to be recycled")
        assert _alive(small), "a worker under the ceiling was signalled"
        assert proc.poll() is None, "the recycler must keep running"
    finally:
        _stop(proc)
    assert f"recycling puma worker {big} at" in proc.stderr.read()


def test_nothing_is_recycled_under_the_ceiling(tmp_path):
    proc = _start(tmp_path, big_mb=50, max_mb=500)
    try:
        big = int((tmp_path / "big.pid").read_text())
        time.sleep(3)
        assert _alive(big)
    finally:
        _stop(proc)
    assert "recycling" not in proc.stderr.read()


def test_the_container_exits_when_the_entrypoint_does(tmp_path):
    proc = _start(tmp_path, big_mb=10, max_mb=500)
    try:
        # The stub's foreground `sleep` stands in for nginx: end it, as a crash would.
        os.kill(int((tmp_path / "entrypoint.pid").read_text()), signal.SIGKILL)
        assert proc.wait(timeout=10) == 1
    finally:
        _stop(proc)
    assert "docker/entrypoint.sh exited" in proc.stderr.read()
