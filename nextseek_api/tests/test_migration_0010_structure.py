"""Regression contract for the MySQL-safe TurnLedger migration."""
from importlib import import_module

from django.db import migrations as dj_migrations

from nextseek_api.migrations import _turn_ledger_heal as heal


def test_0010_uses_idempotent_database_heal_with_original_state():
    migration = import_module("nextseek_api.migrations.0010_turn_ledger").Migration
    assert migration.atomic is False
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, dj_migrations.SeparateDatabaseAndState)
    assert [type(op) for op in operation.state_operations] == [
        dj_migrations.CreateModel,
        dj_migrations.AddIndex,
        dj_migrations.AddConstraint,
    ]
    assert len(operation.database_operations) == 1
    assert isinstance(operation.database_operations[0], dj_migrations.RunPython)
    assert operation.database_operations[0].reverse_code is not dj_migrations.RunPython.noop


def test_0010_state_keeps_the_declared_foreign_key():
    migration = import_module("nextseek_api.migrations.0010_turn_ledger").Migration
    create = migration.operations[0].state_operations[0]
    fields = dict(create.fields)
    assert fields["session"].remote_field.model == "nextseek_api.chatsession"
    assert fields["session"].db_constraint is True
    assert create.options["db_table"] == "assistant_turn_ledger"


class _Cursor:
    def __init__(self, *, result_sets, one=(0,)):
        self.result_sets = iter(result_sets)
        self.rows = ()
        self.one = one
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql
        if "SELECT COUNT(*)" not in sql:
            self.rows = next(self.result_sets)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


def test_partial_shape_requires_the_unique_constraint_to_be_unique():
    rows = [
        ("PRIMARY", 0, "id"),
        (heal.UNIQUE_INDEX_NAME, 1, "session_id,turn_number"),
        (heal.FAMILY_INDEX_NAME, 1, "task_family,route"),
    ]
    cursor = _Cursor(result_sets=[heal._EXPECTED_COLUMNS, rows], one=(0,))
    try:
        heal._assert_exact_empty_partial(cursor)
    except RuntimeError as exc:
        assert "unexpected indexes" in str(exc)
    else:
        raise AssertionError("non-unique turn constraint was accepted")


def test_only_the_declared_foreign_key_shape_is_accepted():
    declared = ((heal.FK_NAME, heal.CHILD_COLUMN, heal.PARENT_TABLE, heal.PARENT_COLUMN),)
    wrong_target = (("other_fk", heal.CHILD_COLUMN, "other_table", "id"),)
    additional = declared + (("other_fk", "route", "other_table", "id"),)
    assert heal._has_declared_foreign_key(declared)
    assert not heal._has_declared_foreign_key(wrong_target)
    assert not heal._has_declared_foreign_key(additional)
