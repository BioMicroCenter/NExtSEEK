"""A batch upload queues every sample it inserts or updates in SEEK's auth_lookup_update_queues.

SEEK refreshes sample_auth_lookup from that table (AuthLookupUpdateJob). The batch writes samples straight into
SEEK's database, so without these rows the lookup misses them. The table is SEEK 1.15.1's
(id, item_id, item_type, created_at, updated_at, priority), written here on the batch's own connection.
"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from nextseek_api.batch_upload.config import BatchUploadConfig
from nextseek_api.batch_upload.errors import ErrorCollector
from nextseek_api.batch_upload.insert import process_batches
from nextseek_api.batch_upload.models import DirectionComputation, InsertableSample, RowOutcome


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE auth_lookup_update_queues (id INTEGER PRIMARY KEY, item_id INTEGER, "
                       "item_type TEXT, created_at TEXT, updated_at TEXT, priority INTEGER DEFAULT 0)"))
        c.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY, uuid TEXT, policy_id INTEGER, "
                       "json_metadata TEXT, title TEXT, contributor_id INTEGER)"))
        c.execute(text("CREATE TABLE permissions (id INTEGER PRIMARY KEY, policy_id INTEGER, "
                       "contributor_type TEXT, contributor_id INTEGER, access_type INTEGER, created_at TEXT, updated_at TEXT)"))
        c.execute(text("CREATE TABLE projects_samples (project_id INTEGER, sample_id INTEGER)"))
        c.execute(text("INSERT INTO samples VALUES (1, 'OLD-1', 11, '{}', 't', 99), (2, 'OLD-2', 12, '{}', 't', 99)"))
        c.execute(text("INSERT INTO projects_samples VALUES (7, 1), (7, 2)"))
    return eng


def _queue(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT item_id, item_type, priority, created_at FROM auth_lookup_update_queues "
                              "ORDER BY item_id, item_type")).fetchall()


def _run(engine, samples, existing, **kw):
    return process_batches(
        insertable_samples=samples, project_id=7, contributor_id=5, config=BatchUploadConfig(),
        direction_computation=DirectionComputation({}, {}, {}, {}), error_collector=ErrorCollector(),
        existing_samples=existing, conn_factory=engine.begin, **kw)


def _sample(uuid):
    return InsertableSample(uuid=uuid, title="n", sample_type_id=1, json_metadata="{}")


def test_every_inserted_sample_is_queued_once(engine):
    ids = {"NEW-1": 10, "NEW-2": 11, "NEW-3": 12}
    with patch("nextseek_api.batch_upload.insert.insert_policies_for_uids",
               side_effect=lambda uids, name, conn: [(u, 1) for u in uids]), \
         patch("nextseek_api.batch_upload.insert.insert_samples",
               side_effect=lambda rows, conn: [(ids[r["uuid"]], r["uuid"]) for r in rows]), \
         patch("nextseek_api.batch_upload.insert.batch_insert_projects_samples", return_value=3), \
         patch("nextseek_api.batch_upload.insert.batch_insert_assay_assets", return_value=0):
        result = _run(engine, [_sample(u) for u in ids], {})
    assert result.inserted_count == 3
    rows = _queue(engine)
    # 2 is SEEK's own default (ResourceQueue::DEFAULT_PRIORITY, lib/seek/samples/extractor.rb); lowest runs first
    assert [(r[0], r[1], r[2]) for r in rows] == [(10, "Sample", 2), (11, "Sample", 2), (12, "Sample", 2)]
    assert all(r[3] for r in rows)  # SEEK's job reads created_at


def test_every_updated_sample_is_queued_once(engine):
    import nextseek_api.batch_upload.update as update_mod

    def fake_bulk(rows, details, **kw):
        return {s.uuid: RowOutcome(status="success", sample_id=details[s.uuid]["sample_id"]) for s in rows}

    with patch.object(update_mod, "bulk_update_samples", side_effect=fake_bulk):
        result = _run(engine, [_sample("OLD-1"), _sample("OLD-2")], {"OLD-1": 1, "OLD-2": 2},
                      update_existing=True)
    assert result.updated_count == 2
    assert [(r[0], r[1]) for r in _queue(engine)] == [(1, "Sample"), (2, "Sample")]


def test_a_missing_queue_table_does_not_fail_the_batch():
    from nextseek_api.batch_upload.insert import enqueue_auth_lookup

    eng = create_engine("sqlite://")
    with eng.begin() as c:
        assert enqueue_auth_lookup(c, [1, 2]) is False


def test_a_sample_already_queued_keeps_its_one_row(engine):
    # SEEK's enqueue keeps one row per item (first_or_initialize on item_id and item_type) and its priority.
    from nextseek_api.batch_upload.insert import enqueue_auth_lookup

    with engine.begin() as c:
        c.execute(text("INSERT INTO auth_lookup_update_queues (item_id, item_type, priority) "
                       "VALUES (11, 'Sample', 1), (12, 'Person', 2)"))
    with engine.begin() as c:
        assert enqueue_auth_lookup(c, [10, 11, 12]) is True
    assert [(r[0], r[1], r[2]) for r in _queue(engine)] == [
        (10, "Sample", 2), (11, "Sample", 1), (12, "Person", 2), (12, "Sample", 2)]
