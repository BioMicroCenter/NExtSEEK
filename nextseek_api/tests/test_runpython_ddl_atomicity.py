"""Every migration that issues DDL inside RunPython must declare atomic = False.

On MySQL (no transactional DDL) Django's ``Migration.apply`` force-wraps atomic
RunPython operations in a transaction. DDL executed inside that forced
transaction either raises ``TransactionManagementError`` (when routed through
``schema_editor.execute`` — the seek.0002 cold clean-seed wedge, ESCALATION
2026-07-10) or silently implicit-commits mid-transaction (when routed through a
raw ``connection.cursor()`` — 0005_chatsession_extra_state_column). Both are
wrong; ``atomic = False`` is the standard remedy and the precedent set by
nextseek_api.0005_ensure/0007/0008.

This is a hermetic contract lock: the MySQL-lane behavioral proof for seek.0002
lives in nextseek_api/batch_upload/tests/test_migration_name_identity.py
(TestMigrationExecutorAtomicity). A behavioral test cannot go red for the
raw-cursor variant — nothing crashes — so the declaration itself is the oracle.
"""
from __future__ import annotations

from importlib import import_module

import pytest

DDL_RUNPYTHON_MIGRATIONS = [
    "seek.migrations.0002_samples_name_identity",
    "nextseek_api.migrations.0005_chatsession_extra_state_column",
    "nextseek_api.migrations.0005_ensure_chatsession_extra_state_column",
    "nextseek_api.migrations.0007_ccsessiontranscript",
    "nextseek_api.migrations.0008_heal_cc_transcript_fk",
    "nextseek_api.migrations.0010_turn_ledger",
]


@pytest.mark.parametrize("module_path", DDL_RUNPYTHON_MIGRATIONS)
def test_ddl_runpython_migration_declares_atomic_false(module_path):
    migration_cls = import_module(module_path).Migration
    assert migration_cls.atomic is False, (
        f"{module_path} issues DDL from RunPython but does not set atomic = False; "
        "on MySQL the forced transaction wedges or implicit-commits the migration"
    )
