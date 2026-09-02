"""Behavioral contract for the app container's background runtimes.

The attribute-mutation worker, the outbox dispatcher, the sync-recovery
scheduler and the assay-registration drain loop used to be four separate
compose services behind two `profiles:` keys. Nothing persisted
COMPOSE_PROFILES, so `./startup.sh rebuild` moved the app to the new image and
left them on the old one under `restart: unless-stopped`, which looks healthy.
They now run as background processes of the app container, the way the
batch_upload worker already did, so there is nothing left to leave behind.

Lives beside `test_entrypoint_migrate_failfast.py` and reuses its shape: these
tests EXECUTE the script with a stubbed `uv` on PATH (no Docker, no network),
so the contract cannot be gamed by editing a source string. It is not under
`nextseek_api/attributes/tests/` because that package's conftest declares an
application plugin that imports MySQLdb, which this needs none of.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "docker" / "scripts" / "entrypoint.sh"

# Logs the broker URL each process was started WITH, which is the whole point of
# the per-process prefix: one container, two brokers. `${VAR-<unset>}` (no colon)
# distinguishes unset from empty.
#
# The fall-through case lingers instead of exiting so that `wait -n` cannot
# return before every background process has written its line. Without it the
# script exits on whichever stub wins the race and the log is short by however
# many processes had not been scheduled yet.
_UV_STUB = """#!/usr/bin/env bash
echo "CELERY_BROKER_URL=${CELERY_BROKER_URL-<unset>} uv $*" >> "$CALL_LOG"
case "$*" in
  *"ensure_connection"*) exit "${DB_PROBE_EXIT:-0}" ;;
  *"manage.py collectstatic"*) exit "${COLLECTSTATIC_EXIT:-0}" ;;
  *"manage.py migrate"*) exit "${MIGRATE_EXIT:-0}" ;;
  *) sleep "${STUB_LINGER:-0.5}"; exit 0 ;;
esac
"""


def _run_entrypoint(tmp_path, migrate_exit: int = 0, **env_overrides):
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
        "DB_WAIT_ATTEMPTS": "2",
        "DB_WAIT_INTERVAL": "0",
        "NEXTSEEK_SERVER": "gunicorn",
        **{k: str(v) for k, v in env_overrides.items()},
    }
    proc = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc, call_log.read_text()


def _line_for(calls: str, needle: str) -> str:
    matches = [line for line in calls.splitlines() if needle in line]
    assert len(matches) == 1, f"expected exactly one {needle!r} line, got {matches!r}"
    return matches[0]


class TestEveryRuntimeStarts:
    """All six processes, or the fold silently dropped one."""

    def test_the_web_server_and_all_five_workers_start(self, tmp_path):
        proc, calls = _run_entrypoint(tmp_path)
        assert proc.returncode == 0
        assert "gunicorn" in calls
        assert "-Q batch_upload" in calls
        assert "-Q attribute_mutations" in calls
        assert "manage.py dispatch_attribute_outbox" in calls
        assert "manage.py recover_attribute_sync_jobs" in calls
        assert "manage.py run_assay_registration_jobs" in calls

    def test_migrate_failure_starts_none_of_them(self, tmp_path):
        """The fail-fast guard must cover the processes the fold added, not just
        the two it was written for."""
        proc, calls = _run_entrypoint(tmp_path, migrate_exit=1)
        assert proc.returncode != 0
        assert "[MIGRATE-FAILED]" in proc.stderr
        for absent in (
            "gunicorn",
            "celery",
            "dispatch_attribute_outbox",
            "recover_attribute_sync_jobs",
            "run_assay_registration_jobs",
        ):
            assert absent not in calls, absent


class TestBrokerIsolation:
    """One container, two brokers. Taisha's constraint 4, preserved literally.

    The attribute queue keeps the durable volume it had as its own service. The
    batch_upload queue keeps the container-local default it had as a process of
    this container. A single container-wide CELERY_BROKER_URL would silently
    move batch_upload onto a volume that survives a recreate, and a queued
    upload would then re-run after a deploy.
    """

    def test_the_attribute_worker_gets_the_durable_broker(self, tmp_path):
        _, calls = _run_entrypoint(tmp_path)
        line = _line_for(calls, "-Q attribute_mutations")
        assert "/var/lib/attribute-broker/broker.sqlite3" in line

    def test_the_batch_upload_worker_keeps_the_container_default(self, tmp_path):
        _, calls = _run_entrypoint(tmp_path)
        line = _line_for(calls, "-Q batch_upload")
        assert "CELERY_BROKER_URL=<unset>" in line, (
            "the attribute broker leaked out of its process prefix into the "
            "batch_upload worker"
        )

    def test_the_dispatcher_publishes_to_the_same_broker_it_consumes_from(
        self, tmp_path
    ):
        """Sole publisher (constraint 3) is only true if it publishes where the
        worker is listening."""
        _, calls = _run_entrypoint(tmp_path)
        worker = _line_for(calls, "-Q attribute_mutations")
        dispatcher = _line_for(calls, "dispatch_attribute_outbox")
        broker = "CELERY_BROKER_URL=sqla+sqlite:////var/lib/attribute-broker/broker.sqlite3"
        assert broker in worker
        assert broker in dispatcher

    def test_the_recovery_scheduler_gets_no_broker_and_runs_no_celery(self, tmp_path):
        """Constraint 2: it can never consume either queue. As a service that was
        enforced by giving it no broker volume and no Celery command; as a
        process it is enforced here."""
        _, calls = _run_entrypoint(tmp_path)
        line = _line_for(calls, "recover_attribute_sync_jobs")
        assert "CELERY_BROKER_URL=<unset>" in line
        assert "celery" not in line

    def test_the_assay_registration_worker_gets_no_broker_and_runs_no_celery(
        self, tmp_path
    ):
        """It uses no Celery and no queue, only the durable MySQL job row and its
        compare-and-set lease."""
        _, calls = _run_entrypoint(tmp_path)
        line = _line_for(calls, "run_assay_registration_jobs")
        assert "CELERY_BROKER_URL=<unset>" in line
        assert "celery" not in line


class TestRetiredServiceArguments:
    """Every argument the retired compose services carried, carried forward.

    A fold that drops an argument produces a worker that runs and does the wrong
    thing, which is harder to notice than one that does not start.
    """

    def test_the_attribute_worker_keeps_its_hostname_and_queue(self, tmp_path):
        _, calls = _run_entrypoint(tmp_path)
        line = _line_for(calls, "-Q attribute_mutations")
        assert "--hostname=attribute_mutations@%h" in line

    def test_attribute_worker_concurrency_defaults_to_one(self, tmp_path):
        _, calls = _run_entrypoint(tmp_path)
        assert "--concurrency=1" in _line_for(calls, "-Q attribute_mutations")

    def test_attribute_worker_concurrency_is_configurable(self, tmp_path):
        _, calls = _run_entrypoint(
            tmp_path, ATTRIBUTE_MUTATION_WORKER_CONCURRENCY=4
        )
        assert "--concurrency=4" in _line_for(calls, "-Q attribute_mutations")

    def test_the_recovery_loop_keeps_its_interval(self, tmp_path):
        _, calls = _run_entrypoint(tmp_path)
        line = _line_for(calls, "recover_attribute_sync_jobs")
        assert "--loop" in line
        assert "--interval-seconds 30" in line

    def test_the_assay_registration_loop_keeps_its_interval(self, tmp_path):
        _, calls = _run_entrypoint(tmp_path)
        assert "--interval 5" in _line_for(calls, "run_assay_registration_jobs")
