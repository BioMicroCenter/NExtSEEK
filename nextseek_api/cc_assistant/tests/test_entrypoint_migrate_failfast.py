"""Behavioral contract for docker/scripts/entrypoint.sh migrate handling.

Review follow-up FU4 (2026-07-07, user decision: FAIL-FAST): a silent
``manage.py migrate`` failure used to boot gunicorn+celery on the unhealed
schema with only a log line — exactly the masking that hid Bug C's wedged
0007 for a week. The entrypoint must now refuse to serve when migrate exits
non-zero: loud ``[MIGRATE-FAILED]`` marker on stderr, exit 1, no servers
started. Under compose ``restart: always`` this crash-loops until the wedge
is fixed (chosen over stay-up-but-loud). Remediation for the heal's
orphan-guard RuntimeError is manual row triage — never ``migrate --fake``.

These tests EXECUTE the script with a stubbed ``uv`` on PATH (no Docker, no
network) so the contract cannot be gamed by source-string tweaks.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "docker" / "scripts" / "entrypoint.sh"

_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "$CALL_LOG"
case "$*" in
  *"manage.py collectstatic"*) exit "${COLLECTSTATIC_EXIT:-0}" ;;
  *"manage.py migrate"*) exit "${MIGRATE_EXIT:-0}" ;;
  *) exit 0 ;;
esac
"""


def _run_entrypoint(
    tmp_path,
    migrate_exit: int = 0,
    collectstatic_exit: int = 0,
):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uv"
    stub.write_text(_UV_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    call_log = tmp_path / "calls.log"
    call_log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CALL_LOG": str(call_log),
        "MIGRATE_EXIT": str(migrate_exit),
        "COLLECTSTATIC_EXIT": str(collectstatic_exit),
        "NEXTSEEK_SERVER": "gunicorn",
    }
    proc = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, call_log.read_text()


class TestMigrateFailFast:
    def test_migrate_failure_exits_nonzero_without_starting_servers(
        self, tmp_path
    ):
        proc, calls = _run_entrypoint(tmp_path, migrate_exit=1)
        assert proc.returncode != 0
        assert "[MIGRATE-FAILED]" in proc.stderr
        assert "gunicorn" not in calls
        assert "celery" not in calls

    def test_migrate_failure_message_forbids_fake(self, tmp_path):
        proc, _ = _run_entrypoint(tmp_path, migrate_exit=1)
        assert "--fake" in proc.stderr

    def test_migrate_success_still_starts_servers(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, migrate_exit=0)
        assert proc.returncode == 0
        assert "manage.py migrate" in calls
        assert "gunicorn" in calls
        assert "celery" in calls


class TestCollectstaticFailFast:
    def test_collectstatic_failure_exits_without_migrate_or_servers(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, collectstatic_exit=1)
        assert proc.returncode != 0
        assert "[COLLECTSTATIC-FAILED]" in proc.stderr
        assert "manage.py migrate" not in calls
        assert "gunicorn" not in calls
        assert "celery" not in calls

    def test_collectstatic_success_proceeds_to_migrate(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, collectstatic_exit=0, migrate_exit=0)
        assert proc.returncode == 0
        assert "manage.py collectstatic" in calls
        assert "manage.py migrate" in calls
