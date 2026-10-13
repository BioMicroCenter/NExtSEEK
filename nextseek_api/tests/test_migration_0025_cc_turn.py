"""Migration 0025: the Container-CC turn pass table and the nested turn's link to it."""
from importlib import import_module

import pytest
from django.db import migrations as dj_migrations
from django.db.models.deletion import CASCADE, SET_NULL

from nextseek_api.migrations import _cc_turn_heal as heal

MIGRATION = "nextseek_api.migrations.0025_cc_turn"


def _migration():
    return import_module(MIGRATION).Migration


def test_0025_creates_the_table_through_the_heal_then_adds_the_link():
    migration = _migration()
    assert migration.atomic is False
    first, second = migration.operations
    assert isinstance(first, dj_migrations.SeparateDatabaseAndState)
    assert [type(op) for op in first.state_operations] == [dj_migrations.CreateModel]
    (database_op,) = first.database_operations
    assert isinstance(database_op, dj_migrations.RunPython)
    assert database_op.reverse_code is not dj_migrations.RunPython.noop
    assert isinstance(second, dj_migrations.AddField)
    assert (second.model_name, second.name) == ("querytask", "parent_cc_turn")
    assert second.field.null is True
    assert second.field.remote_field.on_delete is SET_NULL
    assert second.field.remote_field.related_name == "children"
    assert second.field.remote_field.model == "nextseek_api.ccturn"


def test_0025_state_keeps_the_declared_keys():
    create = _migration().operations[0].state_operations[0]
    fields = dict(create.fields)
    assert create.options["db_table"] == "assistant_cc_turn"
    assert fields["chat"].remote_field.model == "nextseek_api.chatsession"
    assert fields["chat"].db_constraint is True
    assert fields["task"].remote_field.model == "nextseek_api.querytask"
    assert fields["task"].remote_field.related_name == "cc_turn"
    assert fields["task"].remote_field.on_delete is CASCADE
    assert fields["pass_hash"].unique is True
    assert [index.name for index in create.options["indexes"]] == ["assistant_cc_turn_live"]


class _Cursor:
    """Answers the heal's introspection queries in order and records every statement."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.answers.pop(0)


def test_the_heal_creates_the_table_with_the_parents_charset_and_key_types():
    cursor = _Cursor([(0,), ("latin1", "latin1_swedish_ci"), ("bigint",), ("int",)])
    assert heal.heal_mysql(cursor, user_table="auth_user") == ["create_table"]
    create_sql = cursor.statements[-1][0]
    assert create_sql.lstrip().startswith("CREATE TABLE `assistant_cc_turn`")
    assert "`chat_id` char(32) CHARACTER SET latin1 COLLATE latin1_swedish_ci NOT NULL" in create_sql
    assert "`task_id` bigint NOT NULL" in create_sql
    assert "`user_id` int NOT NULL" in create_sql
    assert "REFERENCES `assistant_chat_session` (`session_id`)" in create_sql
    assert "REFERENCES `assistant_query_task` (`id`)" in create_sql
    assert "REFERENCES `auth_user` (`id`)" in create_sql
    assert "KEY `assistant_cc_turn_live` (`revoked_at`, `expires_at`)" in create_sql


def test_the_heal_leaves_an_existing_table_alone():
    cursor = _Cursor([(1,)])
    assert heal.heal_mysql(cursor, user_table="auth_user") == []
    assert len(cursor.statements) == 1  # the existence check, nothing else


@pytest.mark.parametrize("answers", [
    [(0,), None],                                                        # no parent chat column
    [(0,), ("latin1; x", "latin1_swedish_ci"), ("bigint",), ("int",)],   # a charset that is not a name
    [(0,), ("latin1", "latin1_swedish_ci"), ("varchar(10)",), ("int",)],  # a task key that is not an integer
    [(0,), ("latin1", "latin1_swedish_ci"), ("bigint",), None],          # no user key column
])
def test_the_heal_refuses_to_guess(answers):
    with pytest.raises(RuntimeError):
        heal.heal_mysql(_Cursor(answers), user_table="auth_user")


@pytest.mark.django_db
def test_the_orm_round_trip_and_the_delete_rules():
    from django.contrib.auth import get_user_model

    from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask

    user = get_user_model().objects.create_user("mig-user", password="x")
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
    turn = CCTurn.objects.create(task=task, user=user, chat=chat, pass_hash="a" * 64)
    child = QueryTask.objects.create(session=chat, user=user, query="nested", parent_cc_turn=turn)

    assert task.cc_turn == turn
    assert list(turn.children.all()) == [child]
    assert (turn.plans, turn.strikes, turn.vocabulary, turn.ops_in_flight) == ({}, [], None, 0)

    turn.delete()
    child.refresh_from_db()
    assert child.parent_cc_turn is None  # SET_NULL: the nested task outlives the pass row

    turn = CCTurn.objects.create(task=task, user=user, chat=chat, pass_hash="b" * 64)
    task.delete()
    assert not CCTurn.objects.filter(pk=turn.pk).exists()  # CASCADE from the task


@pytest.mark.django_db
def test_the_heal_create_table_columns_match_the_0025_state():
    import re

    from django.apps import apps

    model = heal._frozen_0025_model(apps)
    sql_columns = set(re.findall(r"^  `(\w+)` ", heal._CREATE_TABLE_SQL, re.M))
    assert {f.column for f in model._meta.local_fields} == sql_columns
