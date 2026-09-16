"""The graph sync loop's launch line in the app container's entrypoint (spec 12).

``manage.py graph_sync --loop`` runs the schedule, drains the outbox and starts
the heavy syncs as its own child processes. It is started by
``docker/scripts/entrypoint.sh``, like every other long-running process of this
container, but it is the one runtime here that must NOT be able to end the
container: ``wait -n`` at the foot of that script returns as soon as any
background job exits, so a loop started as a bare background job would bounce
the web server every time it crashed. The launch line is therefore an ``if``
block around a backgrounded restart loop whose inner ``while`` never returns,
and with ``NEXTSEEK_GRAPH_SYNC_LOOP=0`` it creates no job at all.

These tests EXTRACT the marked block and EXECUTE it under bash with ``uv`` and
``sleep`` stubbed on PATH (no Docker, no network, no database), the way the
entrypoint guards under ``nextseek_api/tests/repo_guards/`` execute the whole
script, so the contract cannot be gamed by editing a source string. The
harness ends the block with ``wait -n``, the same line the real entrypoint
ends with, which is what turns "the loop stays up" into an assertion: with the
loop running, that ``wait -n`` must never return.
"""
from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker" / "scripts" / "entrypoint.sh"
BEGIN_MARKER = "# --- graph sync loop (BEGIN)"
END_MARKER = "# --- graph sync loop (END)"

# Logs the command it was asked to run and exits with UV_EXIT, so a test can
# make the loop's child fail (the crash case) or succeed (a clean exit still
# has to be restarted: the loop is meant to run forever).
_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "$CALL_LOG"
exit "${UV_EXIT:-1}"
"""

# Logs the delay it was asked for, then parks. Without the park the restart
# loop would spin as fast as bash can fork for as long as the test watched it,
# and the number of starts would be a race rather than a fact. After
# SLEEP_PARK_AFTER calls this blocks on a fifo nothing ever opens for writing,
# so the loop stops at a known number of starts and stays alive, which is
# exactly the state the assertions need. Only bash builtins here: the stub
# shadows the real `sleep`, and nothing else may be assumed present.
_SLEEP_STUB = """#!/usr/bin/env bash
echo "sleep $*" >> "$CALL_LOG"
count=0
[ -s "$SLEEP_COUNT" ] && read -r count < "$SLEEP_COUNT"
count=$((count + 1))
echo "$count" > "$SLEEP_COUNT"
if [ "$count" -ge "${SLEEP_PARK_AFTER:-2}" ]; then
  read -r _ < "$PARK_FIFO"
fi
exit 0
"""

# The harness script: the block as the entrypoint has it, a line proving the
# block returned instead of running the loop in the foreground, then the
# entrypoint's own `wait -n` and a line that appears only if it ever returns.
_HARNESS = """
echo "block returned" >> "$CALL_LOG"
wait -n
echo "wait -n returned $?" >> "$CALL_LOG"
"""


def _entrypoint_lines() -> list[str]:
    return ENTRYPOINT.read_text(encoding="utf-8").splitlines()


def _marker_index(lines: list[str], marker: str) -> int:
    hits = [i for i, line in enumerate(lines) if line.startswith(marker)]
    assert len(hits) == 1, f"expected exactly one {marker!r} line, got {hits!r}"
    return hits[0]


def _block_bounds(lines: list[str]) -> tuple[int, int]:
    begin = _marker_index(lines, BEGIN_MARKER)
    end = _marker_index(lines, END_MARKER)
    assert begin < end, "the graph sync loop's END marker comes before its BEGIN marker"
    return begin, end


def _block_text() -> str:
    lines = _entrypoint_lines()
    begin, end = _block_bounds(lines)
    return "\n".join(lines[begin + 1 : end]) + "\n"


def _block_code_lines() -> list[str]:
    return [
        line
        for line in _block_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _write_stub(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _Run:
    """One execution of the marked block, plus the log its stubs write."""

    def __init__(self, proc: subprocess.Popen, call_log: Path):
        self.proc = proc
        self.call_log = call_log

    @property
    def calls(self) -> str:
        return self.call_log.read_text(encoding="utf-8")

    def starts(self) -> int:
        return sum(
            1 for line in self.calls.splitlines() if "manage.py graph_sync --loop" in line
        )

    def wait_until(self, predicate, what: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        calls = self.calls
        while time.monotonic() < deadline:
            calls = self.calls
            if predicate(calls):
                return calls
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}; the log holds:\n{calls}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self.proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - the kill above failed
            self.proc.kill()
            self.proc.communicate()


@pytest.fixture
def run_block(tmp_path):
    """Runs the marked block under bash with `uv` and `sleep` stubbed on PATH."""
    started: list[_Run] = []

    def factory(**env_overrides) -> _Run:
        work = tmp_path / f"run{len(started)}"
        bindir = work / "bin"
        bindir.mkdir(parents=True)
        _write_stub(bindir / "uv", _UV_STUB)
        _write_stub(bindir / "sleep", _SLEEP_STUB)
        call_log = work / "calls.log"
        call_log.write_text("", encoding="utf-8")
        park_fifo = work / "park.fifo"
        os.mkfifo(park_fifo)
        script = work / "block.sh"
        script.write_text(_block_text() + _HARNESS, encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "CALL_LOG": str(call_log),
            "SLEEP_COUNT": str(work / "sleep.count"),
            "PARK_FIFO": str(park_fifo),
            **{k: str(v) for k, v in env_overrides.items()},
        }
        # A box that exports either variable must not decide what a test runs.
        for name in ("NEXTSEEK_GRAPH_SYNC_LOOP", "GRAPH_SYNC_RESTART_DELAY"):
            if name not in env_overrides:
                env.pop(name, None)
        proc = subprocess.Popen(
            ["bash", str(script)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        run = _Run(proc, call_log)
        started.append(run)
        return run

    yield factory
    for run in started:
        run.stop()


class TestTheBlockIsAnIfBlock:
    """Structure, because the shape is the whole safety property.

    A backgrounded test (`[ ... ] && ( ... ) &`) would read as equivalent and is
    not: the job exists whatever the variable says, so `wait -n` can see it end.
    """

    def test_the_block_is_marked_exactly_once_and_sits_before_wait_n(self):
        lines = _entrypoint_lines()
        begin, end = _block_bounds(lines)
        waits = [i for i, line in enumerate(lines) if line.strip().startswith("wait -n")]
        assert len(waits) == 1, f"expected exactly one `wait -n`, got {waits!r}"
        assert end < waits[0], "the graph sync loop starts after `wait -n`, so it never starts"
        assert begin > 0

    def test_the_block_opens_with_if_and_closes_with_fi(self):
        code = _block_code_lines()
        assert code, "the marked block holds no code"
        assert code[0].startswith("if "), f"the block does not open with `if`: {code[0]!r}"
        assert code[-1].strip() == "fi", f"the block does not close with `fi`: {code[-1]!r}"

    def test_the_block_reads_the_off_switch_and_backgrounds_the_loop(self):
        code = "\n".join(_block_code_lines())
        assert "NEXTSEEK_GRAPH_SYNC_LOOP" in code
        assert "graph_sync --loop" in code
        assert code.rstrip().endswith("fi")
        assert "&" in code, "the loop is not backgrounded, so the entrypoint never reaches `wait -n`"

    def test_the_block_waits_on_nothing_itself(self):
        code = "\n".join(_block_code_lines())
        assert not re.search(r"\bwait\b", code), (
            "the block waits on its own job, which hands the loop's exit to the "
            "entrypoint and takes the container down with it"
        )

    def test_bash_accepts_the_whole_file(self):
        proc = subprocess.run(
            ["bash", "-n", str(ENTRYPOINT)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr


class TestTheOffSwitch:
    """`NEXTSEEK_GRAPH_SYNC_LOOP=0` (spec 12): no job, not a job that does nothing."""

    def test_zero_starts_no_background_job(self, run_block):
        run = run_block(NEXTSEEK_GRAPH_SYNC_LOOP=0)
        run.proc.wait(timeout=60)
        calls = run.calls
        assert "block returned" in calls
        assert "graph_sync" not in calls
        assert "sleep" not in calls

    def test_zero_leaves_wait_n_nothing_to_wait_for(self, run_block):
        """With no job created, `wait -n` returns at once: the script's other
        background processes are the only thing keeping the container up."""
        run = run_block(NEXTSEEK_GRAPH_SYNC_LOOP=0)
        run.proc.wait(timeout=60)
        assert "wait -n returned" in run.calls


class TestTheLoopStarts:
    """On by default (the operator's ruling, spec 12)."""

    def test_the_default_is_on(self, run_block):
        run = run_block()
        run.wait_until(lambda c: "graph_sync --loop" in c, "the loop's first start")

    def test_an_explicit_one_is_on(self, run_block):
        run = run_block(NEXTSEEK_GRAPH_SYNC_LOOP=1)
        run.wait_until(lambda c: "graph_sync --loop" in c, "the loop's first start")

    def test_any_other_value_is_off(self, run_block):
        """Only `1` is on, so a typo in a box's env file fails safe."""
        run = run_block(NEXTSEEK_GRAPH_SYNC_LOOP="true")
        run.proc.wait(timeout=60)
        assert "graph_sync" not in run.calls

    def test_the_command_line_is_the_loop_the_container_needs(self, run_block):
        run = run_block()
        calls = run.wait_until(
            lambda c: "graph_sync --loop" in c, "the loop's first start"
        )
        line = next(x for x in calls.splitlines() if "graph_sync --loop" in x)
        assert line == "uv run --no-sync python manage.py graph_sync --loop", line

    def test_the_block_returns_instead_of_running_the_loop_in_the_foreground(
        self, run_block
    ):
        """In the foreground it would never reach `wait -n`, so the web server
        and the workers would never be waited on at all."""
        run = run_block()
        run.wait_until(lambda c: "block returned" in c, "the block to return")


class TestTheLoopRestartsAndNeverEndsTheContainer:
    def test_a_crash_is_followed_by_another_start(self, run_block):
        run = run_block(UV_EXIT=1, SLEEP_PARK_AFTER=3)
        run.wait_until(lambda c: run.starts() >= 3, "three starts after two crashes")

    def test_a_clean_exit_is_also_followed_by_another_start(self, run_block):
        """The loop is not supposed to return at all; if it does, restarting it
        is the only thing that keeps the schedule running."""
        run = run_block(UV_EXIT=0, SLEEP_PARK_AFTER=3)
        run.wait_until(lambda c: run.starts() >= 3, "three starts after two clean exits")

    def test_wait_n_never_returns_while_the_loop_lives(self, run_block):
        """The container invariant: whatever the loop does, `wait -n` must not
        see it, or every crash of the loop bounces the web server."""
        run = run_block(UV_EXIT=1, SLEEP_PARK_AFTER=2)
        run.wait_until(lambda c: run.starts() >= 2, "two starts")
        time.sleep(0.5)
        assert "wait -n returned" not in run.calls
        assert run.proc.poll() is None, "the script ended although the loop is alive"


class TestTheRestartDelay:
    def test_it_defaults_to_sixty_seconds(self, run_block):
        run = run_block(UV_EXIT=1, SLEEP_PARK_AFTER=1)
        calls = run.wait_until(lambda c: "sleep " in c, "the delay between starts")
        assert "sleep 60" in calls, calls

    def test_it_is_configurable(self, run_block):
        run = run_block(UV_EXIT=1, SLEEP_PARK_AFTER=1, GRAPH_SYNC_RESTART_DELAY=7)
        calls = run.wait_until(lambda c: "sleep " in c, "the delay between starts")
        assert "sleep 7" in calls, calls
