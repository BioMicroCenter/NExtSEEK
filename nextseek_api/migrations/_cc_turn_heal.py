"""Create ``assistant_cc_turn`` with its chat column matched to ``assistant_chat_session.session_id``.

Migration 0023's database half. The seeded boxes keep the parent ``session_id`` in latin1 while a new table takes
the utf8mb4 database default, and InnoDB refuses a foreign key between columns whose charset or collation differ
(errno 3780): the lesson of 0007 and 0010. So MySQL gets one CREATE TABLE with every column, index and foreign key
inline, the chat column in the parent's charset and collation, and the two integer keys in their parents' exact
column types (read, never assumed). MySQL 8 creates a table and its inline foreign keys atomically, so a failure
leaves no table and a re-run starts clean; an existing table means a previous run succeeded. Every other backend
(the SQLite test lane) creates the model from 0023's own state, frozen, so a later AddField never collides with it.
"""
from __future__ import annotations

import re

CHILD_TABLE = "assistant_cc_turn"
CHAT_PARENT = ("assistant_chat_session", "session_id")
TASK_PARENT = ("assistant_query_task", "id")

_NAME_RE = re.compile(r"[A-Za-z0-9_]+")
_INTEGER_TYPE_RE = re.compile(r"(tiny|small|medium|big)?int(\(\d+\))?( unsigned)?")

_TABLE_EXISTS_SQL = (
    "SELECT COUNT(*) FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
)
_CHARSET_SQL = (
    "SELECT CHARACTER_SET_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s"
)
_COLUMN_TYPE_SQL = (
    "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s"
)

# Mirrors 0023's CreateModel field for field (BigAutoField pk, CharField(64) unique, two BinaryFields, four
# DateTimeFields, three JSONFields, DecimalField(10, 6), PositiveSmallIntegerField, the three keys) and its
# Meta.indexes. Django sets no database defaults; the ORM always writes the JSON columns.
_CREATE_TABLE_SQL = """
CREATE TABLE `assistant_cc_turn` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `pass_hash` varchar(64) NOT NULL,
  `login_nonce` longblob NULL,
  `login_ciphertext` longblob NULL,
  `created_at` datetime(6) NOT NULL,
  `deadline_at` datetime(6) NULL,
  `expires_at` datetime(6) NULL,
  `revoked_at` datetime(6) NULL,
  `vocabulary` json NULL,
  `plans` json NOT NULL,
  `strikes` json NOT NULL,
  `ops_cost_usd` decimal(10,6) NOT NULL,
  `ops_in_flight` smallint UNSIGNED NOT NULL,
  `chat_id` char(32) CHARACTER SET {chat_charset} COLLATE {chat_collation} NOT NULL,
  `task_id` {task_type} NOT NULL,
  `user_id` {user_type} NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `assistant_cc_turn_pass_hash_uniq` (`pass_hash`),
  UNIQUE KEY `assistant_cc_turn_task_id_uniq` (`task_id`),
  KEY `assistant_cc_turn_chat_id_idx` (`chat_id`),
  KEY `assistant_cc_turn_user_id_idx` (`user_id`),
  KEY `assistant_cc_turn_live` (`revoked_at`, `expires_at`),
  CONSTRAINT `assistant_cc_turn_chat_id_fk` FOREIGN KEY (`chat_id`) REFERENCES `assistant_chat_session` (`session_id`),
  CONSTRAINT `assistant_cc_turn_task_id_fk` FOREIGN KEY (`task_id`) REFERENCES `assistant_query_task` (`id`),
  CONSTRAINT `assistant_cc_turn_user_id_fk` FOREIGN KEY (`user_id`) REFERENCES `{user_table}` (`id`)
) ENGINE=InnoDB
"""


def _one(cursor, sql, params):
    cursor.execute(sql, params)
    return cursor.fetchone()


def _integer_type(cursor, table: str, column: str) -> str:
    row = _one(cursor, _COLUMN_TYPE_SQL, (table, column))
    value = str(row[0]).lower() if row and row[0] is not None else ""
    if not _INTEGER_TYPE_RE.fullmatch(value):
        raise RuntimeError(
            f"{table}.{column} is not an integer key ({value or 'missing'}); refusing to guess its type"
        )
    return value


def heal_mysql(cursor, *, user_table: str) -> list[str]:
    """Create the table unless it exists; return the DDL actions performed."""
    exists = _one(cursor, _TABLE_EXISTS_SQL, (CHILD_TABLE,))
    if exists and int(exists[0]):
        return []
    chat = _one(cursor, _CHARSET_SQL, CHAT_PARENT)
    if chat is None or not all(isinstance(v, str) and _NAME_RE.fullmatch(v) for v in chat):
        raise RuntimeError(
            f"{CHAT_PARENT[0]}.{CHAT_PARENT[1]} has no readable charset; cannot match the {CHILD_TABLE} key to it"
        )
    task_type = _integer_type(cursor, *TASK_PARENT)
    user_type = _integer_type(cursor, user_table, "id")
    if not _NAME_RE.fullmatch(user_table):
        raise RuntimeError(f"unexpected user table name {user_table!r}; refusing to interpolate it")
    cursor.execute(_CREATE_TABLE_SQL.format(
        chat_charset=chat[0], chat_collation=chat[1],
        task_type=task_type, user_type=user_type, user_table=user_table,
    ))
    return ["create_table"]


def _frozen_0023_model(apps):
    """CCTurn rendered from 0023's own CreateModel, never the live class (see the module docstring)."""
    from importlib import import_module

    from django.db.migrations.state import ProjectState

    migration = import_module("nextseek_api.migrations.0023_cc_turn").Migration
    state = ProjectState.from_apps(apps)
    for operation in migration.operations[0].state_operations:
        operation.state_forwards("nextseek_api", state)
    return state.apps.get_model("nextseek_api", "CCTurn")


def heal(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        if CHILD_TABLE not in connection.introspection.table_names():
            schema_editor.create_model(_frozen_0023_model(apps))
        return
    from django.conf import settings

    user_table = apps.get_model(settings.AUTH_USER_MODEL)._meta.db_table
    with connection.cursor() as cursor:
        actions = heal_mysql(cursor, user_table=user_table)
    if actions:
        print(f"[migrations] {CHILD_TABLE} heal applied: {', '.join(actions)}")


def unheal(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        if CHILD_TABLE in connection.introspection.table_names():
            schema_editor.delete_model(_frozen_0023_model(apps))
        return
    with connection.cursor() as cursor:
        exists = _one(cursor, _TABLE_EXISTS_SQL, (CHILD_TABLE,))
        if exists and int(exists[0]):
            cursor.execute(f"DROP TABLE `{CHILD_TABLE}`")
            print(f"[migrations] {CHILD_TABLE} heal reversed: drop_table")
