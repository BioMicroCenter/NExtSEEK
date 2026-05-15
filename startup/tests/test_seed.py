"""Tests for startup.steps.seed."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.steps.seed import (
    SEED_FILES,
    seed_files_present,
    mysql_db_is_populated,
    neo4j_is_populated,
)


def test_seed_files_constant() -> None:
    assert SEED_FILES["dmac"].endswith("dmac.sql.gz")
    assert SEED_FILES["seek_production"].endswith("seek_production.sql.gz")
    assert SEED_FILES["neo4j"].endswith("neo4j.cypher.gz")


def test_seed_files_present_true_when_all_three_exist(tmp_path: Path) -> None:
    seed_dir = tmp_path / "startup" / "seed"
    seed_dir.mkdir(parents=True)
    (seed_dir / "dmac.sql.gz").write_bytes(b"x")
    (seed_dir / "seek_production.sql.gz").write_bytes(b"x")
    (seed_dir / "neo4j.cypher.gz").write_bytes(b"x")
    missing = seed_files_present(tmp_path)
    assert missing == []


def test_seed_files_present_lists_missing(tmp_path: Path) -> None:
    seed_dir = tmp_path / "startup" / "seed"
    seed_dir.mkdir(parents=True)
    (seed_dir / "dmac.sql.gz").write_bytes(b"x")
    missing = seed_files_present(tmp_path)
    assert "seek_production.sql.gz" in missing
    assert "neo4j.cypher.gz" in missing
    assert "dmac.sql.gz" not in missing


@patch("startup.steps.seed.compose_exec")
def test_mysql_db_is_populated_true_when_tables_exist(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "42\n"
    assert mysql_db_is_populated(database="dmac", repo_root=Path("/repo"), env={}) is True


@patch("startup.steps.seed.compose_exec")
def test_mysql_db_is_populated_false_when_zero_tables(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "0\n"
    assert mysql_db_is_populated(database="dmac", repo_root=Path("/repo"), env={}) is False


@patch("startup.steps.seed.compose_exec")
def test_neo4j_is_populated_true_when_nodes_exist(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "count\n51032\n"
    assert neo4j_is_populated(neo4j_password="x", repo_root=Path("/repo"), env={}) is True


@patch("startup.steps.seed.compose_exec")
def test_neo4j_is_populated_false_when_zero(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "count\n0\n"
    assert neo4j_is_populated(neo4j_password="x", repo_root=Path("/repo"), env={}) is False
