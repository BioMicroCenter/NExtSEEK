"""Drain the sample-share queue (the studies tool's share mode, nextseek_api/studies/share_apply.py).

    manage.py run_share_jobs [--once] [--interval S] [--limit N]

Each pass claims the oldest claimable share: a ``planning`` one is planned (its dry run, written to its run directory
under ``LOG_DIR/studies``), a ``queued`` one has its link unit run under the studies tool's run lock (a busy lock puts
it back to ``queued`` for the next pass). The worker holds no SEEK credential and makes no SEEK call: the admin's own
apply calls make the clones. ``docker/scripts/entrypoint.sh`` starts it beside ``run_assay_registration_jobs``.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import BaseCommand

from nextseek_api.studies import share_apply, share_jobs
from nextseek_api.studies.snapshot import SnapshotReader

log = logging.getLogger(__name__)


@contextmanager
def _graph():
    """A read-only use of the configured Neo4j: the plan reads the stored labels of the samples it touches."""
    from neo4j import GraphDatabase

    config = getattr(settings, "NEO4J_DATABASE", None) or {}
    with GraphDatabase.driver(config["URI"], auth=config["AUTH"]) as driver:
        yield driver, config.get("NAME") or "neo4j"


def run_pass(owner: str, limit: int) -> int:
    """Up to ``limit`` claimable shares, oldest first. Returns how many were taken."""
    taken = 0
    for _ in range(limit):
        share = share_jobs.next_claimable()
        if share is None or not share_jobs.claim(share, owner):
            break
        taken += 1
        if share.state == "planning":
            with _graph() as (driver, db):
                outcome = share_apply.plan_job(share, owner, reader=SnapshotReader(None, driver, db))
        else:
            outcome = share_apply.run_share_unit(share, owner)
        log.info("run_share_jobs: share %s %s", share.share_id, outcome)
    return taken


class Command(BaseCommand):
    help = "Plan sample shares and run their link units (the studies tool's share mode)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="one pass, then exit")
        parser.add_argument("--interval", type=float, default=5.0, help="seconds between passes")
        parser.add_argument("--limit", type=int, default=10, help="shares per pass")

    def handle(self, *args, **options):
        owner = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            try:
                run_pass(owner, options["limit"])
            except Exception:  # noqa: BLE001 - one bad pass must not stop the drain; the share keeps its state
                log.exception("run_share_jobs: a pass failed")
            if options["once"]:
                return
            time.sleep(options["interval"])
