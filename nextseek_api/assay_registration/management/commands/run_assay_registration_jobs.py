"""Run queued assay-registration jobs.

    manage.py run_assay_registration_jobs --once
    manage.py run_assay_registration_jobs --limit 5

A management command rather than a Celery task deliberately: this needs no
broker, no new container and no new failure mode, and the operator can run it
by hand to drain a backlog. If a scheduled drain is wanted later, cron it.

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
from django.core.management.base import BaseCommand

from nextseek_api.assay_registration.runner import run_pending, worker_identity


class Command(BaseCommand):
    help = "Claim and run queued assay-registration jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1,
                            help="Maximum jobs to claim in this pass (default 1).")
        parser.add_argument("--once", action="store_true",
                            help="Kept for symmetry; this command always makes "
                                 "exactly one pass and exits.")

    def handle(self, *args, **options):
        owner = worker_identity()
        ran = run_pending(limit=options["limit"], owner=owner)
        # "succeeded", not "completed": run_pending counts successful terminal
        # states only, so a job that finished `partial` ran and is not in `ran`.
        self.stdout.write(f"{owner}: {ran} job(s) succeeded")
