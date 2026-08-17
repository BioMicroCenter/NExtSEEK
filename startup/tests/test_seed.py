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


# --- #92: dump-db on a host whose mysqldump is MariaDB's -------------------
#
# Two independent defects, both exercised below by running the real script
# against a fake mysqldump rather than grepping its source:
#   1. `--column-statistics=0` is an Oracle-only client option; MariaDB's
#      mysqldump exits 7 with "unknown variable" rather than ignoring it.
#   2. `| gzip > "$dest"` truncated the committed seed before mysqldump was
#      execed, so that exit destroyed the artifact being refreshed.

_FAKE_MYSQLDUMP = """#!/usr/bin/env bash
if [[ "$1" == "--help" ]]; then
  echo "  --single-transaction"
  echo "  --quick"
{column_statistics_help}
  exit 0
fi
printf '%s\\n' "$*" >> "$ARGV_LOG"
{dump_body}
"""


def _install_fake_dump_lane(tmp_path: Path, *, supports_colstats: bool, dump_ok: bool):
    """Stage a runnable copy of dump_mysql.sh with a fake mysqldump on PATH.

    Returns (script_path, seed_dir, env_with_fake_path, argv_log).
    """
    seed_dir = tmp_path / "seed"
    regen_dir = seed_dir / "regenerate"
    regen_dir.mkdir(parents=True)

    real = _REPO_ROOT / "startup" / "seed" / "regenerate" / "dump_mysql.sh"
    script = regen_dir / "dump_mysql.sh"
    script.write_bytes(real.read_bytes())
    script.chmod(0o755)

    (regen_dir / "dump-source.env").write_text(
        "MYSQL_HOST_DEV=example.invalid\n"
        "MYSQL_PORT=3306\n"
        "MYSQL_USER=nobody\n"
        "MYSQL_DEV_PASSWORD=unused\n"
    )

    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_log = tmp_path / "argv.log"
    fake = bindir / "mysqldump"
    fake.write_text(
        _FAKE_MYSQLDUMP.format(
            column_statistics_help='  echo "  --column-statistics"' if supports_colstats else "  :",
            dump_body="echo '-- dump payload'" if dump_ok else "exit 7",
        )
    )
    fake.chmod(0o755)

    import os

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["ARGV_LOG"] = str(argv_log)
    return script, seed_dir, env, argv_log


def test_dump_script_omits_column_statistics_when_client_lacks_it(tmp_path: Path) -> None:
    """MariaDB's mysqldump has no --column-statistics; passing it exits 7."""
    import subprocess

    script, _seed, env, argv_log = _install_fake_dump_lane(
        tmp_path, supports_colstats=False, dump_ok=True
    )
    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    invocations = argv_log.read_text()
    assert "--column-statistics" not in invocations, (
        "the option was passed to a client that does not implement it"
    )


def test_dump_script_passes_column_statistics_when_client_has_it(tmp_path: Path) -> None:
    """Oracle MySQL 8 clients still get the option; the probe is not a blanket drop."""
    import subprocess

    script, _seed, env, argv_log = _install_fake_dump_lane(
        tmp_path, supports_colstats=True, dump_ok=True
    )
    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "--column-statistics=0" in argv_log.read_text()


def test_failed_dump_leaves_the_existing_seed_intact(tmp_path: Path) -> None:
    """A non-zero mysqldump must not destroy the dump it was refreshing.

    This is the severity of #92: the shell truncated the redirect target before
    mysqldump ran, so a failure replaced a 3.7 MB seed with a 20-byte valid-but-
    empty gzip stream that `gzip -t` reports as clean.
    """
    import subprocess

    script, seed_dir, env, _log = _install_fake_dump_lane(
        tmp_path, supports_colstats=False, dump_ok=False
    )
    existing = seed_dir / "dmac.sql.gz"
    with gzip.open(existing, "wt", encoding="utf-8") as fh:
        fh.write("-- previous good seed\n")
    before = existing.read_bytes()

    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode != 0, "the fake dump was supposed to fail"
    assert existing.read_bytes() == before, (
        "a failed dump-db run destroyed the previous seed"
    )
    leftovers = list(seed_dir.glob("*.tmp.*"))
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_successful_dump_replaces_the_seed(tmp_path: Path) -> None:
    """The safety net must not stop the script doing its job."""
    import subprocess

    script, seed_dir, env, _log = _install_fake_dump_lane(
        tmp_path, supports_colstats=False, dump_ok=True
    )
    existing = seed_dir / "dmac.sql.gz"
    with gzip.open(existing, "wt", encoding="utf-8") as fh:
        fh.write("-- previous good seed\n")

    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    with gzip.open(existing, "rt", encoding="utf-8") as fh:
        assert "dump payload" in fh.read()
    assert (seed_dir / "seek_production.sql.gz").exists()
    assert not list(seed_dir.glob("*.tmp.*"))
