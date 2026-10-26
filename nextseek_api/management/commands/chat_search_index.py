"""Fill or rebuild the session search index (NessieAI/ns/chat_search_index.py) for the chats already stored."""
import contextlib

from django.core.management.base import BaseCommand
from django.db import transaction

from NessieAI.ns.chat_search_index import index_chat
from nextseek_api.assistant.models_db import ChatSearchEntry, ChatSession


class Command(BaseCommand):
    help = ("Index every chat's turns for the session search (turns already indexed are skipped); "
            "--rebuild empties the index first.")

    def add_arguments(self, parser):
        parser.add_argument("--rebuild", action="store_true", help="Delete the index rows first, then index everything.")
        parser.add_argument("--user", type=int, help="Only this user id's chats.")
        parser.add_argument("--dry-run", action="store_true", help="Index inside a transaction rolled back: counts only.")

    def handle(self, *args, **o):
        chats = ChatSession.objects.order_by()
        if o["user"] is not None:
            chats = chats.filter(user_id=o["user"])
        ids = list(chats.values_list("pk", flat=True))
        n_chats = n_rows = failed = 0
        # A real run commits chat by chat; a dry run holds one transaction and rolls it back.
        with transaction.atomic() if o["dry_run"] else contextlib.nullcontext():
            if o["rebuild"]:
                ChatSearchEntry.objects.filter(session_id__in=ids).delete()
            for pk in ids:  # one chat at a time: a chat's bundles can be large
                chat = ChatSession.objects.only("user_id", "extra_state").filter(pk=pk).first()
                if chat is None:
                    continue  # deleted while this runs
                try:
                    with transaction.atomic():
                        written = index_chat(chat)
                except Exception as exc:  # noqa: BLE001 - one bad chat must not stop the rest
                    failed += 1
                    self.stderr.write(f"chat {pk}: {type(exc).__name__}: {exc}")
                    continue
                n_chats += bool(written)
                n_rows += written
            if o["dry_run"]:
                transaction.set_rollback(True)
        self.stdout.write(f"chat_search_index: {len(ids)} chat(s) read, {n_chats} indexed, {n_rows} row(s), "
                          f"{failed} failed{' (dry run: nothing written)' if o['dry_run'] else ''}")
