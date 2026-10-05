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
            if line.startswith("--"):
                continue  # mysqldump's own `-- WHERE:  var <> 'site_base_host'` note on the filtered table
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


def test_dump_script_drops_the_mariadb_sandbox_line(tmp_path: Path) -> None:
    """A MariaDB 10.5.25+/11.x client opens every dump with a line MySQL 8's client rejects at install."""
    import subprocess

    script, seed_dir, env, _log = _install_fake_dump_lane(tmp_path, supports_colstats=False, dump_ok=True)
    fake = Path(env["PATH"].split(":")[0]) / "mysqldump"
    fake.write_text(fake.read_text().replace(
        "echo '-- dump payload'",
        "printf '%s\\n' '/*M!999999\\- enable the sandbox mode */' '-- dump payload'"))
    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    for name in ("dmac.sql.gz", "seek_production.sql.gz"):
        with gzip.open(seed_dir / name, "rt", encoding="utf-8") as fh:
            text = fh.read()
        assert "sandbox mode" not in text, f"{name} still opens with the MariaDB sandbox line"
        assert "dump payload" in text


def test_dump_script_writes_the_dmac_seed_as_utf8mb4(tmp_path: Path) -> None:
    """R7 holds even when the source dmac database is latin1: table defaults and the latin1 key columns that match
    them become utf8mb4 together (or the foreign keys would not load), and a data row is never rewritten."""
    import subprocess

    script, seed_dir, env, _log = _install_fake_dump_lane(tmp_path, supports_colstats=False, dump_ok=True)
    fake = Path(env["PATH"].split(":")[0]) / "mysqldump"
    fake.write_text(fake.read_text().replace(
        "echo '-- dump payload'",
        "printf '%s\\n' 'CREATE TABLE `t` (' '  `k` char(32) CHARACTER SET latin1 COLLATE latin1_swedish_ci NOT NULL,'"
        " ') ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=latin1;'"
        " \"INSERT INTO \\`t\\` VALUES ('x CHARACTER SET latin1 COLLATE latin1_swedish_ci DEFAULT CHARSET=latin1');\""))
    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    with gzip.open(seed_dir / "dmac.sql.gz", "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert "  `k` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL," in lines
    assert ") ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;" in lines
    assert any(line.startswith("INSERT") and "CHARACTER SET latin1" in line for line in lines), "a data row was rewritten"
    with gzip.open(seed_dir / "seek_production.sql.gz", "rt", encoding="utf-8") as fh:
        assert "DEFAULT CHARSET=latin1" in fh.read(), "only the dmac seed is rewritten"


def test_dump_script_runs_the_client_named_by_mysqldump(tmp_path: Path) -> None:
    """MYSQLDUMP picks the client, e.g. a MySQL container's own (`docker exec -i <c> mysqldump`)."""
    import subprocess

    script, seed_dir, env, argv_log = _install_fake_dump_lane(tmp_path, supports_colstats=True, dump_ok=True)
    bindir = Path(env["PATH"].split(":")[0])
    (bindir / "mysqldump").rename(bindir / "container-mysqldump")
    env["MYSQLDUMP"] = f"{bindir / 'container-mysqldump'}"
    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "--column-statistics=0" in argv_log.read_text()
    with gzip.open(seed_dir / "dmac.sql.gz", "rt", encoding="utf-8") as fh:
        assert "dump payload" in fh.read()


# --- temporal properties: dump_neo4j.py writes them as Cypher function calls -------------------------------
# Every Sample carries `synced_at` (a DateTime) since graph schema 1.1, and many carry a Date attribute, so the
# fast loader must read `datetime("...")` and friends, and give the driver the same type back.

@pytest.fixture
def dump_neo4j(monkeypatch):
    """Import startup/seed/regenerate/dump_neo4j.py; its python-dotenv import is maintainer-only, so stub it for
    this test only."""
    import importlib.util
    import sys
    import types

    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    path = _REPO_ROOT / "startup" / "seed" / "regenerate" / "dump_neo4j.py"
    spec = importlib.util.spec_from_file_location("dump_neo4j_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cypher_map_reads_temporal_calls_as_markers() -> None:
    from startup.steps.seed import Temporal

    d = _cypher_map_to_dict(
        '{`synced_at`: datetime("2026-10-04T03:04:24.367000000+00:00"), `SampleCreationDate`: date("2023-07-13"), '
        '`t`: localdatetime("2023-07-13T10:00:00"), `d`: duration("P1DT2H"), `xs`: [date("2020-01-01")], `n`: 3}'
    )
    assert d["synced_at"] == Temporal("datetime", "2026-10-04T03:04:24.367000000+00:00")
    assert d["SampleCreationDate"] == Temporal("date", "2023-07-13")
    assert d["t"] == Temporal("localdatetime", "2023-07-13T10:00:00")
    assert d["d"] == Temporal("duration", "P1DT2H")
    assert d["xs"] == [Temporal("date", "2020-01-01")]
    assert d["n"] == 3


def test_cypher_map_leaves_temporal_text_inside_strings_alone() -> None:
    d = _cypher_map_to_dict('{`note`: "datetime(\\"2020\\") is text", `k`: "date(\\"x\\")"}')
    assert d == {"note": 'datetime("2020") is text', "k": 'date("x")'}


def test_temporal_round_trip_escape_parse_driver(dump_neo4j) -> None:
    """dump_neo4j._escape -> install parser -> driver value equals what the source graph held, type included."""
    import datetime

    import neo4j.time as nt
    from startup.steps.seed import driver_value

    values = {
        "dt": nt.DateTime(2026, 10, 4, 3, 4, 24, 367000000, tzinfo=datetime.timezone.utc),
        "local": nt.DateTime(2023, 7, 13, 10, 0, 0, 5),
        "day": nt.Date(2023, 7, 13),
        "dur": nt.Duration(days=1, hours=2),
        "plain": "datetime(\"not a call\")",
    }
    parsed = _cypher_map_to_dict(dump_neo4j._props_str(values))
    for k, v in values.items():
        back = driver_value(parsed[k])
        assert type(back) is type(v), (k, type(back), type(v))
        assert back == v, (k, back, v)


def test_box_graph_exporter_writes_what_dump_neo4j_writes(dump_neo4j) -> None:
    """The refresh kit's on-box exporter keeps its own copy of the escaper (it runs in the app container, without
    python-dotenv); both must write the same text for every kind of value."""
    import datetime
    import importlib.util

    import neo4j.time as nt

    path = _REPO_ROOT / "startup" / "seed" / "regenerate" / "refresh" / "box" / "graph_export.py"
    spec = importlib.util.spec_from_file_location("graph_export_under_test", path)
    box = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(box)
    values = {"s": 'a "q" \\ \n', "n": None, "b": True, "i": 3, "f": 1.5, "l": ["x", 2],
              "dt": nt.DateTime(2026, 1, 2, 3, 4, 5, 6, tzinfo=datetime.timezone.utc), "ld": nt.DateTime(2026, 1, 2),
              "d": nt.Date(2026, 1, 2), "t": nt.Time(1, 2, 3), "tz": nt.Time(1, 2, 3, tzinfo=datetime.timezone.utc),
              "ds": [nt.Date(2020, 1, 1), nt.Date(2021, 2, 3)], "du": nt.Duration(months=1, seconds=2),
              "other": object.__new__(type("Odd", (), {"__str__": lambda self: 'odd "x"'}))}
    assert box._props_str(values) == dump_neo4j._props_str(values)


def test_unconvertible_temporal_fails_before_any_write(tmp_path: Path) -> None:
    """A temporal the driver cannot rebuild (an old-format datetime("2023-07-13")) stops the load before a driver is
    even opened: half a graph would make the next install skip Neo4j as already populated."""
    from startup.steps import seed as seed_mod

    gz = tmp_path / "neo4j.cypher.gz"
    with gzip.open(gz, "wt") as fh:
        fh.write('CREATE (n0:Sample:_ImportRef {`_exportId`: 0, `d`: datetime("2023-07-13")});')
    with patch("neo4j.GraphDatabase") as gdb, patch.object(seed_mod, "compose_port", return_value=7687):
        with pytest.raises(ValueError):
            seed_mod.load_neo4j_dump(gz, "pw", tmp_path, {})
    gdb.driver.assert_not_called()
