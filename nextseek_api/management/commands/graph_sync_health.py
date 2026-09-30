"""``manage.py graph_sync_health [--json]``: the graph sync health line that ``./startup.sh ci`` and ``rebuild`` print
on every box (SPEC-ci-health D12 to D15).

It reads the body the superuser status endpoint answers (``services.graph_sync_status.build_status``) straight from
the two dmac tables inside the app container, so it needs no HTTP login. That is what brings these checks to
production, whose smoke suite never holds superuser rights (``ci/smoke/test_registry_contents.py``). The body is
judged by ``graph_sync/health.py``, the functions the smoke suite applies on local and dev, so both say the same thing.

It prints the judgement only: a summary, one line per problem and one per failure still inside its retry window.
Never the body, whose ``runs`` part carries whole error texts; this output reaches terminals and CI records.

Exit status: 0 nothing to fail on; 1 a problem; 3 the two tables could not be read; 4 this container has not applied
the app's migrations yet (its entrypoint runs them at start, so ``./startup.sh`` asks again). Reads only.
"""
from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from django.db.migrations.executor import MigrationExecutor

from nextseek_api.graph_sync import health
from nextseek_api.services.graph_sync_status import UNAVAILABLE_DETAIL, build_status

APP = "nextseek_api"
EXIT_PROBLEMS = 1
EXIT_UNAVAILABLE = 3
EXIT_NOT_READY = 4


def unapplied_migrations(alias: str) -> list[str]:
    """The app's migrations not yet applied on ``alias``, in order. Right after a rebuild the entrypoint's
    ``migrate`` may still be running, and the status reads a column (``failing_since``) that 0023 adds."""
    executor = MigrationExecutor(connections[alias])
    targets = [node for node in executor.loader.graph.leaf_nodes() if node[0] == APP]
    return [migration.name for migration, _backwards in executor.migration_plan(targets)]


def judge(body: dict) -> dict:
    """What the command prints with ``--json`` for a status body."""
    problems = health.problems(body)
    return {"verdict": "problems" if problems else "ok", "summary": health.summary(body),
            "problems": problems, "warnings": health.within_grace(body)}


def verdict(alias: str) -> tuple[dict, int]:
    """The judgement, and the exit status it carries."""
    try:
        pending = unapplied_migrations(alias)
        if pending:
            return ({"verdict": "not_ready", "problems": [], "warnings": [],
                     "summary": f"{len(pending)} migrations of {APP} not applied yet, first {pending[0]}"},
                    EXIT_NOT_READY)
        result = judge(build_status())
    except DatabaseError:
        return ({"verdict": "unavailable", "summary": UNAVAILABLE_DETAIL, "problems": [UNAVAILABLE_DETAIL],
                 "warnings": []}, EXIT_UNAVAILABLE)
    return result, (EXIT_PROBLEMS if result["problems"] else 0)


class Command(BaseCommand):
    help = ("Judge the graph sync's own state: stale jobs, dead or failing outbox rows, failed runs, drift. "
            "Reads only.")
    # It must answer even when an unrelated system check fails: `manage.py check` is a health line of its own.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="print one JSON object instead of lines")

    def handle(self, *args, **options):
        result, code = verdict(getattr(settings, "NEXTSEEK_DATABASE", "default"))
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True))
        else:
            self.stdout.write(f"graph sync health: {result['verdict']}: {result['summary']}")
            for line in result["problems"]:
                self.stdout.write(f"PROBLEM  {line}")
            for line in result["warnings"]:
                self.stdout.write(f"WAITING  {line}")
        if code:
            raise CommandError(f"graph sync health: {result['verdict']}", returncode=code)
