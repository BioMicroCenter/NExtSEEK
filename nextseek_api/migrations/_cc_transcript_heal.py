"""Idempotent heal for assistant_cc_transcript + its FK (Bug C, 2026-07-07).

Shared by migrations 0007 (rewritten in place) and 0008 (companion). The
original 0007 CreateModel built the table with the DB default charset
(utf8mb4 on fresh installs) while the seed dump ships the referenced
``assistant_chat_session`` as latin1; InnoDB requires FK column pairs to match
charset AND collation exactly, so the deferred ``ADD FOREIGN KEY`` failed with
errno 3780 (ER_FK_INCOMPATIBLE_COLUMNS). MySQL DDL is non-transactional, so
the table (with PK + unique triple, no FK) survived while 0007 stayed
unrecorded — wedging migrate on every boot of a seeded greenfield. The live
dev DB is a fourth shape: 0007 recorded (out-of-band, 2026-07-01) with the FK
absent, which only 0008 can reach.

The heal converges every observed state to the same invariant —
``chat_session_id`` charset/collation == parent ``session_id``'s, table
present, FK present — and no-ops (returns []) when already converged:

- fresh seed (latin1 parent, no child): CREATE charset-matched + ADD FK;
- wedged (child exists FK-less, charset mismatched): MODIFY + ADD FK
  (lossless — values are ASCII UUID hex);
- live-dev shape (same schema as wedged; ledger differs): via 0008;
- native utf8mb4 (test_dmac, --no-seed): CREATE utf8mb4-matched + ADD FK
  through 0007's RunPython; 0008 no-ops.
"""
from __future__ import annotations

PARENT_TABLE = "assistant_chat_session"
PARENT_COLUMN = "session_id"
CHILD_TABLE = "assistant_cc_transcript"
CHILD_COLUMN = "chat_session_id"

# Django's generated unique_together index name (verified identical on the
# live dev DB and the Step 7d greenfield) — reused for byte-parity between
# heal-created and Django-created tables.
UNIQUE_INDEX_NAME = "assistant_cc_transcript_chat_session_id_cc_sessi_bdda2d20_uniq"
FK_NAME = "assistant_cc_transcript_chat_session_id_fk"

# Mirrors 0007's CreateModel field-for-field (BigAutoField pk, CharField(128)
# x2, BinaryField, BigIntegerField, DateTimeField(6), FK char(32)). The FK
# column's charset/collation is interpolated from the introspected parent; the
# table default stays the DB default, matching what Django itself creates. No
# dedicated chat_session_id index: the unique triple's leftmost prefix serves
# as the FK supporting index, exactly like Django's own FK handling here.
_CREATE_TABLE_SQL = f"""
CREATE TABLE `{CHILD_TABLE}` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `cc_session_id` varchar(128) NOT NULL,
  `turn_id` varchar(128) NOT NULL,
  `blob` longblob NOT NULL,
  `uncompressed_size` bigint NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `{CHILD_COLUMN}` char(32) CHARACTER SET {{charset}} COLLATE {{collation}} NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `{UNIQUE_INDEX_NAME}` (`{CHILD_COLUMN}`,`cc_session_id`,`turn_id`)
) ENGINE=InnoDB
"""


def heal_mysql(cursor) -> list[str]:
    """Converge the current database; return the DDL actions performed.

    Raises RuntimeError (loudly, with counts) instead of guessing when the
    parent table is missing or orphaned child rows would poison the FK.
    """

    def fetch_charset(table: str, column: str):
        cursor.execute(
            "SELECT CHARACTER_SET_NAME, COLLATION_NAME "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column),
        )
        return cursor.fetchone()

    actions: list[str] = []

    parent = fetch_charset(PARENT_TABLE, PARENT_COLUMN)
    if parent is None:
        raise RuntimeError(
            f"{PARENT_TABLE}.{PARENT_COLUMN} not found in the current database — "
            f"cannot align the {CHILD_TABLE} FK charset"
        )
    parent_charset, parent_collation = parent

    child = fetch_charset(CHILD_TABLE, CHILD_COLUMN)
    if child is None:
        cursor.execute(
            _CREATE_TABLE_SQL.format(charset=parent_charset, collation=parent_collation)
        )
        actions.append("create_table")
    elif (child[0], child[1]) != (parent_charset, parent_collation):
        # Lossless: session ids are 32-char ASCII hex under any charset here.
        cursor.execute(
            f"ALTER TABLE `{CHILD_TABLE}` MODIFY `{CHILD_COLUMN}` char(32) "
            f"CHARACTER SET {parent_charset} COLLATE {parent_collation} NOT NULL"
        )
        actions.append("align_charset")

    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s "
        "AND REFERENCED_TABLE_NAME = %s AND REFERENCED_COLUMN_NAME = %s",
        (CHILD_TABLE, CHILD_COLUMN, PARENT_TABLE, PARENT_COLUMN),
    )
    if cursor.fetchone()[0] == 0:
        # Post-alignment both sides share a collation, so the join is legal
        # (a cross-charset join would raise 1267 'Illegal mix of collations').
        cursor.execute(
            f"SELECT COUNT(*) FROM `{CHILD_TABLE}` t "
            f"LEFT JOIN `{PARENT_TABLE}` s ON t.`{CHILD_COLUMN}` = s.`{PARENT_COLUMN}` "
            f"WHERE s.`{PARENT_COLUMN}` IS NULL"
        )
        orphans = cursor.fetchone()[0]
        if orphans:
            raise RuntimeError(
                f"{orphans} orphaned {CHILD_TABLE} row(s) reference missing "
                f"{PARENT_TABLE} sessions — resolve them before the FK can be "
                "added (refusing to guess)"
            )
        cursor.execute(
            f"ALTER TABLE `{CHILD_TABLE}` ADD CONSTRAINT `{FK_NAME}` "
            f"FOREIGN KEY (`{CHILD_COLUMN}`) "
            f"REFERENCES `{PARENT_TABLE}` (`{PARENT_COLUMN}`)"
        )
        actions.append("add_fk")

    return actions


def heal(apps, schema_editor):
    """RunPython entrypoint shared by 0007 and 0008."""
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        # Non-MySQL (hermetic sqlite): charset FKs aren't a thing there; just
        # make sure the table exists, mirroring the original CreateModel.
        if CHILD_TABLE not in connection.introspection.table_names():
            try:
                model = apps.get_model("nextseek_api", "CCSessionTranscript")
            except LookupError:
                # Inside 0007's SeparateDatabaseAndState the RunPython receives
                # the PRE-migration state (Django passes from_state.apps), which
                # doesn't yet contain the model this migration creates. The
                # real model matches 0007's CreateModel field-for-field
                # (guarded by test_migration_0007_structure + the app-scoped
                # `makemigrations nextseek_api --check` gate).
                from nextseek_api.assistant.models_db import (
                    CCSessionTranscript as model,
                )
            schema_editor.create_model(model)
        return

    with connection.cursor() as cursor:
        actions = heal_mysql(cursor)
    if actions:
        print(f"[migrations] {CHILD_TABLE} heal applied: {', '.join(actions)}")
