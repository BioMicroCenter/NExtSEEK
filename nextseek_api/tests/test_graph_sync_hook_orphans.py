"""Orphan resolution enqueues the children it resolved, and writes no graph (the sync design, sections 8 and 12;
C-14; nextseek_api/batch_upload/tasks.py::resolve_orphans_task).

The Celery task keeps its MySQL rewrite. What follows it changes: one ``samples sample:<id>`` outbox row per resolved
child, written after that rewrite's transaction has committed and never inside it, and nothing sent to Neo4j. The row
is the record the drain turns into a graph write, so a failure to write it is logged and counted, never raised.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError

from nextseek_api.graph_sync import hooks, state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox


@pytest.fixture(autouse=True)
def fresh_counts():
    hooks.reset_failure_counts()
    yield
    hooks.reset_failure_counts()


class FakeConnection:
    """What ``db_engine.get_connection()`` returns: a block that commits when it exits."""

    def __init__(self):
        self.exited = False

    def __enter__(self):
        return MagicMock(name="sql_conn")

    def __exit__(self, *exc_info):
        self.exited = True
        return False


@contextmanager
def task_world(*, orphans, sample_ids, connection=None):
    """The task's world: a reachable Neo4j, ``discover_orphans`` finding ``orphans`` and ``resolve_orphans``
    resolving ``sample_ids``. Yields the connection the task's rewrite runs in."""
    connection = connection or FakeConnection()
    with patch("nextseek_api.batch_upload.orphan_resolution.discover_orphans") as discover, \
         patch("nextseek_api.batch_upload.orphan_resolution.resolve_orphans") as resolve, \
         patch("nextseek_api.batch_upload.db_engine.get_connection") as get_connection, \
         patch("nextseek_api.batch_upload.config.Neo4jConfig.from_django_settings") as from_settings, \
         patch("neo4j.GraphDatabase") as graph_database:
        from_settings.return_value = MagicMock(
            NEO4J_UPLOAD_ENABLED=True, URI="bolt://localhost",
            NEO4J_USER="u", PASSWORD="p", NEO4J_DB="db",
        )
        graph_database.driver.return_value = MagicMock(name="driver")
        discover.return_value = list(orphans)
        resolve.return_value = {"resolved": len(sample_ids), "sample_ids": list(sample_ids)}
        get_connection.return_value = connection
        yield connection


def run_task():
    from nextseek_api.batch_upload.tasks import resolve_orphans_task

    return resolve_orphans_task.run(identity_map={"Mouse-A": "MUS-260305MIT-1"}, parent_info={})


ONE_ORPHAN = [{"id": 500, "uuid": "CHD-260101MIT-1", "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"}}]
TWO_ORPHANS = ONE_ORPHAN + [{"id": 600, "uuid": "CHD-260101MIT-2", "matched_tokens": {"Mouse-A": "MUS-260305MIT-1"}}]


@pytest.mark.django_db
def test_every_resolved_child_gets_one_row():
    with task_world(orphans=TWO_ORPHANS, sample_ids=[500, 600]):
        result = run_task()

    rows = GraphSyncOutbox.objects.filter(kind="samples", done_at__isnull=True)
    assert set(rows.values_list("key", flat=True)) == {"sample:500", "sample:600"}
    assert [r.payload for r in rows] == [None, None]
    assert result["queued"] == 2


@pytest.mark.django_db
def test_the_row_is_written_after_the_rewrite_commits():
    """The hook goes after the writer's own commit, never inside its transaction (the sync design, section 8)."""
    committed_first = []
    connection = FakeConnection()
    real_enqueue = hooks.enqueue

    def spy(kind, key, payload=None):
        committed_first.append(connection.exited)
        return real_enqueue(kind, key, payload)

    with task_world(orphans=ONE_ORPHAN, sample_ids=[500], connection=connection), \
         patch.object(hooks, "enqueue", spy):
        run_task()

    assert committed_first == [True]
    assert GraphSyncOutbox.objects.filter(kind="samples", key="sample:500").exists()


@pytest.mark.django_db
def test_nothing_is_enqueued_when_no_orphan_resolves():
    with task_world(orphans=ONE_ORPHAN, sample_ids=[]):
        result = run_task()

    assert not GraphSyncOutbox.objects.exists()
    assert result["queued"] == 0


@pytest.mark.django_db
def test_nothing_is_enqueued_when_discovery_finds_nothing():
    with task_world(orphans=[], sample_ids=[]):
        result = run_task()

    assert not GraphSyncOutbox.objects.exists()
    assert result == {"resolved": 0}


@pytest.mark.django_db
def test_an_enqueue_failure_never_reaches_the_caller(monkeypatch):
    """The rewrite has already committed: a lost row is a delay the nightly targeted sync closes, not an error."""
    def broken(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", broken)
    with task_world(orphans=ONE_ORPHAN, sample_ids=[500]):
        result = run_task()

    assert result["resolved"] == 1 and result["queued"] == 0
    assert hooks.failure_counts() == {"samples": 1}


@pytest.mark.django_db
def test_the_task_sends_no_write_to_neo4j():
    """The driver is the discovery read and nothing else: the drain writes the graph."""
    with task_world(orphans=ONE_ORPHAN, sample_ids=[500]), \
         patch("neo4j.GraphDatabase") as graph_database:
        driver = MagicMock(name="driver")
        graph_database.driver.return_value = driver
        run_task()

    assert driver.execute_query.call_args_list == []
    assert driver.session.call_args_list == []


@pytest.mark.django_db
def test_two_uploads_resolving_the_same_child_coalesce():
    for _ in range(2):
        with task_world(orphans=ONE_ORPHAN, sample_ids=[500]):
            run_task()

    assert GraphSyncOutbox.objects.filter(kind="samples", key="sample:500").count() == 1
