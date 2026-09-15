"""hooks.enqueue: what every NExtSEEK writer calls after it writes (nextseek_api/graph_sync/hooks.py; spec 12, CI-6).

The one rule: it never raises into its caller. A failure is logged and counted, and the writer's own work stands.
Runs on the SQLite test settings.
"""
from __future__ import annotations

import logging

import pytest
from django.contrib.auth.models import User
from django.db import OperationalError, connection, transaction

from nextseek_api.graph_sync import hooks, state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox


@pytest.fixture(autouse=True)
def fresh_counts():
    hooks.reset_failure_counts()
    yield
    hooks.reset_failure_counts()


@pytest.mark.django_db
def test_enqueue_writes_the_row_and_reports_success():
    assert hooks.enqueue("samples", "sample:7") is True
    assert GraphSyncOutbox.objects.filter(kind="samples", key="sample:7", done_at__isnull=True).exists()
    assert hooks.failure_counts() == {}


@pytest.mark.django_db
def test_enqueue_passes_the_payload():
    hooks.enqueue("samples", "batch:job-1:0", [4, 5])
    assert GraphSyncOutbox.objects.get(key="batch:job-1:0").payload == [4, 5]


@pytest.mark.django_db
def test_enqueue_swallows_a_database_error(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", broken)
    with caplog.at_level(logging.ERROR, logger="nextseek_api.graph_sync.hooks"):
        assert hooks.enqueue("catalog", "*") is False
    assert hooks.failure_counts() == {"catalog": 1}
    assert "catalog" in caplog.text and "gone away" in caplog.text


@pytest.mark.django_db
def test_enqueue_swallows_a_missing_table():
    with connection.cursor() as cur:
        cur.execute('DROP TABLE "graph_sync_outbox"')
    assert hooks.enqueue("retire", "sample:7") is False
    assert hooks.failure_counts() == {"retire": 1}


def test_enqueue_swallows_a_malformed_item(caplog):
    with caplog.at_level(logging.ERROR, logger="nextseek_api.graph_sync.hooks"):
        assert hooks.enqueue("samples", "sample:None") is False
    assert hooks.failure_counts() == {"samples": 1}
    assert "sample:None" in caplog.text


def test_enqueue_swallows_any_exception(monkeypatch):
    monkeypatch.setattr(state, "enqueue", lambda *a, **k: 1 / 0)
    assert hooks.enqueue("isa", "*") is False
    assert hooks.enqueue("isa", "*") is False
    assert hooks.enqueue("membership", "*") is False
    assert hooks.failure_counts() == {"isa": 2, "membership": 1}


def test_enqueue_survives_a_kind_that_is_not_a_string(monkeypatch):
    assert hooks.enqueue(["samples"], "sample:1") is False
    assert sum(hooks.failure_counts().values()) == 1


@pytest.mark.django_db
def test_a_failed_enqueue_leaves_the_writers_transaction_usable():
    """A writer that enqueues inside its own transaction on the dmac database keeps that transaction: the failed
    insert is rolled back to a savepoint, and the writer's rows commit."""
    with connection.cursor() as cur:
        cur.execute('DROP TABLE "graph_sync_outbox"')
    with transaction.atomic():
        User.objects.create(username="writer-row")
        assert hooks.enqueue("samples", "sample:1") is False
        User.objects.create(username="after-the-hook")
    assert set(User.objects.values_list("username", flat=True)) >= {"writer-row", "after-the-hook"}


@pytest.mark.django_db
def test_an_enqueue_inside_a_transaction_that_rolls_back_leaves_no_row():
    """The row rides the writer's transaction on the dmac database: no write, no row."""
    with pytest.raises(RuntimeError), transaction.atomic():
        hooks.enqueue("samples", "sample:1")
        raise RuntimeError("the writer failed")
    assert not GraphSyncOutbox.objects.exists()


def test_failure_counts_is_a_copy():
    hooks.enqueue("samples", "sample:None")
    counts = hooks.failure_counts()
    counts["samples"] = 99
    assert hooks.failure_counts() == {"samples": 1}
