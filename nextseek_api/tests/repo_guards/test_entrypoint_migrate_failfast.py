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

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "docker" / "scripts" / "entrypoint.sh"

_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "$CALL_LOG"
case "$*" in
  *"MySQLdb.connect"*) exit "${DB_PROBE_EXIT:-0}" ;;
  *"manage.py collectstatic"*) exit "${COLLECTSTATIC_EXIT:-0}" ;;
  *"manage.py migrate"*) exit "${MIGRATE_EXIT:-0}" ;;
  *) exit 0 ;;
esac
"""


def _run_entrypoint(
    tmp_path,
    migrate_exit: int = 0,
    collectstatic_exit: int = 0,
    db_probe_exit: int = 0,
    db_wait_attempts: int = 2,
    mode: str = "worker",
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
        "DB_PROBE_EXIT": str(db_probe_exit),
        "DB_WAIT_ATTEMPTS": str(db_wait_attempts),
        "DB_WAIT_INTERVAL": "0",
        "NEXTSEEK_SERVER": "gunicorn",
        # The graph sync loop restarts itself forever, so it outlives this
        # script and holds open the pipes `capture_output` reads to EOF: left
        # on, every run here blocks until the timeout. Its own contract is
        # `nextseek_api/tests/test_graph_sync_entrypoint.py`, which runs the
        # marked block by itself with `sleep` stubbed too.
        "NEXTSEEK_GRAPH_SYNC_LOOP": "0",
    }
    proc = subprocess.run(
        ["bash", str(ENTRYPOINT), mode],
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

    def test_migrate_success_still_starts_the_loops(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, migrate_exit=0)
        assert proc.returncode == 0
        assert "manage.py migrate" in calls
        assert "gunicorn" not in calls  # the worker serves nothing; the web mode does
        assert "celery" in calls


class TestCollectstaticFailFast:
    def test_collectstatic_failure_exits_without_migrate_or_servers(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, collectstatic_exit=1, mode="web")
        assert proc.returncode != 0
        assert "[COLLECTSTATIC-FAILED]" in proc.stderr
        assert "manage.py migrate" not in calls
        assert "gunicorn" not in calls
        assert "celery" not in calls

    def test_collectstatic_success_proceeds_to_migrate(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, collectstatic_exit=0, migrate_exit=0, mode="web")
        assert proc.returncode == 0
        assert "manage.py collectstatic" in calls
        assert "manage.py migrate" in calls


class TestDbReadinessProbe:
    def test_db_unreachable_exits_with_distinct_marker_no_migrate(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, db_probe_exit=1)
        assert proc.returncode != 0
        assert "[DB-UNREACHABLE]" in proc.stderr
        assert "[MIGRATE-FAILED]" not in proc.stderr
        assert "manage.py migrate" not in calls
        assert "gunicorn" not in calls

    def test_db_ready_then_migrate_runs(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, db_probe_exit=0, migrate_exit=0)
        assert proc.returncode == 0
        assert "MySQLdb.connect" in calls
        assert "manage.py migrate" in calls
        assert "celery" in calls

    def test_migrate_failure_with_db_up_still_reports_migrate_failed(self, tmp_path):
        proc, _ = _run_entrypoint(tmp_path, db_probe_exit=0, migrate_exit=1)
        assert proc.returncode != 0
        assert "[MIGRATE-FAILED]" in proc.stderr
        assert "[DB-UNREACHABLE]" not in proc.stderr

    def test_probe_retries_configured_attempts_before_giving_up(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path, db_probe_exit=1, db_wait_attempts=3)
        assert proc.returncode != 0
        assert "[DB-UNREACHABLE]" in proc.stderr
        assert calls.count("MySQLdb.connect") == 3


# The stub tests above only see the probe's command line. These run its Python
# against a fake MySQLdb: a misspelt key would otherwise pass every test and
# crash-loop a box with [DB-UNREACHABLE].
_FAKE_MYSQLDB = """import json, os
class _Conn:
    def close(self):
        pass
def connect(**kw):
    if os.environ.get("FAKE_DB_DOWN"):
        raise OSError("connection refused")
    with open(os.environ["PROBE_LOG"], "w") as fh:
        json.dump(kw, fh)
    return _Conn()
"""


def _db_probe_code() -> str:
    match = re.search(r"^_DB_PROBE='(.*)'$", ENTRYPOINT.read_text(), re.M)
    assert match, "entrypoint.sh no longer defines _DB_PROBE on one line"
    return match.group(1)


def _run_probe(tmp_path, **extra_env):
    (tmp_path / "MySQLdb.py").write_text(_FAKE_MYSQLDB)
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "PROBE_LOG": str(tmp_path / "kw.json"),
        "MYSQL_HOST": "db",
        "MYSQL_USER": 'seek "user"',
        "MYSQL_PASSWORD": "pa$s w'rd",
        "NEXTSEEK_MYSQL_DATABASE": "dmac",
        **extra_env,
    }
    return subprocess.run([sys.executable, "-c", _db_probe_code()], env=env, capture_output=True, text=True)


class TestDbProbeCode:
    def test_probe_connects_with_the_values_from_the_environment(self, tmp_path):
        proc = _run_probe(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert json.loads((tmp_path / "kw.json").read_text()) == {
            "host": "db", "port": 3306, "user": 'seek "user"', "password": "pa$s w'rd",
            "database": "dmac", "connect_timeout": 5,
        }

    def test_probe_reads_the_names_django_reads_for_the_default_database(self):
        settings = (REPO_ROOT / "dmac" / "settings.py").read_text()
        default = re.search(r'"default":\s*\{(.*?)\}', settings, re.S).group(1)
        code = _db_probe_code()
        for key, var in (("NAME", "NEXTSEEK_MYSQL_DATABASE"), ("USER", "MYSQL_USER"),
                         ("PASSWORD", "MYSQL_PASSWORD"), ("HOST", "MYSQL_HOST")):
            assert f'"{key}": os.getenv("{var}")' in default, key
            assert f'os.environ["{var}"]' in code, var
        assert '"PORT": "3306"' in default and "port=3306" in code

    def test_probe_fails_when_the_database_refuses(self, tmp_path):
        assert _run_probe(tmp_path, FAKE_DB_DOWN="1").returncode != 0
