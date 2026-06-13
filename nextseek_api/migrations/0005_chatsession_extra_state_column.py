"""Idempotently create the ``assistant_chat_session.extra_state`` column.

Background
----------
``0004_chatsession_extra_state_state_only`` is a ``SeparateDatabaseAndState``
migration with ``database_operations=[]``: it adds ``extra_state`` to Django's
model *state* but issues no DDL, on the assumption that an earlier (now-rewritten)
migration already created the column. On the live dev DB the column was in fact
added out-of-band, so the server works. But a **fresh** database (e.g. Django's
``test_dmac`` test database) runs ``0001`` (creates ``assistant_chat_session``
without ``extra_state``) → ``0004`` (no DDL) and ends up with the model field but
no column, so every ``ChatSession`` insert fails with
``(1054, "Unknown column 'extra_state' in 'field list'")``.

This migration repairs the chain for fresh databases without disturbing any
database where the column already exists. It is **idempotent**: it inspects
``information_schema`` (MySQL) / ``PRAGMA table_info`` (SQLite) and only issues the
``ADD COLUMN`` when the column is absent. On the production/dev DB (column present)
it is a no-op; on a fresh test DB it adds the column. ``state_operations`` is empty
because ``0004`` already declared the field in Django's state.
"""

from django.db import migrations

TABLE = "assistant_chat_session"
COLUMN = "extra_state"


def add_extra_state_if_missing(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor
    with connection.cursor() as cursor:
        if vendor == "mysql":
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s "
                "AND column_name = %s",
                [TABLE, COLUMN],
            )
            exists = cursor.fetchone()[0] > 0
            if not exists:
                # JSON column, nullable so no backfill default is required (the
                # Django field provides ``default=dict`` on insert).
                cursor.execute(
                    f"ALTER TABLE `{TABLE}` ADD COLUMN `{COLUMN}` JSON NULL"
                )
        elif vendor == "sqlite":
            cursor.execute(f"PRAGMA table_info('{TABLE}')")
            cols = {row[1] for row in cursor.fetchall()}
            if COLUMN not in cols:
                cursor.execute(
                    f"ALTER TABLE \"{TABLE}\" ADD COLUMN \"{COLUMN}\" text"
                )
        else:  # pragma: no cover - other backends unused in this project
            cursor.execute(
                f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} json"
            )


def noop_reverse(apps, schema_editor):
    # Reversing this migration must not drop a column other migrations/state
    # depend on; leave the column in place.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0004_chatsession_extra_state_state_only"),
    ]

    operations = [
        migrations.RunPython(add_extra_state_if_missing, noop_reverse),
    ]
