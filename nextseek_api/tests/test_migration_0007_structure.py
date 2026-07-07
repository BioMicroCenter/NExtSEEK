"""Structure contract for the 0007 charset-heal rewrite (Bug C, 2026-07-07).

Migration 0007 originally shipped a plain CreateModel whose deferred
``ADD FOREIGN KEY`` fails with errno 3780 on every seeded greenfield (the seed
dump's ``assistant_chat_session`` is latin1; fresh DBs default utf8mb4), leaving
the table half-created and the ledger wedged. The fix rewrites 0007 as
SeparateDatabaseAndState (state = the original CreateModel verbatim; database =
an idempotent heal) and adds 0008 running the SAME heal — 0008 is the only
vehicle that reaches deployments where 0007 is already recorded (the live dev
DB was fake-recorded on 2026-07-01 with the FK absent).
"""
from __future__ import annotations

from importlib import import_module

from django.db import migrations as dj_migrations

from nextseek_api.migrations import _cc_transcript_heal as heal_mod

MIG_0007 = import_module("nextseek_api.migrations.0007_ccsessiontranscript")
MIG_0008 = import_module("nextseek_api.migrations.0008_heal_cc_transcript_fk")


class Test0007Rewrite:
    def test_single_separate_database_and_state_operation(self):
        ops = MIG_0007.Migration.operations
        assert len(ops) == 1
        assert isinstance(ops[0], dj_migrations.SeparateDatabaseAndState)

    def test_state_operation_is_verbatim_create_model(self):
        """Model-state parity: Django must see the exact original CreateModel
        (gate: `makemigrations nextseek_api --check --dry-run` stays clean)."""
        state_ops = MIG_0007.Migration.operations[0].state_operations
        assert len(state_ops) == 1
        cm = state_ops[0]
        assert isinstance(cm, dj_migrations.CreateModel)
        assert cm.name == "CCSessionTranscript"
        assert cm.options["db_table"] == "assistant_cc_transcript"
        assert cm.options["unique_together"] == {
            ("chat_session", "cc_session_id", "turn_id")
        }
        assert [f[0] for f in cm.fields] == [
            "id",
            "cc_session_id",
            "turn_id",
            "blob",
            "uncompressed_size",
            "created_at",
            "chat_session",
        ]

    def test_database_operation_is_shared_heal(self):
        db_ops = MIG_0007.Migration.operations[0].database_operations
        assert len(db_ops) == 1
        assert isinstance(db_ops[0], dj_migrations.RunPython)
        assert db_ops[0].code is heal_mod.heal

    def test_nonatomic_like_house_precedent(self):
        # MySQL DDL auto-commits; 0005_ensure set the precedent.
        assert MIG_0007.Migration.atomic is False


class Test0008Companion:
    def test_depends_only_on_0007(self):
        assert MIG_0008.Migration.dependencies == [
            ("nextseek_api", "0007_ccsessiontranscript")
        ]

    def test_runs_the_same_heal_function(self):
        ops = MIG_0008.Migration.operations
        assert len(ops) == 1
        assert isinstance(ops[0], dj_migrations.RunPython)
        assert ops[0].code is heal_mod.heal

    def test_no_state_operations(self):
        """0008 is DB-only: no state change, so makemigrations stays clean."""
        assert not isinstance(
            MIG_0008.Migration.operations[0], dj_migrations.SeparateDatabaseAndState
        )

    def test_nonatomic(self):
        assert MIG_0008.Migration.atomic is False


class TestHealIdentifiers:
    def test_mysql_identifier_length_limits(self):
        assert len(heal_mod.FK_NAME) <= 64
        assert len(heal_mod.UNIQUE_INDEX_NAME) <= 64

    def test_unique_index_name_matches_django_generated(self):
        """Byte-parity with Django-created tables (verified live + greenfield)."""
        assert (
            heal_mod.UNIQUE_INDEX_NAME
            == "assistant_cc_transcript_chat_session_id_cc_sessi_bdda2d20_uniq"
        )
