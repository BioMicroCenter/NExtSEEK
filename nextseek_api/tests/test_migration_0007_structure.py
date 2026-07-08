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

    def test_reverse_restores_create_model_semantics(self):
        """Reversing below 0007 must drop the child table; a noop reverse
        strands the heal-added FK and 0001's reverse DROP of
        assistant_chat_session then fails with errno 3730
        (ER_FK_CANNOT_DROP_PARENT)."""
        db_ops = MIG_0007.Migration.operations[0].database_operations
        assert db_ops[0].reverse_code is heal_mod.unheal

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

    def test_reverse_stays_noop(self):
        """0008 owns no schema (0007's reverse drops the table); its reverse
        must remain a noop so reversing 0008 alone never destroys data."""
        ops = MIG_0008.Migration.operations
        assert ops[0].reverse_code is dj_migrations.RunPython.noop

    def test_no_state_operations(self):
        """0008 is DB-only: no state change, so makemigrations stays clean."""
        assert not isinstance(
            MIG_0008.Migration.operations[0], dj_migrations.SeparateDatabaseAndState
        )

    def test_nonatomic(self):
        assert MIG_0008.Migration.atomic is False


class TestFrozenFallback:
    """The heal's non-MySQL create path must build the table from 0007's own
    frozen CreateModel — never the live model class. Otherwise a future 0009
    AddField would be baked into 0007's CREATE TABLE on fresh non-MySQL
    chains, and 0009 itself would then fail with a duplicate column."""

    def _historical_apps_without_model(self):
        """Render the registry as 0007's RunPython sees it (pre-migration
        state: CCSessionTranscript does not exist yet)."""
        from django.apps import apps as real_apps
        from django.db.migrations.state import ProjectState

        state = ProjectState.from_apps(real_apps)
        state.remove_model("nextseek_api", "ccsessiontranscript")
        return state.apps

    def test_frozen_model_renders_0007_shape(self):
        historical_apps = self._historical_apps_without_model()
        model = heal_mod._frozen_0007_model(historical_apps)
        cm = MIG_0007.Migration.operations[0].state_operations[0]
        assert [f.name for f in model._meta.concrete_fields] == [
            name for name, _ in cm.fields
        ]
        assert model._meta.db_table == "assistant_cc_transcript"

    def test_frozen_model_is_not_the_live_class(self):
        from nextseek_api.assistant.models_db import CCSessionTranscript

        historical_apps = self._historical_apps_without_model()
        model = heal_mod._frozen_0007_model(historical_apps)
        assert model is not CCSessionTranscript

    def test_frozen_fk_resolves_historical_chat_session(self):
        """The FK must bind to a ChatSession rendered from the historical
        state — never the live registered class (registry-conflict hazard).
        _frozen_0007_model clones the passed-in registry (ProjectState.
        from_apps) instead of mutating it, so identity with the caller's
        classes is intentionally NOT expected — only historical shape."""
        from nextseek_api.assistant.models_db import (
            ChatSession as LiveChatSession,
        )

        historical_apps = self._historical_apps_without_model()
        model = heal_mod._frozen_0007_model(historical_apps)
        fk = model._meta.get_field("chat_session")
        target = fk.remote_field.model
        assert target is not LiveChatSession
        assert target._meta.db_table == "assistant_chat_session"
        assert fk.target_field.column == "session_id"


class TestLiveModelParity:
    """DRIFT TRIPWIRE (review follow-up FU2, 2026-07-07): the live
    CCSessionTranscript must stay field-for-field identical to 0007's
    CreateModel. The heal's MySQL branch freezes 0007's shape as hand DDL
    (_CREATE_TABLE_SQL) and the non-MySQL branch renders 0007's CreateModel;
    if you are changing the model, ship the new field via a normal 0009+
    migration and DO NOT touch 0007's frozen shapes — this test failing means
    the model and 0007 have drifted, which is expected for a properly
    migrated change ONLY if 0007's CreateModel stays untouched. See
    nextseek_api/migrations/_cc_transcript_heal.py (fallback + hand DDL)."""

    def _canon_fields(self, fields):
        out = {}
        for name, field in fields:
            path, args, kwargs = field.deconstruct()[1:]
            if "to" in kwargs:
                kwargs = {**kwargs, "to": str(kwargs["to"]).lower()}
            out[name] = (path, args, kwargs)
        return out

    def test_live_fields_match_0007_create_model(self):
        from django.db.migrations.state import ModelState

        from nextseek_api.assistant.models_db import CCSessionTranscript

        cm = MIG_0007.Migration.operations[0].state_operations[0]
        live = ModelState.from_model(CCSessionTranscript)
        assert self._canon_fields(cm.fields) == self._canon_fields(
            live.fields.items()
        ), (
            "Live CCSessionTranscript has drifted from 0007's CreateModel — "
            "the heal's frozen table shapes (hand DDL + frozen render in "
            "_cc_transcript_heal.py) would create a 0007-shaped table that "
            "no longer matches the model. Add new fields via a normal 0009+ "
            "migration; never widen 0007."
        )

    def test_live_options_match_0007_create_model(self):
        from django.db.migrations.state import ModelState

        from nextseek_api.assistant.models_db import CCSessionTranscript

        cm = MIG_0007.Migration.operations[0].state_operations[0]
        live = ModelState.from_model(CCSessionTranscript)
        for key in ("db_table", "ordering", "unique_together"):
            live_val = live.options.get(key)
            cm_val = cm.options.get(key)
            if key == "unique_together":
                live_val = {tuple(t) for t in (live_val or ())}
                cm_val = {tuple(t) for t in (cm_val or ())}
            assert live_val == cm_val, f"options[{key!r}] drifted from 0007"


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
