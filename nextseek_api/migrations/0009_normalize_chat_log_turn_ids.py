"""Heal poisoned chat_log turn_ids in ChatSession.extra_state.

The CC path wrote its chat_log entry with ``turn_id=str(run_id)`` (a Celery task
UUID). The NS read site then computed ``log[-1]["turn_id"] + 1`` and crashed with
``TypeError: can only concatenate str (not "int") to str`` — poisoning every
subsequent NS turn in that session (the str entry is always the tail, so the log
never advances). Live census (2026-07-18): 24 of 55 sessions with a chat_log
were poisoned.

This DATA-ONLY migration renumbers each session's chat_log turn_ids to sequential
ints (1..N in order) and moves any str (UUID) turn_id into a distinct
``cc_run_id`` field on that entry, so transcript/artifact linkage stays
recoverable. No DDL is issued: ``cc_run_id`` is just another key inside the
existing ``extra_state`` JSONField. The pure transform lives in
``_chat_log_normalize`` and is idempotent + tolerant of NULL / non-dict /
list-shaped / malformed extra_state.

Irreversible by design: the forward transform discards the original (possibly
non-sequential) numbering and merges the turn_id/cc_run_id distinction that a
naive reverse could not un-merge without re-introducing the poison pill. The
reverse is therefore a defensive no-op (data already valid stays valid).
"""
from django.db import migrations

from ._chat_log_normalize import forwards_apply


def forwards(apps, schema_editor):
    ChatSession = apps.get_model("nextseek_api", "ChatSession")
    forwards_apply(ChatSession)


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0008_heal_cc_transcript_fk"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
