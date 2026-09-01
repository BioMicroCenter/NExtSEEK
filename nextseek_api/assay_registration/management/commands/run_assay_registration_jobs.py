"""Run queued assay-registration jobs.

    manage.py run_assay_registration_jobs                 # loop, the deployment form
    manage.py run_assay_registration_jobs --once          # one pass, drain by hand
    manage.py run_assay_registration_jobs --once --limit 5

IT LOOPS BY DEFAULT, because a command that makes one pass and exits is only a
worker if something re-runs it, and nothing did. That gap is this task's own
founding argument one level up: the endpoint answers 202 and promises a
`status_url`, and if no process ever drains the queue the URL reports
`accepted`, 0 of N, forever -- exactly the state the task exists to remove,
moved from the branch into the deployment. The sibling says it outright:
`attributes/management/commands/dispatch_attribute_outbox.py` opens with "every
deployment that can return 202 for an asynchronous mutation must run exactly
this loop", and `docker-compose.yml` runs it `restart: unless-stopped`.

A management command rather than a Celery task deliberately: this needs no
broker, no new failure mode, and the operator can drain a backlog by hand with
`--once`.

Note on discoverability: Django's per-app command scan walks only each
INSTALLED_APPS entry's own filesystem path (`django.core.management.get_commands`),
and `nextseek_api.assay_registration` is a plain subpackage of the `nextseek_api`
app -- its model sets `app_label = "nextseek_api"` rather than declaring an
AppConfig -- so this module is not on that path. A same-named shim under
`nextseek_api/management/commands/` re-exports this `Command`, exactly as
`dispatch_attribute_outbox` and `recover_attribute_sync_jobs` already do for the
sibling job endpoint. Without the shim `manage.py run_assay_registration_jobs`
answers "Unknown command" (verified against this checkout before the shim
existed).
"""
import logging
import time

from django.core.management.base import BaseCommand

from nextseek_api.assay_registration.runner import run_pending, worker_identity

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Claim and run queued assay-registration jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1,
                            help="Maximum jobs to claim per pass (default 1).")
        parser.add_argument("--once", action="store_true",
                            help="Make one pass and exit, instead of looping. "
                                 "Use this to drain a backlog by hand.")
        parser.add_argument("--interval", type=float, default=5.0,
                            help="Seconds to sleep between passes when looping "
                                 "(default 5).")

    def handle(self, *args, **options):
        owner = worker_identity()

        # "succeeded", not "completed", in both branches: run_pending counts
        # successful terminal states only, so a job that finished `partial` ran
        # and is not in that number.
        if options["once"]:
            ran = run_pending(limit=options["limit"], owner=owner)
            # Reported even when zero. A hand drain that prints nothing is
            # indistinguishable from a hang.
            self.stdout.write(f"{owner}: {ran} job(s) succeeded")
            return

        self.stdout.write(f"{owner}: draining every {options['interval']}s")
        while True:
            try:
                ran = run_pending(limit=options["limit"], owner=owner)
                if ran:
                    # Only when there was something to say: a line every
                    # `--interval` seconds forever is not a log, it is noise.
                    self.stdout.write(f"{owner}: {ran} job(s) succeeded")
            except Exception:  # noqa: BLE001
                # Swallowed on purpose. One job's failure is already recorded on
                # that job by `run_one`; a loop that dies on it stops draining
                # every OTHER job, which is a strictly worse outcome than a noisy
                # log. This is the only place in the package that swallows.
                log.exception("assay-registration drain pass failed")
            time.sleep(options["interval"])
