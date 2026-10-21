"""Size guard maintenance: shrink old chat rows, undo the guard, sweep unlinked files (nextseek_api/assistant/spill.py)."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from nextseek_api.assistant import spill
from nextseek_api.assistant.models_db import ChatSession, QueryTask

_MODELS = {m._meta.db_table: m for m in (ChatSession, QueryTask)}


def _rows(want_links: bool):
    """(model, column, pk, stored bytes) for every row to rewrite (stored text over the value limit) or inline (holds a link)."""
    with connection.cursor() as cur:
        for table, pk, col in spill.SPILL_COLUMNS:
            cur.execute(f"SELECT {pk}, LENGTH(CAST({col} AS CHAR)) FROM {table}")
            for key, size in cur.fetchall():
                if want_links:
                    cur.execute(f"SELECT {col} FROM {table} WHERE {pk} = %s", [key])
                    if not spill._FIND.search(spill._text(cur.fetchone()[0])):
                        continue
                elif not size or size <= spill.SPILL_VALUE_BYTES:  # NULL: a task still pending or running
                    continue
                yield _MODELS[table], col, key, size


class Command(BaseCommand):
    help = "Chat size guard: --rewrite old big rows, --inline to undo the guard, --sweep unlinked spill files."

    def add_arguments(self, parser):
        parser.add_argument("--rewrite", action="store_true", help="Re-save every row whose stored column is over the value limit.")
        parser.add_argument("--inline", action="store_true", help="Write links back inline (run before going back to a plain JSONField).")
        parser.add_argument("--sweep", action="store_true", help="Delete spill files no row links to (older than a day).")
        parser.add_argument("--dry-run", action="store_true", help="Count and write nothing.")

    def handle(self, *args, **o):
        if not (o["rewrite"] or o["inline"] or o["sweep"]):
            raise CommandError("give --rewrite, --inline or --sweep")
        if o["rewrite"]:
            n = total = 0
            for model, col, key, size in list(_rows(False)):
                n, total = n + 1, total + size
                if not o["dry_run"]:
                    value = getattr(model.objects.only(col).get(pk=key), col)
                    # update(), not save(): updated_at must not move
                    model.objects.filter(pk=key).update(**{col: value})
            self.stdout.write(f"rewrite: {n} column value(s), {total} stored bytes{' (dry run)' if o['dry_run'] else ''}")
        if o["inline"]:
            n = refused = 0
            for model, col, key, _ in list(_rows(True)):
                value = getattr(model.objects.only(col).get(pk=key), col)
                size = len(json.dumps(value))
                if size >= spill.INLINE_MAX_BYTES:
                    refused += 1
                    self.stderr.write(f"refused: {model._meta.db_table}.{col} {key} would be {size} bytes inline")
                    continue
                n += 1
                if not o["dry_run"]:
                    with spill.spilling_off():
                        model.objects.filter(pk=key).update(**{col: value})
            self.stdout.write(f"inline: {n} column value(s), {refused} refused{' (dry run)' if o['dry_run'] else ''}")
        if o["sweep"]:
            self.stdout.write(f"sweep: {spill.sweep(dry_run=o['dry_run'])}")
