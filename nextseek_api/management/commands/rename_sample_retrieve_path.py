"""``manage.py rename_sample_retrieve_path``: rewrite the download API's old path in stored Nessie chats.

The sample download API moved from ``/nextseek_api/admin/samples/retrieve/`` to ``/nextseek_api/samples/retrieve/``.
Nessie stores the endpoint a turn called (``api_plan.endpoint``, ``parser_plan.target_endpoint``, request URLs and
step outputs) inside the JSON of ``assistant_chat_session`` (``results_history``, ``extra_state``, ``last_debug``)
and ``assistant_query_task`` (``progress``, ``result``), and a follow-up turn replays what it stored. The old path
stays routed as an alias, so nothing breaks if this never runs; running it makes the stored chats name the endpoint
by its new path.

Dry run by default: it counts the rows and strings it would change. ``--apply`` writes. Every string inside those
JSON documents that contains ``admin/samples/retrieve`` has that substring replaced with ``samples/retrieve``, chat
text that quotes the path included (``extra_state.chat_log``); the
documents are rewritten as JSON, never edited as text, and ``updated_at`` is left alone (``QuerySet.update``), so no
session moves in anyone's chat list. The compressed CC transcripts (``assistant_cc_session_transcript``) are left
as they are: they are an immutable record of what ran.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from nextseek_api.assistant.models_db import ChatSession, QueryTask

OLD = "admin/samples/retrieve"
NEW = "samples/retrieve"

TARGETS = (
    (ChatSession, ("results_history", "extra_state", "last_debug")),
    (QueryTask, ("progress", "result")),
)


def rewrite(value):
    """(value with OLD replaced by NEW in every string, keys included; number of strings changed)."""
    if isinstance(value, str):
        return (value.replace(OLD, NEW), 1) if OLD in value else (value, 0)
    if isinstance(value, list):
        out, n = [], 0
        for item in value:
            new, k = rewrite(item)
            out.append(new)
            n += k
        return out, n
    if isinstance(value, dict):
        out, n = {}, 0
        for key, item in value.items():
            new_key, k1 = rewrite(key)
            new, k2 = rewrite(item)
            out[new_key] = new
            n += k1 + k2
        return out, n
    return value, 0


class Command(BaseCommand):
    help = "Rewrite /nextseek_api/admin/samples/retrieve/ to /nextseek_api/samples/retrieve/ in stored Nessie chats."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run).")
        parser.add_argument("--batch", type=int, default=200, help="Rows per transaction.")

    def handle(self, *args, apply=False, batch=200, **options):
        grand_rows = grand_strings = 0
        for model, fields in TARGETS:
            rows = strings = 0
            pks = list(model.objects.values_list("pk", flat=True).order_by("pk"))
            for start in range(0, len(pks), batch):
                chunk = pks[start:start + batch]
                with transaction.atomic():
                    # Locked while read and rewritten, so a chat turn saving the same row waits instead of being lost.
                    rows_qs = model.objects.filter(pk__in=chunk).only("pk", *fields)
                    for obj in (rows_qs.select_for_update() if apply else rows_qs):
                        changes, n = {}, 0
                        for field in fields:
                            new, k = rewrite(getattr(obj, field))
                            if k:
                                changes[field], n = new, n + k
                        if changes:
                            rows, strings = rows + 1, strings + n
                            if apply:
                                model.objects.filter(pk=obj.pk).update(**changes)
            self.stdout.write(f"{model._meta.db_table}: {rows} rows, {strings} strings "
                              f"{'rewritten' if apply else 'would be rewritten'}")
            grand_rows, grand_strings = grand_rows + rows, grand_strings + strings
        self.stdout.write(f"total: {grand_rows} rows, {grand_strings} strings"
                          f"{'' if apply else ' (dry run; pass --apply to write)'}")
