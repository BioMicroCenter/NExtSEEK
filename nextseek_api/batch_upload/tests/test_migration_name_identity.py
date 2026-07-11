"""Behavioral tests for the hashed samples.name_identity migration."""
from __future__ import annotations

import os
import time
from importlib import import_module

import pytest

from nextseek_api.batch_upload.identity import hash_identity

pymysql = pytest.importorskip("pymysql")

def _mariadb_conn_info() -> tuple[str | None, str | None, str | None, int | None]:
    host = os.environ.get("SPIKE_DB_HOST")
    user = os.environ.get("SPIKE_DB_USER")
    password = os.environ.get("SPIKE_DB_PASSWORD")
    port = os.environ.get("SPIKE_DB_PORT")
    if host and user and password:
        return host, user, password, int(port or "3306")

    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
        from django.conf import settings
    except Exception:
        return None, None, None, None

    db = settings.DATABASES.get("seek") or {}
    engine = db.get("ENGINE", "")
    if "mysql" not in engine:
        return None, None, None, None

    host = db.get("HOST") or "127.0.0.1"
    user = db.get("USER") or None
    password = db.get("PASSWORD") or None
    port = int(str(db.get("PORT") or "3306"))
    if not user or password is None:
        return None, None, None, None
    return host, user, password, port


_HOST, _USER, _PASS, _PORT = _mariadb_conn_info()
_HAS_DB_FIXTURE = all([_HOST, _USER, _PASS, _PORT])

pytestmark = pytest.mark.skipif(not _HAS_DB_FIXTURE, reason="MariaDB test fixture not configured")


_STRICT_MODE_FLAGS = ("STRICT_ALL_TABLES", "STRICT_TRANS_TABLES")


def _is_strict_sql_mode(conn) -> bool:
    """Return True when the current session enables a STRICT_* sql_mode flag."""
    with conn.cursor() as c:
        c.execute("SELECT @@SESSION.sql_mode")
        mode = c.fetchone()[0] or ""
    tokens = {t.strip().upper() for t in mode.split(",")}
    return any(flag in tokens for flag in _STRICT_MODE_FLAGS)

CREATE_TABLE_SQL = """
CREATE TABLE samples (
  id INT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255),
  sample_type_id INT,
  json_metadata TEXT,
  uuid VARCHAR(255),
  contributor_id INT DEFAULT NULL,
  policy_id INT DEFAULT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  first_letter VARCHAR(1),
  other_creators TEXT,
  originating_data_file_id INT DEFAULT NULL,
  deleted_contributor VARCHAR(255)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _load_migration_sql():
    mod = import_module("seek.migrations.0002_samples_name_identity")
    return mod.FORWARD_SQL, mod.REVERSE_SQL


def _skip_if_unreachable(exc: Exception) -> None:
    pytest.skip(f"MariaDB fixture unreachable in this environment: {exc}")


@pytest.fixture
def throwaway_db():
    db_name = f"test_namei_{int(time.time() * 1000)}"
    try:
        conn = pymysql.connect(host=_HOST, user=_USER, password=_PASS, port=_PORT, autocommit=True)
    except Exception as exc:
        _skip_if_unreachable(exc)
    try:
        with conn.cursor() as c:
            c.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            c.execute(f"USE `{db_name}`")
            c.execute(CREATE_TABLE_SQL)
        yield conn, db_name
    finally:
        try:
            with conn.cursor() as c:
                c.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        finally:
            conn.close()


class TestMigrationSqlModule:
    def test_exports_forward_and_reverse_sql(self):
        forward, reverse = _load_migration_sql()
        assert "ADD COLUMN name_identity" in forward
        assert "DROP COLUMN name_identity" in reverse


class TestMigrationApplies:
    def test_forward_sql_runs_without_error(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)

    def test_column_exists_after_forward(self, throwaway_db):
        conn, db_name = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
            c.execute(
                "SELECT COLUMN_NAME "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND COLUMN_NAME = 'name_identity'",
                (db_name,),
            )
            row = c.fetchone()
        assert row is not None

    def test_index_exists_after_forward(self, throwaway_db):
        conn, db_name = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
            c.execute(
                "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND INDEX_NAME = 'idx_samples_name_identity'",
                (db_name,),
            )
            rows = c.fetchall()
        assert rows

    def test_column_is_virtual(self, throwaway_db):
        conn, db_name = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
            c.execute(
                "SELECT EXTRA FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND COLUMN_NAME = 'name_identity'",
                (db_name,),
            )
            row = c.fetchone()
        assert "VIRTUAL" in (row[0] or "").upper()

    def test_column_is_char_64_ascii(self, throwaway_db):
        conn, db_name = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
            c.execute(
                "SELECT CHARACTER_MAXIMUM_LENGTH, CHARACTER_SET_NAME, COLLATION_NAME "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND COLUMN_NAME = 'name_identity'",
                (db_name,),
            )
            row = c.fetchone()
        assert row == (64, "ascii", "ascii_bin")


class TestMigrationBehavior:
    def _seed(self, conn, uuid, json_meta):
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO samples "
                "(title, sample_type_id, json_metadata, uuid, contributor_id, policy_id, first_letter) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("ignored", 1, json_meta, uuid, 1, 1, uuid[0].lower() if uuid else "x"),
            )

    def _fetch_name_identity(self, conn, uuid):
        with conn.cursor() as c:
            c.execute("SELECT name_identity FROM samples WHERE uuid = %s", (uuid,))
            row = c.fetchone()
        return row[0] if row else None

    def test_nhp_prefix_uses_name(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-1", '{"Name":"sampleA","File_PrimaryData":"ignored.csv"}')
        assert self._fetch_name_identity(conn, "NHP-260413NA-1") == hash_identity("sampleA")

    def test_d_prefix_uses_file_primary_data(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "D.SEQ-260413NA-1", '{"Name":"ignored","File_PrimaryData":"real.csv"}')
        assert self._fetch_name_identity(conn, "D.SEQ-260413NA-1") == hash_identity("real.csv")

    def test_a_prefix_uses_file_primary_data(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "A.GEX-260413NA-1", '{"Name":"ignored","File_PrimaryData":"real.csv"}')
        assert self._fetch_name_identity(conn, "A.GEX-260413NA-1") == hash_identity("real.csv")

    def test_typo_variant_matched_for_d_prefix(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "D.IMG-260413NA-1", '{"File_PrimartyData":"typo.csv"}')
        assert self._fetch_name_identity(conn, "D.IMG-260413NA-1") == hash_identity("typo.csv")

    def test_forward_variant_matched(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "D.SEQ-260413NA-2", '{"File_PrimaryData_Forward":"fwd.fa"}')
        assert self._fetch_name_identity(conn, "D.SEQ-260413NA-2") == hash_identity("fwd.fa")

    def test_reverse_variant_matched(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "D.SEQ-260413NA-3", '{"File_PrimaryData_Reverse":"rev.fa"}')
        assert self._fetch_name_identity(conn, "D.SEQ-260413NA-3") == hash_identity("rev.fa")

    def test_valid_object_missing_identity_keys_yields_null(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-9", '{"SomeOther":"val"}')
        assert self._fetch_name_identity(conn, "NHP-260413NA-9") is None

    def test_invalid_json_yields_null(self, throwaway_db):
        conn, _ = throwaway_db
        if _is_strict_sql_mode(conn):
            pytest.skip(
                "strict sql_mode rejects invalid JSON at INSERT (1406/3141); "
                "Python-layer validation covers this in the pipeline"
            )
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-10", '{"Name":"unterminated"')
        assert self._fetch_name_identity(conn, "NHP-260413NA-10") is None

    def test_json_array_yields_null(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-11", '["not","an","object"]')
        assert self._fetch_name_identity(conn, "NHP-260413NA-11") is None

    def test_oversized_identity_hashes_correctly(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        for offset, length in enumerate((300, 700, 1500, 2500), start=12):
            identity = "x" * length
            uuid = f"NHP-260413NA-{offset}"
            self._seed(conn, uuid, f'{{"Name":"{identity}"}}')
            assert self._fetch_name_identity(conn, uuid) == hash_identity(identity)

    def test_sql_python_hash_parity_ascii(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        rows = (
            ("NHP-260413NA-20", '{"Name":"Alpha-42"}', "Alpha-42"),
            ("NHP-260413NA-21", '{"Name":"  MixedCase-Value  "}', "  MixedCase-Value  "),
            (
                "D.SEQ-260413NA-22",
                '{"File_PrimaryData":"lane1_R1.fastq.gz;lane1_R2.fastq.gz"}',
                "lane1_R1.fastq.gz;lane1_R2.fastq.gz",
            ),
            ("A.GEX-260413NA-23", '{"File_PrimaryData":"GENE_PANEL.csv"}', "GENE_PANEL.csv"),
            ("NHP-260413NA-24", '{"Name":"   "}', "   "),
        )
        with conn.cursor() as c:
            c.execute(forward)
        for uuid, json_meta, expected_identity in rows:
            self._seed(conn, uuid, json_meta)
            assert self._fetch_name_identity(conn, uuid) == hash_identity(expected_identity)

    def test_empty_after_trim_yields_null(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-25", '{"Name":"   "}')
        assert hash_identity("   ") is None
        assert self._fetch_name_identity(conn, "NHP-260413NA-25") is None

    def test_case_insensitive_hash_lookup(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-26", '{"Name":"MixedCase-Value"}')
        with conn.cursor() as c:
            c.execute(
                "SELECT uuid FROM samples WHERE name_identity IN (%s)",
                (hash_identity("mixedcase-value"),),
            )
            rows = c.fetchall()
        assert any(r[0] == "NHP-260413NA-26" for r in rows)

    def test_write_to_generated_column_rejected(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        with pytest.raises(Exception) as exc_info:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO samples "
                    "(title, sample_type_id, json_metadata, uuid, contributor_id, policy_id, first_letter, name_identity) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    ("ignored", 1, '{"Name":"x"}', "NHP-260413NA-27", 1, 1, "n", "explicit"),
                )
        assert "generated column" in str(exc_info.value).lower() or "1906" in str(exc_info.value)


class TestMigrationExecutorAtomicity:
    """Clean-seed regression (ESCALATION 2026-07-10): applying seek.0002 through
    Django's migration executor on MySQL must not wedge.

    The raw-SQL tests above bypass the executor, so they cannot catch the
    TransactionManagementError raised when Migration.apply force-wraps an
    atomic RunPython in a transaction on a backend without transactional DDL
    (MySQL). This test drives the real path: executor -> Migration.apply ->
    RunPython -> schema_editor.execute(FORWARD_SQL).
    """

    def test_seek_0002_applies_via_migration_executor(self, throwaway_db, django_db_blocker):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
        import django

        django.setup()
        from django.db import connections
        from django.db.migrations.executor import MigrationExecutor
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.recorder import MigrationRecorder

        conn, db_name = throwaway_db
        alias = f"namei_exec_{db_name}"
        connections.databases[alias] = {
            "ENGINE": "django.db.backends.mysql",
            "NAME": db_name,
            "HOST": _HOST,
            "USER": _USER,
            "PASSWORD": _PASS,
            "PORT": _PORT,
            "ATOMIC_REQUESTS": False,
            "AUTOCOMMIT": True,
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "OPTIONS": {},
            "TIME_ZONE": None,
            "TEST": {},
        }
        try:
            with django_db_blocker.unblock():
                # The fixture already created the samples table; record 0001 as
                # applied BEFORE building the executor (its loader snapshots the
                # applied set at construction) so the plan is exactly [seek.0002].
                # auth/contenttypes are recorded too so the base project state
                # can resolve seek.0001's lazy auth.user reference (state-only;
                # their operations never execute against the throwaway DB).
                recorder = MigrationRecorder(connections[alias])
                for app, name in MigrationLoader(connections[alias]).graph.nodes:
                    if app in ("contenttypes", "auth"):
                        recorder.record_applied(app, name)
                recorder.record_applied("seek", "0001_initial")
                executor = MigrationExecutor(connections[alias])
                executor.migrate([("seek", "0002_samples_name_identity")])
        finally:
            connections[alias].close()
            connections.databases.pop(alias, None)

        with conn.cursor() as c:
            c.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND COLUMN_NAME = 'name_identity'",
                (db_name,),
            )
            assert c.fetchone()[0] == 1


class TestMigrationReversibility:
    def test_reverse_drops_index_and_column(self, throwaway_db):
        conn, db_name = throwaway_db
        forward, reverse = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
            c.execute(reverse)
            c.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND INDEX_NAME = 'idx_samples_name_identity'",
                (db_name,),
            )
            assert c.fetchone()[0] == 0
            c.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND COLUMN_NAME = 'name_identity'",
                (db_name,),
            )
            assert c.fetchone()[0] == 0

    def test_forward_after_reverse_still_works(self, throwaway_db):
        conn, _ = throwaway_db
        forward, reverse = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
            c.execute(reverse)
            c.execute(forward)
