"""Migration 0036's heal on a real MySQL: the latin1 context tables dev and prod hold.

Checks what the fake cursor in test_migration_0036_definitions_guide_columns.py cannot: the information_schema
lookup is scoped to ``DATABASE()`` (a same-named table in another schema does not count), the added columns are
utf8mb4 on a latin1 table so "−", "β" and a four-byte character round-trip, and a second run issues no DDL.
Skipped unless a MySQL fixture is configured; the env-gated raw DB fixture is the idiom of
test_migration_0034_heal_db.py.
"""
from __future__ import annotations

import time

import pytest

from nextseek_api.tests.test_migration_0034_heal_db import _HOST, _PASS, _PORT, _USER, pymysql

pytestmark = pytest.mark.skipif(
    not all([_HOST, _USER, _PASS, _PORT]), reason="MySQL test fixture not configured"
)

LATIN1 = "ENGINE=InnoDB DEFAULT CHARSET=latin1"
TEXT = "Use this when TGF-β treated, −1 to 1 \U0001F9EB"
SIMILAR = '[{"code": "TIS", "differs": "a | piece of tissue, β"}]'


def _heal(cursor):
    from nextseek_api.migrations._definitions_guide_columns import heal_mysql

    return heal_mysql(cursor)


@pytest.fixture
def dbs():
    """A throwaway schema the connection uses, and a sibling schema it does not."""
    stamp = int(time.time() * 1000)
    names = (f"test_guide_{stamp}", f"test_guide_other_{stamp}")
    try:
        conn = pymysql.connect(host=_HOST, user=_USER, password=_PASS, port=_PORT, autocommit=True,
                               charset="utf8mb4")
    except Exception as exc:
        pytest.skip(f"MySQL fixture unreachable in this environment: {exc}")
    try:
        with conn.cursor() as c:
            for name in names:
                c.execute(f"CREATE DATABASE `{name}` CHARACTER SET latin1")
            c.execute(f"USE `{names[0]}`")
        yield conn, names
    finally:
        try:
            with conn.cursor() as c:
                for name in names:
                    c.execute(f"DROP DATABASE IF EXISTS `{name}`")
        finally:
            conn.close()


def _guide_columns(c, db):
    c.execute("SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME, DATA_TYPE FROM "
              "information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND COLUMN_NAME IN ('when_to_use', 'similar_types')",
              (db,))
    return {(t, col): tuple(rest) for t, col, *rest in c.fetchall()}


def test_latin1_tables_get_utf8mb4_text_that_round_trips_and_a_second_run_does_nothing(dbs):
    conn, (db, _) = dbs
    with conn.cursor() as c:
        c.execute(f"CREATE TABLE sample_types_context (id INT AUTO_INCREMENT PRIMARY KEY, sample_type VARCHAR(255), "
                  f"Tags TEXT) {LATIN1}")
        c.execute(f"CREATE TABLE assay_context (id INT AUTO_INCREMENT PRIMARY KEY, assay_name VARCHAR(255), "
                  f"Description TEXT) {LATIN1}")
        c.execute("INSERT INTO sample_types_context (sample_type, Tags) VALUES ('BAC', 'café')")
        c.execute("INSERT INTO assay_context (assay_name) VALUES ('DNA Extraction')")
        assert _heal(c) == ["sample_types_context.when_to_use", "sample_types_context.similar_types",
                            "assay_context.when_to_use", "assay_context.similar_types"]
        assert set(_guide_columns(c, db).values()) == {("utf8mb4", "utf8mb4_unicode_ci", "text")}
        assert len(_guide_columns(c, db)) == 4
        for table in ("sample_types_context", "assay_context"):
            c.execute(f"UPDATE {table} SET when_to_use = %s, similar_types = %s", (TEXT, SIMILAR))
            c.execute(f"SELECT when_to_use, similar_types FROM {table}")
            assert c.fetchone() == (TEXT, SIMILAR)
        c.execute("SELECT Tags FROM sample_types_context")
        assert c.fetchone()[0] == "café"
        assert _heal(c) == []


def test_a_table_in_another_schema_does_not_count_and_an_absent_one_is_skipped(dbs):
    conn, (db, other) = dbs
    with conn.cursor() as c:
        c.execute(f"CREATE TABLE `{other}`.assay_context (id INT PRIMARY KEY, assay_name VARCHAR(255)) {LATIN1}")
        c.execute(f"CREATE TABLE sample_types_context (id INT PRIMARY KEY, sample_type VARCHAR(255), "
                  f"when_to_use TEXT) {LATIN1}")
        assert _heal(c) == ["sample_types_context.similar_types"]
        assert _guide_columns(c, other) == {}
        c.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = "
                  "'assay_context'", (db,))
        assert c.fetchone()[0] == 0
