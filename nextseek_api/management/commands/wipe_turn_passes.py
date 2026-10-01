"""Empty the held login of every Container-CC turn pass past its expiry.

The same clean-up runs every time a pass is issued (``turn_pass.issue_pass``). There is no Celery beat entry on
purpose: nothing starts celery beat on the boxes, and starting it would also switch on the paid summary sweep.
"""
from django.core.management.base import BaseCommand

from nextseek_api.assistant import turn_pass


class Command(BaseCommand):
    help = "Revoke every Container-CC turn pass past its expiry and empty the login it held."

    def handle(self, *args, **options):
        wiped = turn_pass.wipe_expired()
        self.stdout.write(f"wiped {wiped} expired turn pass(es)")
