"""The paper reingest curation loop, in the web container (spec delta 1): the only place a curation agent starts.

    manage.py run_paper_curation [--once] [--interval S]

One curation at a time per instance (spec §5). A tick sweeps stale curations, then claims the oldest run waiting
in `curation_pending` and runs it to the end (launcher.curate, up to two agent launches). It does nothing while the
web drain flag is set, and the command does nothing at all unless NEXTSEEK_PAPER_REINGEST=1. On SIGTERM the run in
hand goes back to `curation_pending`, for the next web container.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from NessieAI.cc import spawner_client
from nextseek_api.paper_reingest import launcher, service
from nextseek_api.paper_reingest.models_db import PaperReingestRun

logger = logging.getLogger(__name__)

STALE_MARGIN = timedelta(minutes=15)
CURATING = ("curating", "verifying")


def _draining() -> bool:
    return os.path.exists(os.path.join(os.environ.get("NEXTSEEK_TURN_DIR", "/tmp/nextseek-turns"), ".draining"))


def sweep_stale(*, now=None, settings=None, client=spawner_client) -> int:
    """A run held in curating or verifying past two launches and a margin lost its web container."""
    settings = settings or launcher.LaunchSettings.from_env()
    cutoff = (now or timezone.now()) - timedelta(seconds=2 * (settings.timeout_s + 60)) - STALE_MARGIN
    moved = 0
    for run in PaperReingestRun.objects.filter(state__in=CURATING, updated_at__lt=cutoff):
        for attempt in (1, 2):
            try:
                client.stop_turn(launcher.launch_id(run.run_id, attempt))
            except Exception:  # noqa: BLE001 - a container already gone, or the spawner down: the claim still moves
                logger.warning("paper reingest %s: could not stop launch %d", run.run_id, attempt, exc_info=True)
        if run.curation_attempts >= launcher.MAX_LAUNCH_ATTEMPTS:
            moved += service.transition(run, CURATING, "failed", failed_stage="curate",
                                        reason="interrupted: the web container stopped mid-curation too often")
        else:
            moved += service.transition(run, CURATING, "curation_pending",
                                        reason="interrupted: the web container stopped mid-curation")
    return moved


def tick(*, client=spawner_client, settings=None) -> str:
    if _draining():
        return "draining"
    sweep_stale(settings=settings, client=client)
    if PaperReingestRun.objects.filter(state__in=CURATING).exists():
        return "busy"
    run = (PaperReingestRun.objects.filter(state="curation_pending", superseded_by__isnull=True)
           .order_by("updated_at", "id").first())
    if run is None:
        return "idle"
    return launcher.curate(run.run_id, client=client, settings=settings)


class Command(BaseCommand):
    help = "Run paper reingest curations, one at a time (web container only)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="one tick, then exit")
        parser.add_argument("--interval", type=float, default=15.0, help="seconds between ticks")

    def handle(self, *args, once: bool, interval: float, **options):
        if os.environ.get("NEXTSEEK_PAPER_REINGEST") != "1":
            self.stdout.write("paper reingest is off on this instance (NEXTSEEK_PAPER_REINGEST is not 1)")
            return

        def stop(signum, frame):
            raise launcher.LoopStopped()

        signal.signal(signal.SIGTERM, stop)
        while True:
            try:
                tick()
            except launcher.LoopStopped:
                return
            except Exception:  # noqa: BLE001 - one bad tick never ends the loop
                logger.exception("paper curation loop: a tick failed")
            if once:
                return
            try:
                time.sleep(interval)
            except launcher.LoopStopped:
                return
