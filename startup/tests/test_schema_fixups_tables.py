"""Unit tests for the MissingTable half of schema_fixups.

Kept out of test_schema_fixups.py deliberately: that file is a pinned
real-boundary contract whose node names must not be renamed or collapsed, and
it runs against a disposable MySQL fixture. These are mock-level tests of the
table-creation path, which has no database of its own to talk to.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.lib.docker_ops import DockerOpsError
from startup.steps import schema_fixups as sf

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE_FIX = sf.MissingTable(
    database="dmac",
    table="sample_attributes_unique",
    ddl_path="startup/seed/sql/sample_attributes_unique.sql",
)


def _replies(*counts: str):
    """One INFORMATION_SCHEMA count per gate call, then empty strings for the
    statements that actually change the schema."""
    queue = list(counts)

    def _side_effect(*_args, **_kwargs):
        return queue.pop(0) if queue else ""

    return _side_effect


def test_the_definitions_table_is_registered():
    """A fresh install must create it: it is in no migration and no seed dump."""
    assert any(f.table == "sample_attributes_unique" for f in sf.KNOWN_TABLE_FIXUPS)


def test_the_registered_ddl_exists_and_is_rerunnable():
    for fix in sf.KNOWN_TABLE_FIXUPS:
        sql = (REPO_ROOT / fix.ddl_path).read_text()
        assert "CREATE TABLE IF NOT EXISTS" in sql, fix.ddl_path


@patch(f"{sf.__name__}.compose_exec")
def test_a_missing_table_is_created_from_its_ddl(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = _replies("0", "1")  # table absent, database present
    # Scoped to a single fixup: this test asserts the create/skip/missing-db
    # behaviour for one MissingTable, and its reply queue is sized for exactly
    # one -- it must not be coupled to how many entries the real registry holds.
    with patch.object(sf, "KNOWN_TABLE_FIXUPS", [TABLE_FIX]):
        result = sf.apply_table_fixups(REPO_ROOT, {})
    assert ("dmac.sample_attributes_unique", "created") in result
    piped = [c for c in mock_exec.call_args_list if c.kwargs.get("stdin")]
    assert len(piped) == 1
    assert b"CREATE TABLE IF NOT EXISTS" in piped[0].kwargs["stdin"]


@patch(f"{sf.__name__}.compose_exec")
def test_an_existing_table_is_left_alone(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = _replies("1")  # table already there
    with patch.object(sf, "KNOWN_TABLE_FIXUPS", [TABLE_FIX]):
        result = sf.apply_table_fixups(REPO_ROOT, {})
    assert ("dmac.sample_attributes_unique", "already present") in result
    assert not [c for c in mock_exec.call_args_list if c.kwargs.get("stdin")]


@patch(f"{sf.__name__}.compose_exec")
def test_a_missing_database_is_skipped_not_raised(mock_exec: MagicMock) -> None:
    """_table_exists returns False both when the table is absent and when the
    query errored. Without the database gate that falls into _create_table,
    whose mysql call raises and aborts the whole install."""
    mock_exec.side_effect = _replies("0", "0")  # table absent, database absent
    with patch.object(sf, "KNOWN_TABLE_FIXUPS", [TABLE_FIX]):
        result = sf.apply_table_fixups(REPO_ROOT, {})
    assert ("dmac.sample_attributes_unique", "database missing") in result
    assert not [c for c in mock_exec.call_args_list if c.kwargs.get("stdin")]


@patch(f"{sf.__name__}.compose_exec")
def test_a_missing_ddl_file_is_surfaced_not_swallowed(mock_exec: MagicMock) -> None:
    """A packaging error should stop the install loudly rather than leave the
    feature silently dark on every future one."""
    mock_exec.side_effect = _replies("0", "1")
    with patch.object(sf, "KNOWN_TABLE_FIXUPS",
                      [sf.MissingTable("dmac", "x", "startup/seed/sql/nope.sql")]):
        with pytest.raises(FileNotFoundError):
            sf.apply_table_fixups(REPO_ROOT, {})


@patch(f"{sf.__name__}.compose_exec")
def test_tables_are_created_before_columns_are_fixed(mock_exec: MagicMock) -> None:
    """A column fixup on a not-yet-created table reports 'table missing' and
    skips, so the ordering has to put table creation first."""
    # One "table already present" gate reply per registered table fixup, derived
    # rather than counted: three literals here went stale the moment
    # KNOWN_TABLE_FIXUPS grew, and the way that presented was an IndexError deep
    # in _table_exists rather than a wrong assertion.
    mock_exec.side_effect = _replies(*("1",) * len(sf.KNOWN_TABLE_FIXUPS))
    with patch.object(sf, "apply_column_fixups", return_value=[("c", "already present")]) as cols:
        with patch.object(sf, "managed_indexes_enabled", return_value=False):
            result = sf.apply_all(REPO_ROOT, {})
    assert cols.called
    assert result[0][0] == "dmac.sample_attributes_unique"


def test_sample_type_requirements_is_a_known_fixup():
    """A fresh install must get the table, or the picker silently shows no
    requirements with nothing to indicate why."""
    import startup.steps.schema_fixups as sf

    fix = next(f for f in sf.KNOWN_TABLE_FIXUPS
               if f.table == "sample_type_requirements")
    assert fix.database == "dmac"
    assert fix.ddl_path == "startup/seed/sql/sample_type_requirements.sql"


def test_sample_type_requirements_ddl_file_exists():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    ddl = root / "startup/seed/sql/sample_type_requirements.sql"
    assert ddl.is_file()
    assert "CREATE TABLE IF NOT EXISTS sample_type_requirements" in ddl.read_text()
