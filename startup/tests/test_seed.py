"""Tests for startup.steps.seed."""
from __future__ import annotations

import gzip
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.steps.seed import (
    SEED_FILES,
    seed_files_present,
    mysql_db_is_populated,
    neo4j_is_populated,
    parse_neo4j_cypher_dump,
    _cypher_map_to_dict,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


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


# --- Neo4j cypher-dump parser ---------------------------------------------------

def test_parse_nodes_grouped_by_labelset_with_props() -> None:
    dump = (
        'CREATE (n0:Sample:_ImportRef {`id`: 5, `uuid`: "AB-1", `_exportId`: 0});\n'
        'CREATE (n1:Sample:_ImportRef {`id`: 6, `uuid`: "AB-2", `_exportId`: 1});\n'
        'CREATE (n2:Study:_ImportRef {`title`: "S1", `_exportId`: 2});\n'
    )
    nodes, rels = parse_neo4j_cypher_dump(dump)
    assert rels == {}
    assert set(nodes) == {":Sample:_ImportRef", ":Study:_ImportRef"}
    assert len(nodes[":Sample:_ImportRef"]) == 2
    assert nodes[":Sample:_ImportRef"][0] == {"id": 5, "uuid": "AB-1", "_exportId": 0}
    assert nodes[":Study:_ImportRef"][0] == {"title": "S1", "_exportId": 2}


def test_parse_relationships_with_and_without_props_grouped_by_type() -> None:
    dump = (
        "MATCH (a:_ImportRef {`_exportId`: 1}) MATCH (b:_ImportRef {`_exportId`: 2}) "
        "CREATE (a)-[:IN_STUDY]->(b);\n"
        "MATCH (a:_ImportRef {`_exportId`: 3}) MATCH (b:_ImportRef {`_exportId`: 4}) "
        'CREATE (a)-[:DERIVED_FROM {`protocol_id`: 7, `note`: "x"}]->(b);\n'
    )
    nodes, rels = parse_neo4j_cypher_dump(dump)
    assert nodes == {}
    assert rels["IN_STUDY"] == [{"a": 1, "b": 2, "props": {}}]
    assert rels["DERIVED_FROM"] == [{"a": 3, "b": 4, "props": {"protocol_id": 7, "note": "x"}}]


def test_parse_skips_index_and_cleanup_scaffolding() -> None:
    dump = (
        'CREATE (n0:Sample:_ImportRef {`_exportId`: 0});\n'
        "CREATE INDEX _import_ref_eid_idx IF NOT EXISTS FOR (n:_ImportRef) ON (n._exportId);\n"
        "MATCH (n:_ImportRef) REMOVE n:_ImportRef, n._exportId;\n"
        "DROP INDEX _import_ref_eid_idx IF EXISTS;\n"
    )
    nodes, rels = parse_neo4j_cypher_dump(dump)
    assert list(nodes) == [":Sample:_ImportRef"]
    assert rels == {}


def test_parse_raises_on_unrecognized_statement() -> None:
    with pytest.raises(ValueError):
        parse_neo4j_cypher_dump("DELETE everything;\n")


def test_cypher_map_tolerates_literal_control_char_in_value() -> None:
    # dump_neo4j.py's _escape does not escape tabs; strict JSON would reject them.
    parsed = _cypher_map_to_dict('{`desc`: "a\tb"}')
    assert parsed == {"desc": "a\tb"}


def test_cypher_map_does_not_treat_backtick_inside_string_as_key() -> None:
    parsed = _cypher_map_to_dict('{`name`: "has `backtick` inside"}')
    assert parsed == {"name": "has `backtick` inside"}


def test_cypher_map_value_types() -> None:
    parsed = _cypher_map_to_dict(
        '{`i`: 3, `f`: 1.5, `s`: "txt", `b`: true, `n`: null, `lst`: [1, 2]}'
    )
    assert parsed == {"i": 3, "f": 1.5, "s": "txt", "b": True, "n": None, "lst": [1, 2]}


def test_committed_seed_carries_no_site_base_host_row() -> None:
    """site_base_host is per-instance deployment config, not seed data.

    The committed seed is universal (laptop / dev / prod). A baked hostname would
    silently repoint every identifier a fresh install publishes -- and dev's DB now
    HAS such a row, so an unfiltered `dump-db` would capture it. dump_mysql.sh
    filters it out; this locks that guarantee against the artifact itself.
    """
    seed = _REPO_ROOT / "startup" / "seed" / "seek_production.sql.gz"
    assert seed.exists(), seed
    with gzip.open(seed, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            assert "site_base_host" not in line, (
                "committed seed contains a site_base_host row -- a dump-db run has "
                "baked one instance's hostname into the universal seed"
            )


def test_dump_script_filters_site_base_host_out_of_settings() -> None:
    """The maintainer dump must not be able to re-introduce the row."""
    script = _REPO_ROOT / "startup" / "seed" / "regenerate" / "dump_mysql.sh"
    body = script.read_text()
    assert "site_base_host" in body, "dump_mysql.sh must explicitly exclude site_base_host"
    assert "--ignore-table" in body or "--where" in body, (
        "dump_mysql.sh must filter the settings table rather than dump it wholesale"
    )
