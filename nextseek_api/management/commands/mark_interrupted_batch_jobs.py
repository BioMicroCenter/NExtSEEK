"""At boot, before the batch worker starts: fail every upload job a restart killed mid-run (never re-run it)."""
from celery.backends.database.models import Task
from django.core.management.base import BaseCommand

INTERRUPTED_MESSAGE = (
    "interrupted by a server restart: re-upload the sheet; rows already saved are skipped"
)


def mark_interrupted_batch_jobs(backend=None) -> int:
    """Turn every STARTED/PROGRESS result row into FAILURE. Returns how many. Publishes nothing."""
    if backend is None:
        from nextseek_api.batch_upload.celery_app import app

        backend = app.backend
    stuck = []
    session = backend.ResultSession()
    try:
        stuck = [t.task_id for t in session.query(Task).filter(Task.status.in_(("STARTED", "PROGRESS")))]
    finally:
        session.close()
    for task_id in stuck:
        backend.mark_as_failure(task_id, RuntimeError(INTERRUPTED_MESSAGE))
    return len(stuck)


class Command(BaseCommand):
    help = "Fail upload jobs left STARTED/PROGRESS by a restart (run once at boot, before the batch worker)."

    def handle(self, *args, **options):
        self.stdout.write(f"marked {mark_interrupted_batch_jobs()} interrupted batch job(s) as failed")
