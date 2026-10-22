"""Migration seek.0003 creates project_template_bundles from the install's own DDL."""
import importlib

import pytest
from pathlib import Path
from unittest import mock

from django.db import connection
from django.db.migrations.loader import MigrationLoader

mig = importlib.import_module("seek.migrations.0003_project_template_bundles_table")
SQL_FILE = Path(__file__).resolve().parents[2] / "startup" / "seed" / "sql" / "project_template_bundles.sql"


def _statement(sql):
    """The file's one statement: comment lines dropped, trailing semicolon dropped."""
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    return body.strip().rstrip(";")


def _norm(sql):
    return " ".join(sql.split())


def _fixup_ddl_path():
    from startup.steps.schema_fixups import KNOWN_TABLE_FIXUPS
    return [f.ddl_path for f in KNOWN_TABLE_FIXUPS if f.table == "project_template_bundles"]


def test_migration_ddl_is_the_fixup_ddl():
    assert _fixup_ddl_path() == ["startup/seed/sql/project_template_bundles.sql"]
    assert _norm(mig.DDL) == _norm(_statement(SQL_FILE.read_text()))


def _fake_editor(vendor, alias):
    ed = mock.Mock()
    ed.connection.vendor = vendor
    ed.connection.alias = alias
    return ed


def test_runs_ddl_once_per_call_on_mysql_default():
    ed = _fake_editor("mysql", "default")
    mig._forward(None, ed)
    mig._forward(None, ed)  # twice: idempotence rests on IF NOT EXISTS in the file
    assert ed.execute.call_count == 2
    sql = ed.execute.call_args.args[0]
    assert sql == mig.DDL
    assert ed.execute.call_args.kwargs == {"params": None}


def test_noop_on_other_vendor_or_alias():
    for vendor, alias in (("sqlite", "default"), ("mysql", "seek")):
        ed = _fake_editor(vendor, alias)
        mig._forward(None, ed)
        ed.execute.assert_not_called()


def test_reverse_is_noop():
    op = mig.Migration.operations[0]
    assert op.reverse_code is mig.migrations.RunPython.noop


@pytest.mark.django_db
def test_single_leaf_in_seek_app():
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    leaves = [n for a, n in loader.graph.leaf_nodes() if a == "seek"]
    assert leaves == ["0003_project_template_bundles_table"]
