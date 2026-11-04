"""Idempotent utf8mb4 conversion of the chat columns users type into (migration 0034).

Boxes seeded before the dmac seed dump was regenerated as utf8mb4 (dev and prod) keep
``assistant_query_task`` and ``assistant_chat_session`` in latin1, so their text columns are
latin1 too. MySQL then rejects any question holding a character outside cp1252 (U+2212 "−",
Greek letters, most symbols) with error 1366 "Incorrect string value", and
``POST /nextseek_api/cc-assistant/query/async/`` answers 500.

Only the free-text columns are converted, one ``MODIFY`` each. ``session_id`` and ``task_id``
stay as they are: ``session_id`` is the parent column of every chat foreign key, and the heals
for 0007, 0010 and 0025 match each child column to its charset, so converting it (or the
whole table with ``CONVERT TO``) would break those keys. The tables' default charset is left
alone for the same reason: a char foreign key added later to one of these tables takes that
default and must keep matching the latin1 ``session_id``.

A column already in utf8mb4 (fresh installs, test databases) is left untouched whatever its
collation, so the heal returns [] there. latin1 to utf8mb4 is lossless: every latin1 value has
a utf8mb4 spelling, and MySQL re-encodes the rows during the ALTER.
"""
from __future__ import annotations

#: (table, column): the chat columns that hold text a person typed. The question, and the
#: chat title the rename endpoint stores (nextseek_api/services/assistant.py).
COLUMNS = (
    ("assistant_query_task", "query"),
    ("assistant_chat_session", "title"),
)

CHARSET = "utf8mb4"
COLLATION = "utf8mb4_unicode_ci"


def heal_mysql(cursor) -> list[str]:
    """Convert each column in ``COLUMNS`` that is not utf8mb4; return one action per conversion.

    Raises RuntimeError instead of guessing when a column is missing or carries a database
    default, because the ``MODIFY`` rebuilds the definition from its type and nullability only.
    """
    actions: list[str] = []
    for table, column in COLUMNS:
        cursor.execute(
            "SELECT CHARACTER_SET_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"{table}.{column} not found in the current database")
        charset, column_type, is_nullable, default = row
        if charset == CHARSET:
            continue
        if default is not None:
            raise RuntimeError(
                f"{table}.{column} has the database default {default!r}; Django sets none here, "
                "so convert it by hand rather than let this heal drop it"
            )
        null_sql = "NULL" if is_nullable == "YES" else "NOT NULL"
        cursor.execute(
            f"ALTER TABLE `{table}` MODIFY `{column}` {column_type} "
            f"CHARACTER SET {CHARSET} COLLATE {COLLATION} {null_sql}"
        )
        actions.append(f"{table}.{column}")
    return actions


def heal(apps, schema_editor):
    """RunPython entrypoint for 0034. SQLite and other backends store any text already."""
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        return
    with connection.cursor() as cursor:
        actions = heal_mysql(cursor)
    if actions:
        print(f"[migrations] converted to {CHARSET}: {', '.join(actions)}")
