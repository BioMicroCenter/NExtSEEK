"""Behavioral tests for migration 0002_samples_name_identity."""
from __future__ import annotations

import os
import time
from importlib import import_module

import pytest
from django.core.management import call_command

pymysql = pytest.importorskip("pymysql")


_HOST = os.environ.get("SPIKE_DB_HOST")
_USER = os.environ.get("SPIKE_DB_USER")
_PASS = os.environ.get("SPIKE_DB_PASSWORD")
_PORT = int(os.environ.get("SPIKE_DB_PORT", "3306"))
_HAS_DB_FIXTURE = all([_HOST, _USER, _PASS])

pytestmark = pytest.mark.skipif(
    not _HAS_DB_FIXTURE,
    reason="MariaDB test fixture env vars not set (SPIKE_DB_HOST/USER/PASSWORD)",
)

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


@pytest.fixture
def throwaway_db():
    db_name = f"test_namei_{int(time.time() * 1000)}"
    conn = pymysql.connect(host=_HOST, user=_USER, password=_PASS, port=_PORT, autocommit=True)
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
                "SELECT COLUMN_NAME, COLLATION_NAME "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'samples' AND COLUMN_NAME = 'name_identity'",
                (db_name,),
            )
            row = c.fetchone()
        assert row is not None
        assert row[1] == "utf8mb4_unicode_ci"

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
        assert self._fetch_name_identity(conn, "NHP-260413NA-1") == "sampleA"

    def test_d_prefix_uses_file_primary_data(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "D.SEQ-260413NA-1", '{"Name":"ignored","File_PrimaryData":"real.csv"}')
        assert self._fetch_name_identity(conn, "D.SEQ-260413NA-1") == "real.csv"

    def test_valid_object_missing_identity_keys_yields_null(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-9", '{"SomeOther":"val"}')
        assert self._fetch_name_identity(conn, "NHP-260413NA-9") is None

    def test_invalid_json_yields_null(self, throwaway_db):
        conn, _ = throwaway_db
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

    def test_oversized_identity_truncates_to_255(self, throwaway_db):
        conn, _ = throwaway_db
        forward, _ = _load_migration_sql()
        long_name = "x" * 300
        with conn.cursor() as c:
            c.execute(forward)
        self._seed(conn, "NHP-260413NA-12", f'{{"Name":"{long_name}"}}')
        value = self._fetch_name_identity(conn, "NHP-260413NA-12")
        assert value == "x" * 255


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


class TestDjangoMigrationRunner:
    def test_django_migrate_seek_forward_and_reverse(self):
        call_command("migrate", "seek", verbosity=0)
        call_command("migrate", "seek", "0001", verbosity=0)
