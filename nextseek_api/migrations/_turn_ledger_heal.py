"""Idempotent database convergence for migration 0010's TurnLedger table."""
from __future__ import annotations

PARENT_TABLE = "assistant_chat_session"
PARENT_COLUMN = "session_id"
CHILD_TABLE = "assistant_turn_ledger"
CHILD_COLUMN = "session_id"
FK_NAME = "assistant_turn_ledger_session_id_fk"
UNIQUE_INDEX_NAME = "uniq_turn_per_session"
FAMILY_INDEX_NAME = "assistant_t_task_fa_6d0f8a_idx"

_EXPECTED_COLUMNS = (
    ("id", "bigint", "NO", "auto_increment"),
    ("turn_number", "int", "NO", ""),
    ("route", "varchar(64)", "NO", ""),
    ("route_source", "varchar(32)", "NO", ""),
    ("task_family", "varchar(128)", "YES", ""),
    ("family_source", "varchar(32)", "YES", ""),
    ("created_at", "datetime(6)", "NO", ""),
    ("session_id", "char(32)", "NO", ""),
)

_CREATE_TABLE_SQL = f"""
CREATE TABLE `{CHILD_TABLE}` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `turn_number` int NOT NULL,
  `route` varchar(64) NOT NULL,
  `route_source` varchar(32) NOT NULL,
  `task_family` varchar(128) NULL,
  `family_source` varchar(32) NULL,
  `created_at` datetime(6) NOT NULL,
  `{CHILD_COLUMN}` char(32) CHARACTER SET {{charset}} COLLATE {{collation}} NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `{UNIQUE_INDEX_NAME}` (`{CHILD_COLUMN}`, `turn_number`),
  KEY `{FAMILY_INDEX_NAME}` (`task_family`, `route`)
) ENGINE=InnoDB
"""


def _charset(cursor, table: str, column: str):
    cursor.execute(
        "SELECT CHARACTER_SET_NAME, COLLATION_NAME "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cursor.fetchone()


def _foreign_keys(cursor) -> tuple[tuple[str, str, str, str], ...]:
    cursor.execute(
        "SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
        "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION",
        (CHILD_TABLE,),
    )
    return tuple(tuple(str(value) for value in row) for row in cursor.fetchall())


def _has_declared_foreign_key(foreign_keys) -> bool:
    return foreign_keys == (
        (FK_NAME, CHILD_COLUMN, PARENT_TABLE, PARENT_COLUMN),
    )


def _assert_exact_empty_partial(cursor) -> None:
    cursor.execute(f"SELECT COUNT(*) FROM `{CHILD_TABLE}`")
    row_count = int(cursor.fetchone()[0])
    if row_count:
        raise RuntimeError(
            f"{CHILD_TABLE} exists without its FK and contains {row_count} row(s); "
            "refusing to adopt or rewrite retained data"
        )

    cursor.execute(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, EXTRA "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        (CHILD_TABLE,),
    )
    columns = tuple(tuple(str(value or "") for value in row) for row in cursor.fetchall())
    if columns != _EXPECTED_COLUMNS:
        raise RuntimeError(
            f"{CHILD_TABLE} exists in an unexpected FK-less shape; refusing to adopt it"
        )

    cursor.execute(
        "SELECT INDEX_NAME, NON_UNIQUE, "
        "GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
        "FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "GROUP BY INDEX_NAME, NON_UNIQUE",
        (CHILD_TABLE,),
    )
    indexes = {
        str(name): (int(non_unique), str(columns))
        for name, non_unique, columns in cursor.fetchall()
    }
    expected = {
        "PRIMARY": (0, "id"),
        UNIQUE_INDEX_NAME: (0, "session_id,turn_number"),
        FAMILY_INDEX_NAME: (1, "task_family,route"),
    }
    if indexes != expected:
        raise RuntimeError(
            f"{CHILD_TABLE} exists with unexpected indexes; refusing to adopt it"
        )


def heal_mysql(cursor) -> list[str]:
    """Converge fresh or exact empty-partial MySQL state without faking."""
    parent = _charset(cursor, PARENT_TABLE, PARENT_COLUMN)
    if parent is None:
        raise RuntimeError(
            f"{PARENT_TABLE}.{PARENT_COLUMN} is missing; cannot create the ledger FK"
        )
    parent_charset, parent_collation = parent
    child = _charset(cursor, CHILD_TABLE, CHILD_COLUMN)
    actions: list[str] = []

    if child is None:
        cursor.execute(
            _CREATE_TABLE_SQL.format(
                charset=parent_charset, collation=parent_collation
            )
        )
        actions.append("create_table")
    else:
        foreign_keys = _foreign_keys(cursor)
        if foreign_keys and not _has_declared_foreign_key(foreign_keys):
            raise RuntimeError(
                f"{CHILD_TABLE} has unexpected foreign keys; refusing to adopt it"
            )
        if not foreign_keys:
            _assert_exact_empty_partial(cursor)
        if (child[0], child[1]) != (parent_charset, parent_collation):
            if foreign_keys:
                raise RuntimeError(
                    f"{CHILD_TABLE}.{CHILD_COLUMN} has an FK but its charset differs "
                    "from the parent; refusing an in-place retained-data rewrite"
                )
            cursor.execute(
                f"ALTER TABLE `{CHILD_TABLE}` MODIFY `{CHILD_COLUMN}` char(32) "
                f"CHARACTER SET {parent_charset} COLLATE {parent_collation} NOT NULL"
            )
            actions.append("align_charset")

    foreign_keys = _foreign_keys(cursor)
    if foreign_keys and not _has_declared_foreign_key(foreign_keys):
        raise RuntimeError(
            f"{CHILD_TABLE} has unexpected foreign keys; refusing to adopt it"
        )
    if not foreign_keys:
        cursor.execute(
            f"ALTER TABLE `{CHILD_TABLE}` ADD CONSTRAINT `{FK_NAME}` "
            f"FOREIGN KEY (`{CHILD_COLUMN}`) "
            f"REFERENCES `{PARENT_TABLE}` (`{PARENT_COLUMN}`)"
        )
        actions.append("add_fk")
    return actions


def _frozen_0010_model(apps):
    from importlib import import_module

    from django.db.migrations.state import ProjectState

    migration = import_module("nextseek_api.migrations.0010_turn_ledger").Migration
    state = ProjectState.from_apps(apps)
    for operation in migration.operations[0].state_operations:
        operation.state_forwards("nextseek_api", state)
    return state.apps.get_model("nextseek_api", "TurnLedger")


def heal(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        if CHILD_TABLE not in connection.introspection.table_names():
            schema_editor.create_model(_frozen_0010_model(apps))
        return
    with connection.cursor() as cursor:
        actions = heal_mysql(cursor)
    if actions:
        print(f"[migrations] {CHILD_TABLE} heal applied: {', '.join(actions)}")


def unheal_mysql(cursor) -> list[str]:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (CHILD_TABLE,),
    )
    if int(cursor.fetchone()[0]) == 0:
        return []
    cursor.execute(f"DROP TABLE `{CHILD_TABLE}`")
    return ["drop_table"]


def unheal(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        if CHILD_TABLE in connection.introspection.table_names():
            schema_editor.delete_model(_frozen_0010_model(apps))
        return
    with connection.cursor() as cursor:
        actions = unheal_mysql(cursor)
    if actions:
        print(f"[migrations] {CHILD_TABLE} heal reversed: {', '.join(actions)}")
