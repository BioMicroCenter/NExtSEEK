"""Idempotently ensure the assistant_chat_session.extra_state column exists.

Migration 0004 is a state-only migration: it declares ``extra_state`` on the
Django model but issues no DDL, on the assumption that the column was already
created by a now-rewritten earlier migration. That assumption holds only for
databases that ran the old (rewritten) migration; on any *fresh* database
(per-run test DB, a clean deploy, or this dev server's dmac DB) the column was
never created, so every ChatSession query failed with
``(1054, "Unknown column 'extra_state'")``.

This migration adds the column when, and only when, it is missing -- so it is a
no-op on databases that already have it (including ones patched out-of-band) and
a real ``ADD COLUMN`` on databases that don't.
"""

from django.db import migrations


TABLE = "assistant_chat_session"
COLUMN = "extra_state"


def add_extra_state_if_missing(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            [TABLE, COLUMN],
        )
        already_exists = cursor.fetchone()[0] > 0

    if already_exists:
        return

    ChatSession = apps.get_model("nextseek_api", "ChatSession")
    field = ChatSession._meta.get_field(COLUMN)
    schema_editor.add_field(ChatSession, field)


def noop_reverse(apps, schema_editor):
    # Leave the column in place on reverse; 0004 owns the model-state removal.
    pass


class Migration(migrations.Migration):

    # The conditional ADD COLUMN issues DDL, and MySQL/MariaDB cannot run DDL
    # inside a transaction. Mark the migration non-atomic so the RunPython can
    # execute schema changes outside an atomic block.
    atomic = False

    dependencies = [
        ("nextseek_api", "0004_chatsession_extra_state_state_only"),
    ]

    operations = [
        migrations.RunPython(add_extra_state_if_missing, noop_reverse),
    ]
