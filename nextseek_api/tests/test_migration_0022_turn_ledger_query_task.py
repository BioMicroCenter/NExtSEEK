"""Migration 0022 links each TurnLedger row to its QueryTask, and it closes the chain without a fork.

The chain has forked before and been stitched by merge migrations (nextseek_api/CLAUDE.md), so the leaf is pinned
here, and makemigrations must propose nothing further for TurnLedger: a model/migration mismatch would reach a box
as DDL nobody reviewed.
"""
import os
import subprocess
import sys
from pathlib import Path

from django.db import models
from django.db.migrations.loader import MigrationLoader

from nextseek_api.assistant.models_db import QueryTask, TurnLedger

REPO_ROOT = Path(__file__).resolve().parents[2]
LEAF = ("nextseek_api", "0022_turn_ledger_query_task")


def test_0022_is_the_single_leaf_and_sits_on_0021():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.graph.leaf_nodes("nextseek_api") == [LEAF]
    assert loader.get_migration(*LEAF).dependencies == [("nextseek_api", "0021_graph_sync_outbox_and_run")]


def test_the_link_is_a_nullable_set_null_fk_to_the_task_pk():
    field = TurnLedger._meta.get_field("query_task")
    assert isinstance(field, models.ForeignKey)
    assert field.related_model is QueryTask
    assert field.null and field.remote_field.on_delete is models.SET_NULL
    assert field.target_field.name == "id"       # the bigint pk, not the char(32) task UUID
    assert field.column == "query_task_id"


def test_the_session_turn_uniqueness_is_kept():
    names = {c.name for c in TurnLedger._meta.constraints}
    assert "uniq_turn_per_session" in names


def test_makemigrations_finds_no_change_for_the_turn_ledger():
    """Run as its own process, like test_graph_sync_models.py's makemigrations check, for the same reasons."""
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "dmac.test_settings"}
    proc = subprocess.run(
        [sys.executable, "manage.py", "makemigrations", "nextseek_api", "--check", "--dry-run", "--skip-checks"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode in (0, 1), f"makemigrations failed:\n{combined}"
    if proc.returncode == 0:
        assert "No changes detected" in proc.stdout, f"makemigrations did not run:\n{combined}"
    else:
        assert proc.stdout.startswith("Migrations for 'nextseek_api'"), f"makemigrations did not run:\n{combined}"
    assert "Traceback" not in proc.stderr, f"makemigrations did not run:\n{combined}"
    ours = [line for line in proc.stdout.splitlines() if "turnledger" in line.lower()]
    assert ours == [], f"makemigrations proposes a change to TurnLedger:\n{combined}"
