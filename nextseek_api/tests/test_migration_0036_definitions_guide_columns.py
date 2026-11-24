"""Migration 0036: when_to_use and similar_types on the two context tables, added once, only where the table is."""
import re
from types import SimpleNamespace

from django.db.migrations.loader import MigrationLoader

from nextseek_api.migrations._definitions_guide_columns import COLUMNS, TABLES, heal, heal_mysql

MIGRATION = ("nextseek_api", "0036_definitions_guide_columns")
TEXT = "TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL"


class _Cursor:
    """information_schema answers from ``tables`` (table -> column names); an ALTER ADD updates it."""

    def __init__(self, tables):
        self.tables = {t: list(c) for t, c in tables.items()}
        self.statements = []
        self._rows = []

    def execute(self, sql, params=None):
        if "information_schema.COLUMNS" in sql:
            self._rows = [(c,) for c in self.tables.get(params[0], [])]
            return
        self.statements.append(sql)
        table, column = re.match(r"ALTER TABLE `(\w+)` ADD COLUMN `(\w+)`", sql).groups()
        self.tables[table].append(column)

    def fetchall(self):
        return self._rows


def test_0036_is_the_head_of_the_single_chain_on_0035_and_reversible():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    leaves = loader.graph.leaf_nodes("nextseek_api")
    assert leaves == [MIGRATION]
    migration = loader.get_migration(*MIGRATION)
    assert migration.dependencies == [("nextseek_api", "0035_sample_type_connections")]
    assert migration.atomic is False and migration.operations[0].reversible


def test_each_missing_column_is_added_once_as_utf8mb4_text_and_a_second_run_does_nothing():
    cursor = _Cursor({"sample_types_context": ["id", "sample_type"], "assay_context": ["id", "assay_name"]})
    assert heal_mysql(cursor) == [f"{t}.{c}" for t in TABLES for c in COLUMNS]
    assert len(cursor.statements) == 4
    for column in COLUMNS:
        assert f"ADD COLUMN `{column}` {TEXT}" in " ".join(cursor.statements)
    assert heal_mysql(cursor) == [] and len(cursor.statements) == 4


def test_only_the_absent_column_is_added():
    cursor = _Cursor({"sample_types_context": ["id", "when_to_use"], "assay_context": ["id", "similar_types"]})
    assert heal_mysql(cursor) == ["sample_types_context.similar_types", "assay_context.when_to_use"]


def test_a_table_that_is_absent_is_skipped():
    cursor = _Cursor({"assay_context": ["id"]})
    assert heal_mysql(cursor) == ["assay_context.when_to_use", "assay_context.similar_types"]
    assert heal_mysql(_Cursor({})) == []


def test_a_vendor_that_is_not_mysql_is_left_alone():
    class Boom:
        vendor = "sqlite"

        def cursor(self):
            raise AssertionError("no cursor on a test database")

    heal(None, SimpleNamespace(connection=Boom()))
