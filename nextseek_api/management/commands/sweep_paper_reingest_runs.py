"""At boot, before the paper_reingest worker starts: put right the paper reingest runs a restart cut.

No worker is running yet, so a run still `acquiring` was killed mid-stage: it is failed at `acquire` (never
re-run here; a paper that brought the worker down would bring it down again on every boot). A `queued` or
`paused` run may have lost its message with the old worker, so stage 1 is queued again. If the broker still
holds the first message too, whichever runs second finds the run claimed or done and skips it
(`tasks.run_acquire`). A run in `contextualizing` is failed the same way, and one in `context` gets stage 2 queued
again; `curation_pending`, `curating` and `verifying` belong to the web loop (`run_paper_curation`), which sweeps
its own.
"""
import logging

from django.core.management.base import BaseCommand

from nextseek_api.paper_reingest import service
from nextseek_api.paper_reingest.models_db import PaperReingestRun

logger = logging.getLogger(__name__)

INTERRUPTED = "interrupted by a server restart: start the paper again"


def sweep_paper_reingest_runs() -> dict:
    failed = queued = unqueued = 0
    for state, stage in (("acquiring", "acquire"), ("contextualizing", "context")):
        for run in PaperReingestRun.objects.filter(state=state):
            failed += service.transition(run, (state,), "failed", failed_stage=stage, reason=INTERRUPTED)
    waiting = PaperReingestRun.objects.filter(state__in=("queued", "paused", "context"), superseded_by__isnull=True)
    for run in waiting:
        try:
            if run.state == "context":
                service.enqueue_context(run.run_id)
            else:
                service.enqueue_acquire(run)
            queued += 1
        except Exception:
            # Left as it is: the next boot, or `manage.py paper_reingest requeue`, queues it.
            logger.exception("paper reingest %s: could not queue its stage at boot", run.run_id)
            unqueued += 1
    return {"failed": failed, "queued": queued, "unqueued": unqueued}

class Command(BaseCommand):
    help = "Fail paper reingest runs a restart cut mid-acquire and queue waiting ones again (once at boot)."

    def handle(self, *args, **options):
        counts = sweep_paper_reingest_runs()
        self.stdout.write(f"failed {counts['failed']} interrupted paper reingest run(s); queued {counts['queued']} "
                          f"again; {counts['unqueued']} could not be queued")
