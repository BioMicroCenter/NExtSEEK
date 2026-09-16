"""The graph_sync state tables (nextseek_api/graph_sync/models_db.py, migration 0021).

Runs on the SQLite test settings: the rows go to the in-memory ``default`` database, and the migration checks read
only the migration files and the model state, never a live database.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader

from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_models_map_the_two_tables():
    assert GraphSyncOutbox._meta.db_table == "graph_sync_outbox"
    assert GraphSyncRun._meta.db_table == "graph_sync_run"


def test_nextseek_api_models_re_exports_both_models():
    """The re-export at the end of nextseek_api/models.py is what registers them when the app loads; without it 0021
    manages two tables whose classes the registry never loads.

    Asserted on the module itself: the import at the top of this file registers both classes on its own, so an
    ``apps.get_model`` lookup here would pass with the re-export gone."""
    import nextseek_api.models as app_models

    assert app_models.GraphSyncOutbox is GraphSyncOutbox
    assert app_models.GraphSyncRun is GraphSyncRun
    assert apps.get_model("nextseek_api", "GraphSyncOutbox") is GraphSyncOutbox
    assert apps.get_model("nextseek_api", "GraphSyncRun") is GraphSyncRun


@pytest.mark.django_db
def test_a_second_kind_and_key_insert_raises():
    """(kind, key) is unique, so a scheduled slot is inserted once and repeated hook writes coalesce."""
    GraphSyncOutbox.objects.create(kind="samples", key="sample:7")
    GraphSyncOutbox.objects.create(kind="retire", key="sample:7")
    with pytest.raises(IntegrityError), transaction.atomic():
        GraphSyncOutbox.objects.create(kind="samples", key="sample:7")
    assert GraphSyncOutbox.objects.filter(key="sample:7").count() == 2


@pytest.mark.django_db
def test_payload_round_trips_a_list_of_ints():
    row = GraphSyncOutbox.objects.create(kind="samples", key="batch:job-1:0", payload=[3, 1, 2, 1084754])
    row.refresh_from_db()
    assert row.payload == [3, 1, 2, 1084754]


@pytest.mark.django_db
def test_payload_is_null_when_not_given():
    row = GraphSyncOutbox.objects.create(kind="catalog", key="*")
    row.refresh_from_db()
    assert row.payload is None


def test_makemigrations_finds_no_change_for_the_graph_sync_models():
    """The two models and migration 0021 agree: makemigrations proposes no operation on either model.

    Scoped to the two models, not to the whole app, because the app already carries one change that predates these
    tables (a TurnLedger index whose generated name no longer matches its migration); the dry-run lists each
    proposed operation with its model name, so a line naming either model is a change here.

    Run as its own process, like test_sqlite_lane_migrations.py. makemigrations reads the applied-migration history
    of every database the router lets a model migrate to, ``seek`` included; inside pytest-django that alias is
    blocked, and opening it fails the test-database setup on the ContentType relation the router refuses there.
    ``--skip-checks`` because the test settings fail a deployment check (CSRF_TRUSTED_ORIGINS) that says nothing
    about migrations; call_command skips the same checks by default."""
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
    # A crash (bad settings, an import error) also exits 1 with nothing on stdout, which the filter below would pass.
    if proc.returncode == 0:
        assert "No changes detected" in proc.stdout, f"makemigrations did not run:\n{combined}"
    else:
        assert proc.stdout.startswith("Migrations for 'nextseek_api'"), f"makemigrations did not run:\n{combined}"
    assert "Traceback" not in proc.stderr, f"makemigrations did not run:\n{combined}"
    names = ("graphsyncoutbox", "graphsyncrun")
    ours = [line for line in proc.stdout.splitlines() if any(name in line.lower() for name in names)]
    assert ours == [], f"makemigrations proposes a change to the graph_sync models:\n{combined}"


def test_0021_is_the_single_leaf_of_the_app():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.graph.leaf_nodes("nextseek_api") == [("nextseek_api", "0021_graph_sync_outbox_and_run")]
    assert loader.get_migration("nextseek_api", "0021_graph_sync_outbox_and_run").dependencies == [
        ("nextseek_api", "0020_assayregistrationjob")
    ]
