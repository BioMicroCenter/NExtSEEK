#!/usr/bin/env python3
"""Low-overhead health probes for the standalone attribute runtimes.

The dispatcher and recovery scheduler already publish durable heartbeats to
MySQL.  Reading those rows directly avoids starting a second Django process
inside memory-bounded service containers.  The Celery SQLAlchemy/SQLite
transport does not provide a reliable remote-control ping, so the worker
probe verifies both its worker process and read access to the durable broker.
No credential value is ever printed.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Mapping

BROKER_PATH = Path("/var/lib/attribute-broker/broker.sqlite3")
HEARTBEAT_TABLE = "attributes_outbox_dispatcher_heartbeat"
HEARTBEAT_KEYS = {
    "dispatcher": "attribute_mutations",
    "recovery": "attribute_sync_recovery",
}
REQUIRED_DB_ENV = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "NEXTSEEK_MYSQL_DATABASE")


def _worker_process_running(proc_root: Path = Path("/proc")) -> bool:
    for cmdline_path in proc_root.glob("[0-9]*/cmdline"):
        try:
            cmdline = cmdline_path.read_bytes()
        except OSError:
            continue
        if (
            b"/app/.venv/bin/celery\0" in cmdline
            and b"\0worker\0" in cmdline
            and b"\0-Q\0attribute_mutations\0" in cmdline
        ):
            return True
    return False


def check_worker(
    broker_path: Path = BROKER_PATH,
    proc_root: Path = Path("/proc"),
) -> tuple[bool, str]:
    if not _worker_process_running(proc_root):
        return False, "attribute Celery worker process is absent"
    try:
        with sqlite3.connect(
            f"file:{broker_path}?mode=ro", uri=True, timeout=2,
        ) as connection:
            connection.execute("SELECT 1").fetchone()
    except (OSError, sqlite3.Error) as exc:
        return False, f"attribute SQLite broker is unreadable: {type(exc).__name__}"
    return True, "attribute Celery worker and SQLite broker are available"


def check_heartbeat(
    singleton_key: str,
    *,
    max_age_seconds: int = 90,
    environ: Mapping[str, str] | None = None,
    connector: Callable[..., object] | None = None,
) -> tuple[bool, str]:
    env = os.environ if environ is None else environ
    missing = [name for name in REQUIRED_DB_ENV if not env.get(name)]
    if missing:
        return False, "attribute heartbeat database configuration is incomplete"

    if connector is None:
        import MySQLdb

        connector = MySQLdb.connect
    try:
        connection = connector(
            host=env["MYSQL_HOST"],
            user=env["MYSQL_USER"],
            passwd=env["MYSQL_PASSWORD"],
            db=env["NEXTSEEK_MYSQL_DATABASE"],
            connect_timeout=3,
            read_timeout=3,
            write_timeout=3,
        )
        try:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT owner, TIMESTAMPDIFF(SECOND, observed_at, UTC_TIMESTAMP(6)) "
                f"FROM {HEARTBEAT_TABLE} WHERE singleton_key=%s",
                (singleton_key,),
            )
            row = cursor.fetchone()
        finally:
            connection.close()
    except Exception as exc:  # connector exceptions vary by mysqlclient version
        return False, f"attribute heartbeat database read failed: {type(exc).__name__}"

    if row is None:
        return False, f"attribute heartbeat {singleton_key!r} is absent"
    owner, age_seconds = row
    if not owner:
        return False, f"attribute heartbeat {singleton_key!r} has no owner"
    if age_seconds is None or age_seconds < 0 or age_seconds > max_age_seconds:
        return False, f"attribute heartbeat {singleton_key!r} is stale"
    return True, f"attribute heartbeat {singleton_key!r} is fresh"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", choices=("worker", "dispatcher", "recovery"))
    parser.add_argument("--max-age-seconds", type=int, default=90)
    args = parser.parse_args(argv)

    if args.runtime == "worker":
        ok, detail = check_worker()
    else:
        ok, detail = check_heartbeat(
            HEARTBEAT_KEYS[args.runtime], max_age_seconds=args.max_age_seconds,
        )
    if not ok:
        print(detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
