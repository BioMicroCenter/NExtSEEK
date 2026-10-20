"""Mark the chat turns a restart of the app container killed (see
``nextseek_api/assistant/interrupted_turns.py``). Run by the entrypoint before
any server starts; running it while turns are in flight would fail them."""
from django.core.management.base import BaseCommand

from nextseek_api.assistant.interrupted_turns import fail_interrupted_turns


class Command(BaseCommand):
    help = "Mark chat turns left 'running' by a restart as interrupted. Boot only."

    def handle(self, *args, **options):
        count = fail_interrupted_turns()
        self.stdout.write(f"marked {count} interrupted chat turn(s)")
