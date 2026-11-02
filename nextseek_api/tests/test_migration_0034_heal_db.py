"""Migration 0034's heal on a real MySQL: the latin1 chat tables of a box seeded before the utf8mb4 dump.

Builds the two tables the way dev and prod hold them (latin1 throughout, the query task's
``session_id`` a foreign key to the chat's), converts them, and checks that a question holding
"−" and "β" now goes in and comes back unchanged, that the old latin1 text survives, and that
the key columns and their foreign key are untouched. A utf8mb4 schema (fresh installs) must see
no DDL. Skipped unless a MySQL fixture is configured; the env-gated raw DB fixture is the idiom
of test_migration_0007_heal_db.py.
"""
from __future__ import annotations

import os
import time

import pytest

pymysql = pytest.importorskip("pymysql")


def _conn_info():
    host = os.environ.get("SPIKE_DB_HOST")
    user = os.environ.get("SPIKE_DB_USER")
    password = os.environ.get("SPIKE_DB_PASSWORD")
    port = os.environ.get("SPIKE_DB_PORT")
    if host and user and password:
        return host, user, password, int(port or "3306")
    return None, None, None, None


_HOST, _USER, _PASS, _PORT = _conn_info()

pytestmark = pytest.mark.skipif(
    not all([_HOST, _USER, _PASS, _PORT]), reason="MySQL test fixture not configured"
)

QUESTION = "For TGF-β treated samples, draw a violin plot per group with the y axis from −1 to 1"
SESSION_ID = "ab" * 16
TASK_ID = "cd" * 16


def _schema(charset: str, collation: str) -> list[str]:
    table = f"ENGINE=InnoDB DEFAULT CHARSET={charset} COLLATE={collation}"
    return [
        f"""CREATE TABLE `assistant_chat_session` (
          `session_id` char(32) NOT NULL PRIMARY KEY,
          `title` varchar(200) NULL
        ) {table}""",
        f"""CREATE TABLE `assistant_query_task` (
          `id` bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
          `task_id` char(32) NOT NULL UNIQUE,
          `query` longtext NOT NULL,
          `status` varchar(20) NOT NULL,
          `session_id` char(32) NOT NULL,
          CONSTRAINT `assistant_query_task_session_id_fk` FOREIGN KEY (`session_id`)
            REFERENCES `assistant_chat_session` (`session_id`)
        ) {table}""",
        # A child of the chat as 0007/0010/0025 build them: its key column matches the parent's charset.
        f"""CREATE TABLE `assistant_turn_ledger` (
          `id` bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
          `session_id` char(32) CHARACTER SET {charset} COLLATE {collation} NOT NULL,
          CONSTRAINT `assistant_turn_ledger_session_id_fk` FOREIGN KEY (`session_id`)
            REFERENCES `assistant_chat_session` (`session_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    ]


def _heal(cursor):
    from nextseek_api.migrations._chat_text_utf8mb4 import heal_mysql

    return heal_mysql(cursor)


@pytest.fixture
def throwaway_db():
    db_name = f"test_chattext_{int(time.time() * 1000)}"
    try:
        conn = pymysql.connect(
            host=_HOST, user=_USER, password=_PASS, port=_PORT, autocommit=True, charset="utf8mb4"
        )
    except Exception as exc:
        pytest.skip(f"MySQL fixture unreachable in this environment: {exc}")
    try:
        with conn.cursor() as c:
            c.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            c.execute(f"USE `{db_name}`")
        yield conn, db_name
    finally:
        try:
            with conn.cursor() as c:
                c.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        finally:
            conn.close()


def _columns(conn, db_name):
    with conn.cursor() as c:
        c.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND CHARACTER_SET_NAME IS NOT NULL",
            (db_name,),
        )
        return {(t, col): (cs, coll) for t, col, cs, coll in c.fetchall()}


def _foreign_keys(conn, db_name):
    with conn.cursor() as c:
        c.execute(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY CONSTRAINT_NAME",
            (db_name,),
        )
        return [row[0] for row in c.fetchall()]


def _insert_question(cursor, question):
    cursor.execute(
        "INSERT INTO assistant_query_task (task_id, query, status, session_id) VALUES (%s, %s, 'running', %s)",
        (TASK_ID, question, SESSION_ID),
    )


class TestSeededLatin1Box:
    """The dev/prod shape: both chat tables latin1, so their text columns are latin1 too."""

    def _seed(self, conn):
        with conn.cursor() as c:
            for sql in _schema("latin1", "latin1_swedish_ci"):
                c.execute(sql)
            c.execute(
                "INSERT INTO assistant_chat_session (session_id, title) VALUES (%s, 'café notes')", (SESSION_ID,)
            )

    def test_the_question_fails_before_the_heal(self, throwaway_db):
        conn, _ = throwaway_db
        self._seed(conn)
        with conn.cursor() as c, pytest.raises((pymysql.err.DataError, pymysql.err.OperationalError), match="1366|Incorrect string value"):
            _insert_question(c, QUESTION)

    def test_the_heal_converts_the_text_columns_and_the_question_round_trips(self, throwaway_db):
        conn, db_name = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            assert _heal(c) == ["assistant_query_task.query", "assistant_chat_session.title"]
            _insert_question(c, QUESTION)
            c.execute("UPDATE assistant_chat_session SET title = %s WHERE session_id = %s", ("TGF-β −1", SESSION_ID))
            c.execute("SELECT query FROM assistant_query_task WHERE task_id = %s", (TASK_ID,))
            assert c.fetchone()[0] == QUESTION
            c.execute("SELECT title FROM assistant_chat_session WHERE session_id = %s", (SESSION_ID,))
            assert c.fetchone()[0] == "TGF-β −1"

        columns = _columns(conn, db_name)
        assert columns[("assistant_query_task", "query")] == ("utf8mb4", "utf8mb4_unicode_ci")
        assert columns[("assistant_chat_session", "title")] == ("utf8mb4", "utf8mb4_unicode_ci")
        for key in [("assistant_chat_session", "session_id"), ("assistant_query_task", "session_id"),
                    ("assistant_query_task", "task_id"), ("assistant_turn_ledger", "session_id")]:
            assert columns[key] == ("latin1", "latin1_swedish_ci"), key
        assert _foreign_keys(conn, db_name) == [
            "assistant_query_task_session_id_fk", "assistant_turn_ledger_session_id_fk",
        ]

    def test_existing_latin1_text_survives_the_conversion(self, throwaway_db):
        conn, _ = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            _insert_question(c, "naïve café question")
            _heal(c)
            c.execute("SELECT query FROM assistant_query_task WHERE task_id = %s", (TASK_ID,))
            assert c.fetchone()[0] == "naïve café question"
            c.execute("SELECT title FROM assistant_chat_session WHERE session_id = %s", (SESSION_ID,))
            assert c.fetchone()[0] == "café notes"

    def test_a_second_run_does_nothing(self, throwaway_db):
        conn, _ = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            _heal(c)
            assert _heal(c) == []


class TestFreshUtf8mb4Install:
    """The current seed dump and test databases: already utf8mb4, so no DDL at all."""

    def test_the_heal_is_a_no_op(self, throwaway_db):
        conn, db_name = throwaway_db
        with conn.cursor() as c:
            for sql in _schema("utf8mb4", "utf8mb4_unicode_ci"):
                c.execute(sql)
            c.execute("INSERT INTO assistant_chat_session (session_id) VALUES (%s)", (SESSION_ID,))
            before = _columns(conn, db_name)
            assert _heal(c) == []
            _insert_question(c, QUESTION)
            c.execute("SELECT query FROM assistant_query_task WHERE task_id = %s", (TASK_ID,))
            assert c.fetchone()[0] == QUESTION
        assert _columns(conn, db_name) == before
