"""End Query tasks left pending/running by a worker that died (the beat runs the same function every 5 minutes)."""
from django.core.management.base import BaseCommand

from nextseek_api.periodic_tasks import reap_stale_query_tasks


class Command(BaseCommand):
    help = "Mark stuck pending/running QueryTasks as errored so pollers stop."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit (the only mode).")

    def handle(self, *args, **options):
        self.stdout.write(f"reaped {reap_stale_query_tasks()} stale query task(s)")
