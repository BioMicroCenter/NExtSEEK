"""End chat turns a stopping web container could not finish (``docker/scripts/entrypoint.sh web`` runs this)."""
from django.core.management.base import BaseCommand

from nextseek_api.periodic_tasks import end_cut_turns


class Command(BaseCommand):
    help = "Write the restart message as the query_error of each named QueryTask that is still pending/running."

    def add_arguments(self, parser):
        parser.add_argument("task_ids", nargs="*", help="QueryTask ids (the turn marker names).")

    def handle(self, *args, **options):
        self.stdout.write(f"ended {end_cut_turns(options['task_ids'])} cut turn(s)")
