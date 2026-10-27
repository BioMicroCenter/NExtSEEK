"""Fill or rebuild the session search index (NessieAI/ns/chat_search_index.py) for the chats already stored."""
import resource

from django.core.management.base import BaseCommand
from django.db import transaction

from NessieAI.ns.chat_search_index import index_chat
from nextseek_api.assistant.models_db import ChatSearchEntry, ChatSession


class _DryRun(Exception):
    """Raised inside a chat's transaction to roll it back after counting."""


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux reports KiB


class Command(BaseCommand):
    help = ("Index every chat's turns for the session search (turns already indexed are skipped, so a stopped run "
            "resumes); --rebuild re-indexes each chat from scratch.")

    def add_arguments(self, parser):
        parser.add_argument("--rebuild", action="store_true", help="Delete each chat's index rows before indexing it.")
        parser.add_argument("--user", type=int, help="Only this user id's chats.")
        parser.add_argument("--dry-run", action="store_true", help="Index each chat in a transaction rolled back: counts only.")
        parser.add_argument("--max-rss-mb", type=int, default=2048,
                            help="Stop (cleanly, resumable) once this process's peak memory passes this many MB.")

    def handle(self, *args, **o):
        chats = ChatSession.objects.order_by()
        if o["user"] is not None:
            chats = chats.filter(user_id=o["user"])
        ids = list(chats.values_list("pk", flat=True))
        n_chats = n_rows = failed = 0
        stopped = ""
        for pk in ids:  # one chat per transaction: a live turn never waits on more than one chat's rows
            if _peak_rss_mb() > o["max_rss_mb"]:
                stopped = f"; stopped at {_peak_rss_mb():.0f} MB peak memory (run again to resume)"
                break
            chat = ChatSession.objects.only("user_id", "extra_state").filter(pk=pk).first()
            if chat is None:
                continue  # deleted while this runs
            try:
                with transaction.atomic():
                    if o["rebuild"]:
                        ChatSearchEntry.objects.filter(session_id=pk).delete()
                    written = index_chat(chat)
                    n_chats += bool(written)
                    n_rows += written
                    if o["dry_run"]:
                        raise _DryRun
            except _DryRun:
                pass
            except Exception as exc:  # noqa: BLE001 - one bad chat must not stop the rest
                failed += 1
                self.stderr.write(f"chat {pk}: {type(exc).__name__}: {exc}")
        self.stdout.write(f"chat_search_index: {len(ids)} chat(s) read, {n_chats} indexed, {n_rows} row(s), "
                          f"{failed} failed{' (dry run: nothing written)' if o['dry_run'] else ''}{stopped}")
