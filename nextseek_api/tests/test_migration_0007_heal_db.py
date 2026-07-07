"""Behavioral tests for the assistant_cc_transcript charset heal (Bug C).

Exercises the shared idempotent heal against real MySQL/MariaDB throwaway
schemas in each deployment state the fix must converge:

- S1 fresh seed: latin1 parent (from startup/seed/dmac.sql.gz), no child —
  every seeded greenfield; the original 0007 failed here with errno 3780.
- S2 wedged: latin1 parent + utf8mb4 FK-less child (what the failed CreateModel
  left behind), possibly with data.
- Live-dev shape: same as S2 at the schema level (0007 fake-recorded there;
  Django ledger state is invisible to the heal itself — 0008 is the vehicle).
- Native path: utf8mb4 parent (test_dmac / --no-seed installs) — must stay
  utf8mb4, no forced latin1.

Idiom (env-gated raw DB fixture) copied from
nextseek_api/batch_upload/tests/test_migration_name_identity.py.
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
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
        from django.conf import settings
    except Exception:
        return None, None, None, None
    db = settings.DATABASES.get("seek") or {}
    if "mysql" not in db.get("ENGINE", ""):
        return None, None, None, None
    host = db.get("HOST") or "127.0.0.1"
    user = db.get("USER") or None
    password = db.get("PASSWORD") or None
    port = int(str(db.get("PORT") or "3306"))
    if not user or password is None:
        return None, None, None, None
    return host, user, password, port


_HOST, _USER, _PASS, _PORT = _conn_info()
_HAS_DB_FIXTURE = all([_HOST, _USER, _PASS, _PORT])

pytestmark = pytest.mark.skipif(
    not _HAS_DB_FIXTURE, reason="MySQL test fixture not configured"
)

LATIN1_PARENT_SQL = """
CREATE TABLE `assistant_chat_session` (
  `session_id` char(32) NOT NULL PRIMARY KEY,
  `title` varchar(200) NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci
"""

UTF8MB4_PARENT_SQL = """
CREATE TABLE `assistant_chat_session` (
  `session_id` char(32) NOT NULL PRIMARY KEY,
  `title` varchar(200) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# The half-created table the failed original 0007 left behind on greenfields
# (verified live + on the Step 7d laptop): all columns, PK, the deferred
# unique triple — but NO foreign key and NO dedicated chat_session_id index.
WEDGED_CHILD_SQL = """
CREATE TABLE `assistant_cc_transcript` (
  `id` bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `cc_session_id` varchar(128) NOT NULL,
  `turn_id` varchar(128) NOT NULL,
  `blob` longblob NOT NULL,
  `uncompressed_size` bigint NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `chat_session_id` char(32) NOT NULL,
  UNIQUE KEY `assistant_cc_transcript_chat_session_id_cc_sessi_bdda2d20_uniq`
    (`chat_session_id`,`cc_session_id`,`turn_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _heal(cursor):
    from nextseek_api.migrations._cc_transcript_heal import heal_mysql

    return heal_mysql(cursor)


@pytest.fixture
def throwaway_db():
    db_name = f"test_ccheal_{int(time.time() * 1000)}"
    try:
        conn = pymysql.connect(
            host=_HOST, user=_USER, password=_PASS, port=_PORT, autocommit=True
        )
    except Exception as exc:
        pytest.skip(f"MySQL fixture unreachable in this environment: {exc}")
    try:
        with conn.cursor() as c:
            c.execute(
                f"CREATE DATABASE `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            c.execute(f"USE `{db_name}`")
        yield conn, db_name
    finally:
        try:
            with conn.cursor() as c:
                c.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        finally:
            conn.close()


def _charsets(conn, db_name):
    with conn.cursor() as c:
        c.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND ("
            "(TABLE_NAME='assistant_chat_session' AND COLUMN_NAME='session_id') OR "
            "(TABLE_NAME='assistant_cc_transcript' AND COLUMN_NAME='chat_session_id'))",
            (db_name,),
        )
        return {row[0]: (row[2], row[3]) for row in c.fetchall()}


def _fk_rows(conn, db_name):
    with conn.cursor() as c:
        c.execute(
            "SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assistant_cc_transcript' "
            "AND REFERENCED_TABLE_NAME IS NOT NULL",
            (db_name,),
        )
        return c.fetchall()


class TestS1FreshSeed:
    """latin1 parent, no child — the seeded-greenfield state that wedged."""

    def test_heal_creates_charset_matched_table_and_fk(self, throwaway_db):
        conn, db_name = throwaway_db
        with conn.cursor() as c:
            c.execute(LATIN1_PARENT_SQL)
            actions = _heal(c)
        assert actions == ["create_table", "add_fk"]
        cs = _charsets(conn, db_name)
        assert cs["assistant_cc_transcript"] == cs["assistant_chat_session"]
        assert cs["assistant_cc_transcript"] == ("latin1", "latin1_swedish_ci")
        fks = _fk_rows(conn, db_name)
        assert len(fks) == 1
        assert fks[0][1:] == ("assistant_chat_session", "session_id")

    def test_unique_triple_present_with_django_name(self, throwaway_db):
        conn, db_name = throwaway_db
        with conn.cursor() as c:
            c.execute(LATIN1_PARENT_SQL)
            _heal(c)
            c.execute(
                "SELECT COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assistant_cc_transcript' "
                "AND INDEX_NAME = "
                "'assistant_cc_transcript_chat_session_id_cc_sessi_bdda2d20_uniq' "
                "ORDER BY SEQ_IN_INDEX",
                (db_name,),
            )
            cols = [r[0] for r in c.fetchall()]
        assert cols == ["chat_session_id", "cc_session_id", "turn_id"]


class TestS2Wedged:
    """latin1 parent + utf8mb4 FK-less child, rows present (laptop/live shape)."""

    def _seed(self, conn):
        with conn.cursor() as c:
            c.execute(LATIN1_PARENT_SQL)
            c.execute(WEDGED_CHILD_SQL)
            c.execute(
                "INSERT INTO assistant_chat_session (session_id, title) "
                "VALUES (%s, 'chat one')",
                ("ab" * 16,),
            )
            c.execute(
                "INSERT INTO assistant_cc_transcript "
                "(`cc_session_id`, `turn_id`, `blob`, `uncompressed_size`, `created_at`, `chat_session_id`) "
                "VALUES ('cc-1', 'turn-1', %s, 5, NOW(6), %s)",
                (b"hello", "ab" * 16),
            )

    def test_heal_aligns_charset_adds_fk_keeps_data(self, throwaway_db):
        conn, db_name = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            actions = _heal(c)
        assert actions == ["align_charset", "add_fk"]
        cs = _charsets(conn, db_name)
        assert cs["assistant_cc_transcript"] == ("latin1", "latin1_swedish_ci")
        assert len(_fk_rows(conn, db_name)) == 1
        with conn.cursor() as c:
            c.execute(
                "SELECT `chat_session_id`, `blob` FROM assistant_cc_transcript "
                "WHERE cc_session_id = 'cc-1'"
            )
            row = c.fetchone()
        assert row[0] == "ab" * 16
        assert bytes(row[1]) == b"hello"

    def test_fk_actually_enforced_after_heal(self, throwaway_db):
        conn, _ = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            _heal(c)
        with pytest.raises(Exception) as exc_info:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO assistant_cc_transcript "
                    "(`cc_session_id`, `turn_id`, `blob`, `uncompressed_size`, `created_at`, `chat_session_id`) "
                    "VALUES ('cc-2', 'turn-1', %s, 3, NOW(6), %s)",
                    (b"bad", "ff" * 16),
                )
        assert "1452" in str(exc_info.value) or "foreign key" in str(exc_info.value).lower()

    def test_heal_is_idempotent(self, throwaway_db):
        conn, _ = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            first = _heal(c)
            second = _heal(c)
        assert first == ["align_charset", "add_fk"]
        assert second == []

    def test_orphaned_rows_fail_loudly_without_adding_fk(self, throwaway_db):
        conn, db_name = throwaway_db
        self._seed(conn)
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO assistant_cc_transcript "
                "(`cc_session_id`, `turn_id`, `blob`, `uncompressed_size`, `created_at`, `chat_session_id`) "
                "VALUES ('cc-orphan', 'turn-1', %s, 3, NOW(6), %s)",
                (b"orp", "ee" * 16),
            )
        with pytest.raises(RuntimeError, match="orphan"):
            with conn.cursor() as c:
                _heal(c)
        assert _fk_rows(conn, db_name) == ()


class TestNativeUtf8mb4Path:
    """utf8mb4 parent (test_dmac / --no-seed): heal must NOT force latin1."""

    def test_created_table_matches_utf8mb4_parent(self, throwaway_db):
        conn, db_name = throwaway_db
        with conn.cursor() as c:
            c.execute(UTF8MB4_PARENT_SQL)
            actions = _heal(c)
        assert actions == ["create_table", "add_fk"]
        cs = _charsets(conn, db_name)
        assert cs["assistant_cc_transcript"] == ("utf8mb4", "utf8mb4_unicode_ci")
        assert len(_fk_rows(conn, db_name)) == 1


class TestMissingParent:
    def test_heal_refuses_without_parent_table(self, throwaway_db):
        conn, _ = throwaway_db
        with pytest.raises(RuntimeError, match="assistant_chat_session"):
            with conn.cursor() as c:
                _heal(c)


@pytest.mark.django_db
class TestFullMigrationChain:
    """The rewritten 0007 + 0008 run inside Django's real migrate machinery
    when pytest-django builds test_dmac — assert the end state it produces."""

    def test_fk_present_and_charsets_matched(self):
        from django.db import connection

        with connection.cursor() as c:
            c.execute(
                "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'assistant_cc_transcript' "
                "AND REFERENCED_TABLE_NAME = 'assistant_chat_session'"
            )
            fks = c.fetchall()
            c.execute(
                "SELECT TABLE_NAME, CHARACTER_SET_NAME, COLLATION_NAME "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND ("
                "(TABLE_NAME='assistant_chat_session' AND COLUMN_NAME='session_id') OR "
                "(TABLE_NAME='assistant_cc_transcript' AND COLUMN_NAME='chat_session_id'))"
            )
            cs = {row[0]: (row[1], row[2]) for row in c.fetchall()}
        assert len(fks) == 1
        assert cs["assistant_cc_transcript"] == cs["assistant_chat_session"]

    def test_orm_round_trip(self):
        from django.contrib.auth.models import User

        from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSession

        user = User.objects.create_user("healtest")
        cs = ChatSession.objects.create(user=user)
        CCSessionTranscript.objects.create(
            chat_session=cs, cc_session_id="cc-x", turn_id="t-1",
            blob=b"payload", uncompressed_size=7,
        )
        assert cs.cc_transcripts.count() == 1
