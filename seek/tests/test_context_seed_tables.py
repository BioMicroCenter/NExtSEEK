"""The three seed DDL files exist, are well formed, and the bundles model maps one."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "startup" / "seed" / "sql"


@pytest.mark.parametrize("name,table", [
    ("assay_context.sql", "assay_context"),
    ("projects_context.sql", "projects_context"),
    ("project_template_bundles.sql", "project_template_bundles"),
])
def test_ddl_file_creates_its_table_idempotently(name, table):
    sql = (SQL / name).read_text()
    assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "ENGINE=InnoDB" in sql
    assert "utf8mb4" in sql


def test_assay_context_seed_carries_every_row_from_the_committed_export():
    """The seed is generated from a committed JSON export, so the counts must agree.

    Not a magic 217: read the export, so a regenerated seed stays honest.
    """
    export = json.loads(
        (ROOT / "chat_nextseek" / "src" / "chat_nextseek" / "context"
         / "assays_db.json").read_text()
    )
    sql = (SQL / "assay_context.sql").read_text()
    inserts = re.findall(r"^INSERT INTO `assay_context`", sql, flags=re.MULTILINE)
    assert len(inserts) == len(export)


def test_the_two_tables_that_ship_empty_carry_no_inserts():
    for name in ("projects_context.sql", "project_template_bundles.sql"):
        assert "INSERT INTO" not in (SQL / name).read_text()


def test_schema_fixups_declares_all_three():
    from startup.steps.schema_fixups import KNOWN_TABLE_FIXUPS

    declared = {(f.database, f.table, f.ddl_path) for f in KNOWN_TABLE_FIXUPS}
    for table in ("assay_context", "projects_context", "project_template_bundles"):
        assert ("dmac", table, f"startup/seed/sql/{table}.sql") in declared


def test_bundles_model_maps_the_table():
    from seek.models import Project_template_bundles

    assert Project_template_bundles._meta.db_table == "project_template_bundles"
    assert Project_template_bundles._meta.managed is False
    names = {f.name for f in Project_template_bundles._meta.get_fields()}
    assert {"project_id", "position", "label", "codes"} <= names
