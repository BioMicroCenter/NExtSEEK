"""Idempotent ``when_to_use`` and ``similar_types`` columns on the two definition tables (migration 0036).

``sample_types_context`` and ``assay_context`` are created out of band (the dmac seed dump), so no Django migration
owns them and schema fixups run only on install, never on rebuild. Boxes that exist already get the columns here.
Each table is touched only when it exists (a vendor or test database has neither), each column only when absent.
The columns are not on the Django model: the Definitions admin reads and writes them with raw SQL.
"""
from __future__ import annotations

TABLES = ("sample_types_context", "assay_context")
#: Plain text; ``similar_types`` holds JSON text, a list of {"code", "differs"}.
COLUMNS = ("when_to_use", "similar_types")
SQL_TYPE = "TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL"


def heal_mysql(cursor) -> list[str]:
    """Add each missing column to each existing table; return one ``table.column`` per addition."""
    added: list[str] = []
    for table in TABLES:
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        have = {r[0].lower() for r in cursor.fetchall()}
        if not have:        # no columns means no such table
            continue
        for column in COLUMNS:
            if column not in have:
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {SQL_TYPE}")
                added.append(f"{table}.{column}")
    return added


def heal(apps, schema_editor):
    """RunPython entrypoint for 0036. Other backends have neither table."""
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        return
    with connection.cursor() as cursor:
        added = heal_mysql(cursor)
    if added:
        print(f"[migrations] added definition columns: {', '.join(added)}")
