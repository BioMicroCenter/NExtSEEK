from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


SCRIPT = Path(__file__).parents[3] / "docker" / "scripts" / "attribute_runtime_healthcheck.py"
SPEC = importlib.util.spec_from_file_location("attribute_runtime_healthcheck", SCRIPT)
health = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(health)


def test_worker_probe_requires_process_and_readable_sqlite(tmp_path):
    broker = tmp_path / "broker.sqlite3"
    sqlite3.connect(broker).close()
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(
        b"/app/.venv/bin/python\0/app/.venv/bin/celery\0worker\0-Q\0attribute_mutations\0"
    )

    assert health.check_worker(broker, tmp_path / "proc")[0] is True
    (proc / "cmdline").unlink()
    ok, detail = health.check_worker(broker, tmp_path / "proc")
    assert ok is False
    assert "process is absent" in detail


class _Cursor:
    def __init__(self, row):
        self.row = row

    def execute(self, query, params):
        assert health.HEARTBEAT_TABLE in query
        assert params == ("attribute_mutations",)

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def cursor(self):
        return _Cursor(self.row)

    def close(self):
        self.closed = True


def _env():
    return {
        "MYSQL_HOST": "db", "MYSQL_USER": "user", "MYSQL_PASSWORD": "secret",
        "NEXTSEEK_MYSQL_DATABASE": "dmac",
    }


def test_heartbeat_probe_accepts_fresh_owned_row_and_closes_connection():
    connection = _Connection(("dispatcher:1", 2))
    ok, detail = health.check_heartbeat(
        "attribute_mutations", environ=_env(), connector=lambda **_kwargs: connection,
    )
    assert ok is True
    assert "fresh" in detail
    assert connection.closed is True


def test_heartbeat_probe_rejects_absent_stale_and_unowned_rows():
    for row, expected in ((None, "absent"), (("owner", 91), "stale"), (("", 1), "no owner")):
        ok, detail = health.check_heartbeat(
            "attribute_mutations", environ=_env(), connector=lambda **_kwargs: _Connection(row),
        )
        assert ok is False
        assert expected in detail


def test_heartbeat_probe_refuses_missing_configuration_without_leaking_names():
    ok, detail = health.check_heartbeat("attribute_mutations", environ={})
    assert ok is False
    assert detail == "attribute heartbeat database configuration is incomplete"
