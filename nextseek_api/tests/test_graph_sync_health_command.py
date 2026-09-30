"""manage.py graph_sync_health: the graph sync health line on every box (SPEC-ci-health D12 to D15).

Hermetic: the two dmac tables on the SQLite test settings, no Neo4j. The command reads the real clock, so a row meant
to be overdue is written at a fixed moment long ago, and a row meant to be inside its window minutes before now.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from importlib import import_module
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from nextseek_api.graph_sync import state
from nextseek_api.services.graph_sync_status import UNAVAILABLE_DETAIL

command = import_module("nextseek_api.management.commands.graph_sync_health")

LONG_AGO = datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc)
LOST = "OperationalError: (2006, 'Server has gone away')"
QUIET = ("failing outbox rows: 0 (0 overdue); failed runs: 0 (0 overdue); "
         "latest drift check: none recorded")


def run(*args) -> tuple[int, str]:
    out = StringIO()
    try:
        call_command("graph_sync_health", *args, stdout=out, stderr=StringIO())
        code = 0
    except CommandError as exc:
        code = exc.returncode
    return code, out.getvalue()


def run_json() -> tuple[int, dict]:
    code, out = run("--json")
    return code, json.loads(out)


def a_drift_run_that_found_drift() -> None:
    state.start_run("drift", trigger="loop", now=LONG_AGO).finish(
        "drift", counts={"failed_checks": ["samples.missing_in_graph"]}, now=LONG_AGO)


@pytest.mark.django_db
def test_the_test_database_has_every_migration_of_the_app():
    assert command.unapplied_migrations("default") == []


@pytest.mark.django_db
def test_a_quiet_box_is_healthy():
    assert run_json() == (0, {"verdict": "ok", "summary": QUIET, "problems": [], "warnings": []})


@pytest.mark.django_db
def test_a_row_failing_past_its_retry_and_a_failed_run_are_problems():
    """The 2026-09-29 shape, long enough ago that both are overdue."""
    state.enqueue("catalog", "*", now=LONG_AGO)
    state.finish_failed(state.claim_next("w1", now=LONG_AGO + timedelta(seconds=1)), LOST, 3600,
                        now=LONG_AGO + timedelta(seconds=2))
    state.start_run("catalog", trigger="loop", now=LONG_AGO).finish("failed", counts={"error": LOST}, now=LONG_AGO)

    code, out = run_json()

    assert code == command.EXIT_PROBLEMS
    assert out["verdict"] == "problems"
    joined = "\n".join(out["problems"])
    assert "catalog *: failing for" in joined
    assert "catalog run" in joined and " failed (trigger loop" in joined
    assert LOST in joined


@pytest.mark.django_db
def test_drift_found_is_a_problem():
    a_drift_run_that_found_drift()

    code, out = run_json()

    assert code == command.EXIT_PROBLEMS
    assert any("found drift in: samples.missing_in_graph" in line for line in out["problems"])


@pytest.mark.django_db
def test_a_failure_inside_its_retry_window_is_a_warning_not_a_problem():
    now = datetime.now(dt_timezone.utc)
    state.enqueue("catalog", "*", now=now - timedelta(minutes=10))
    state.finish_failed(state.claim_next("w1", now=now - timedelta(minutes=9)), LOST, 3600,
                        now=now - timedelta(minutes=9))

    code, out = run_json()

    assert code == 0
    assert out["problems"] == []
    assert out["warnings"][0].startswith("row catalog *: failing for 9 min against 90 min")


@pytest.mark.django_db
def test_tables_that_cannot_be_read_exit_3_with_the_fixed_prose():
    with connection.cursor() as cur:
        cur.execute('DROP TABLE "graph_sync_outbox"')

    code, out = run_json()

    assert code == command.EXIT_UNAVAILABLE
    assert out == {"verdict": "unavailable", "summary": UNAVAILABLE_DETAIL, "problems": [UNAVAILABLE_DETAIL],
                   "warnings": []}


@pytest.mark.django_db
def test_unapplied_migrations_exit_4_so_startup_asks_again(monkeypatch):
    monkeypatch.setattr(command, "unapplied_migrations", lambda alias: ["0023_graph_sync_outbox_failing_since"])

    code, out = run_json()

    assert code == command.EXIT_NOT_READY
    assert out["verdict"] == "not_ready"
    assert "0023_graph_sync_outbox_failing_since" in out["summary"]


@pytest.mark.django_db
def test_without_json_it_prints_a_summary_and_one_line_per_problem():
    a_drift_run_that_found_drift()

    code, out = run()
    lines = out.splitlines()

    assert code == command.EXIT_PROBLEMS
    assert lines[0].startswith("graph sync health: problems: failing outbox rows: 0 (0 overdue)")
    assert any(line.startswith("PROBLEM  drift run") for line in lines)


@pytest.mark.django_db
def test_the_output_carries_excerpts_never_the_body():
    """The body's runs part holds whole error texts; this output reaches terminals and CI records."""
    long = "ServiceUnavailable: bolt://neo4j.example:7687 refused " + "x" * 5000 + "\nTraceback ..."
    state.start_run("catalog", trigger="loop", now=LONG_AGO).finish("failed", counts={"error": long}, now=LONG_AGO)

    _code, out = run("--json")

    assert "bolt://" not in out and "Traceback" not in out and "x" * 300 not in out


@pytest.mark.django_db
def test_the_command_writes_nothing():
    state.enqueue("samples", "sample:7", now=LONG_AGO)

    with CaptureQueriesContext(connection) as queries:
        run("--json")

    writes = [q["sql"] for q in queries.captured_queries
              if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == []
