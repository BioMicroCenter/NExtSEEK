"""Migration 0034: a question holding "−" or "β" is stored, and the conversion leaves the chat keys alone.

On dev and prod ``assistant_query_task.query`` was latin1 and such a question made
``POST /nextseek_api/cc-assistant/query/async/`` answer 500 (MySQL error 1366). The end-to-end
round trip below runs on the SQLite lane, which stores any text, so it pins the request path;
the conversion itself is checked here against a recording cursor and, on a real MySQL, in
test_migration_0034_heal_db.py.
"""
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.db.migrations.loader import MigrationLoader
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.migrations._chat_text_utf8mb4 import COLUMNS, heal_mysql
from nextseek_api.services.cc_assistant import CCAssistantViewSet

MIGRATION = ("nextseek_api", "0034_chat_text_utf8mb4")
QUESTION = "For TGF-β treated samples, draw a violin plot per group with the y axis from −1 to 1"
TITLE = "TGF-β violins, −1 to 1"


def test_0034_sits_on_0033_on_the_single_chain_of_the_app():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    leaves = loader.graph.leaf_nodes("nextseek_api")
    assert len(leaves) == 1, leaves
    assert MIGRATION in loader.graph.forwards_plan(leaves[0])
    migration = loader.get_migration(*MIGRATION)
    assert migration.dependencies == [("nextseek_api", "0033_clear_staff_non_superusers")]
    assert migration.operations[0].reversible


def test_only_free_text_columns_are_converted():
    """session_id is the parent of every chat foreign key and task_id is a key too: neither may move."""
    assert ("assistant_query_task", "query") in COLUMNS
    assert ("assistant_chat_session", "title") in COLUMNS
    assert not {column for _, column in COLUMNS} & {"session_id", "task_id", "chat_session_id", "chat_id"}


class _Cursor:
    """Answers the information_schema lookup from ``columns`` and records every other statement."""

    def __init__(self, columns):
        self.columns = columns
        self.statements = []
        self._row = None

    def execute(self, sql, params=None):
        if "information_schema.COLUMNS" in sql:
            self._row = self.columns.get(params)
        else:
            self.statements.append(sql)

    def fetchone(self):
        return self._row


def test_latin1_columns_are_modified_in_place_keeping_type_and_nullability():
    cursor = _Cursor({
        ("assistant_query_task", "query"): ("latin1", "longtext", "NO", None),
        ("assistant_chat_session", "title"): ("latin1", "varchar(200)", "YES", None),
    })
    assert heal_mysql(cursor) == ["assistant_query_task.query", "assistant_chat_session.title"]
    assert cursor.statements == [
        "ALTER TABLE `assistant_query_task` MODIFY `query` longtext "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL",
        "ALTER TABLE `assistant_chat_session` MODIFY `title` varchar(200) "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL",
    ]


def test_utf8mb4_columns_are_left_alone_whatever_their_collation():
    cursor = _Cursor({
        ("assistant_query_task", "query"): ("utf8mb4", "longtext", "NO", None),
        ("assistant_chat_session", "title"): ("utf8mb4", "varchar(200)", "YES", None),
    })
    assert heal_mysql(cursor) == []
    assert cursor.statements == []


def test_a_missing_column_or_a_database_default_stops_the_migration():
    with pytest.raises(RuntimeError, match="not found"):
        heal_mysql(_Cursor({}))
    with pytest.raises(RuntimeError, match="database default"):
        heal_mysql(_Cursor({("assistant_query_task", "query"): ("latin1", "varchar(20)", "NO", "x")}))


@pytest.mark.django_db
def test_a_question_with_a_unicode_minus_and_beta_is_stored_and_read_back_unchanged():
    user = User.objects.create_user("asker", password="p")
    request = APIRequestFactory().post(
        "/nextseek_api/cc-assistant/query/async/", {"query": QUESTION, "mode": "standard"}, format="json",
    )
    force_authenticate(request, user=user)
    with patch("nextseek_api.services.cc_assistant.resolve_seek_auth", return_value=(("asker", "p"), {})), \
         patch("nextseek_api.services.cc_assistant.plain_scope", return_value=None), \
         patch("nextseek_api.services.cc_assistant.caller_block", return_value={}), \
         patch("nextseek_api.services.cc_assistant.cc_turn.start_task", MagicMock()) as start:
        response = CCAssistantViewSet.as_view({"post": "query_async"})(request)

    assert response.status_code == 202, response.data
    start.assert_called_once()
    task = QueryTask.objects.get(task_id=response.data["task_id"])
    assert task.query == QUESTION

    session = ChatSession.objects.get(session_id=response.data["session_id"])
    session.title = TITLE
    session.save(update_fields=["title"])
    session.refresh_from_db()
    assert session.title == TITLE
