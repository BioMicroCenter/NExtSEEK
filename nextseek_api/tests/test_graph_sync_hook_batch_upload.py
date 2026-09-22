"""Batch upload writes the graph through graph_sync (the sync design, section 8; C-13).

Stage 5 writes one outbox row per committed batch, on the batch's own connection and inside its own transaction, so
a rollback takes the row with it and a crash between the stages leaves the work recorded. Stage 6 calls
``graph_sync.targeted.sync_samples`` for every outcome that has a ``sample_id`` and marks this job's rows done when
that worked; anything else (no Neo4j, a graph below the writer's version, a busy graph-write lock, an error) leaves
them pending for the loop to drain and the job still succeeds.

No Neo4j and no MySQL here: ``sync_samples`` is a recorder, and stage 5's connection is a real SQLAlchemy SQLite
engine with a second database attached under the ``dmac`` name, so the qualified table name is exercised for real.
"""
from __future__ import annotations

import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import timezone
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from nextseek_api.batch_upload import insert as insert_mod
from nextseek_api.batch_upload import orchestrator as orch
from nextseek_api.batch_upload import neo4j_sync
from nextseek_api.batch_upload.errors import ErrorCollector
from nextseek_api.batch_upload.models import InsertableSample, RowOutcome
from nextseek_api.graph_sync import hooks, state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox

REPO_ROOT = Path(__file__).resolve().parents[2]

OUTBOX_DDL = """
CREATE TABLE dmac.graph_sync_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind VARCHAR(32) NOT NULL,
    `key` VARCHAR(191) NOT NULL,
    payload TEXT,
    enqueued_at DATETIME NOT NULL,
    claimed_by VARCHAR(255),
    lease_expires_at DATETIME,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    done_at DATETIME,
    UNIQUE (kind, `key`)
)
"""


@pytest.fixture
def seek_engine():
    """A SQLAlchemy engine shaped like batch upload's: the batch's own connection, with the dmac schema attached
    beside it. One pooled connection, so the ATTACH survives every checkout.

    The driver's own implicit BEGIN is turned off and SQLAlchemy emits the transaction instead, which is what makes
    a SAVEPOINT here behave as it does on MySQL (the SQLAlchemy SQLite dialect's "Serializable isolation /
    Savepoints / Transactional DDL" recipe). Without it pysqlite commits the savepoint on its own and a rollback
    test proves nothing.
    """
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _prepare(dbapi_connection, _record):
        dbapi_connection.isolation_level = None
        # ATTACH cannot run inside a transaction, so it goes on the raw connection.
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS dmac")

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    with engine.begin() as conn:
        conn.exec_driver_sql(OUTBOX_DDL)
        conn.exec_driver_sql("CREATE TABLE scratch (uuid VARCHAR(64))")
    yield engine
    engine.dispose()


def _outbox_rows(engine):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            "SELECT kind, `key`, payload, done_at FROM dmac.graph_sync_outbox ORDER BY id"))]


# --- stage 5: the row rides the batch's own transaction -------------------------------------------

class TestTheOutboxRowRidesTheBatchTransaction:

    def test_the_row_is_written_on_the_batchs_own_connection(self, seek_engine):
        with seek_engine.connect() as conn:
            trans = conn.begin()
            assert insert_mod.enqueue_samples_outbox(conn, "batch:job-1:L0:0", [3, 1, 2]) is True
            trans.commit()
        rows = _outbox_rows(seek_engine)
        assert len(rows) == 1
        assert rows[0]["kind"] == "samples"
        assert rows[0]["key"] == "batch:job-1:L0:0"
        assert json.loads(rows[0]["payload"]) == [1, 2, 3]
        assert rows[0]["done_at"] is None

    def test_a_rollback_takes_the_row_with_it(self, seek_engine):
        with seek_engine.connect() as conn:
            trans = conn.begin()
            insert_mod.enqueue_samples_outbox(conn, "batch:job-1:L0:0", [7])
            trans.rollback()
        assert _outbox_rows(seek_engine) == []

    def test_a_failed_insert_leaves_the_batch_transaction_usable(self, seek_engine, monkeypatch):
        """The row is written to a savepoint: the seek user missing the grant, or the table missing altogether,
        must not roll back the samples the batch just wrote."""
        monkeypatch.setattr(insert_mod, "graph_sync_outbox_table", lambda: "dmac.not_a_table")
        with seek_engine.connect() as conn:
            trans = conn.begin()
            conn.execute(text("INSERT INTO scratch (uuid) VALUES (:u)"), {"u": "before"})
            assert insert_mod.enqueue_samples_outbox(conn, "batch:job-1:L0:0", [7]) is False
            conn.execute(text("INSERT INTO scratch (uuid) VALUES (:u)"), {"u": "after"})
            trans.commit()
        with seek_engine.connect() as conn:
            kept = [r[0] for r in conn.execute(text("SELECT uuid FROM scratch ORDER BY uuid"))]
        assert kept == ["after", "before"]

    def test_an_empty_batch_writes_no_row(self, seek_engine):
        with seek_engine.connect() as conn:
            trans = conn.begin()
            assert insert_mod.enqueue_samples_outbox(conn, "batch:job-1:L0:0", []) is True
            trans.commit()
        assert _outbox_rows(seek_engine) == []

    def test_the_table_is_qualified_by_the_dmac_schema(self):
        """The batch's connection is SEEK's schema, so the outbox needs its own. A name that is not a bare
        identifier (SQLite's :memory: in this lane) falls back to the installed default."""
        assert insert_mod.graph_sync_outbox_table() == "dmac.graph_sync_outbox"
        with override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "NAME": "nextseek"},
                                          "seek": {"ENGINE": "django.db.backends.mysql", "NAME": "seek"}}):
            assert insert_mod.graph_sync_outbox_table() == "nextseek.graph_sync_outbox"


# --- stage 5: process_batches calls it, inside the transaction ------------------------------------

def _sample(uid):
    return InsertableSample(uuid=uid, title=uid, sample_type_id=1, json_metadata="{}", assay_ids=[])


def _direction():
    from nextseek_api.batch_upload.models import DirectionComputation
    return DirectionComputation(direction_by_pair={}, parents_of={}, assays_by_uid={},
                                child_uids_by_assay={}, conflicts_by_assay={})


class _RecordingConn:
    """A batch connection that records when it was left, so a test can tell what happened inside the transaction."""

    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, *args):
        self.events.append("commit")
        return False


def _run_one_batch(monkeypatch, events, *, batch_key_prefix, enqueued=True):
    """``process_batches`` over one new sample, with every step stubbed but the outbox call."""
    calls = []

    def _enqueue(conn, key, sample_ids, **kwargs):
        events.append("outbox")
        calls.append((key, list(sample_ids)))
        return enqueued

    monkeypatch.setattr(insert_mod, "enqueue_samples_outbox", _enqueue)
    monkeypatch.setattr(insert_mod, "determine_resume_uid", lambda *a, **k: None)
    monkeypatch.setattr(insert_mod, "insert_policies_for_uids", lambda uids, name, conn: [(u, 500) for u in uids])
    monkeypatch.setattr(insert_mod, "insert_samples", lambda rows, conn: [(100, r["uuid"]) for r in rows])
    monkeypatch.setattr(insert_mod, "batch_insert_projects_samples", lambda *a, **k: 1)
    monkeypatch.setattr(insert_mod, "batch_insert_assay_assets", lambda *a, **k: 0)
    monkeypatch.setattr(insert_mod, "write_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(insert_mod.PermissionsInserter, "insert_for_policy_ids", lambda self, ids, conn: 0)

    from nextseek_api.batch_upload.config import BatchUploadConfig
    result = insert_mod.process_batches(
        insertable_samples=[_sample("NHP-260101TST-1")],
        project_id=1,
        contributor_id=1,
        config=BatchUploadConfig(),
        direction_computation=_direction(),
        error_collector=ErrorCollector(),
        existing_samples={},
        conn_factory=lambda: _RecordingConn(events),
        batch_key_prefix=batch_key_prefix,
    )
    return result, calls


class TestStageFiveEnqueuesInsideTheTransaction:

    def test_the_enqueue_happens_before_the_batch_commits(self, monkeypatch):
        events = []
        result, calls = _run_one_batch(monkeypatch, events, batch_key_prefix="batch:job-1:L0")
        assert result.inserted_count == 1
        assert events.index("outbox") < events.index("commit")

    def test_the_key_and_the_committed_ids(self, monkeypatch):
        _result, calls = _run_one_batch(monkeypatch, [], batch_key_prefix="batch:job-1:L0")
        assert calls == [("batch:job-1:L0:0", [100])]

    def test_without_a_prefix_no_row_is_written(self, monkeypatch):
        """A caller that records its own work (the validate path, a direct call) is left alone."""
        _result, calls = _run_one_batch(monkeypatch, [], batch_key_prefix="")
        assert calls == []

    def test_a_refused_insert_falls_back_to_the_hook_after_the_commit(self, monkeypatch):
        """If the seek user cannot write dmac.graph_sync_outbox the row still gets written, after the commit,
        through the hook that never raises."""
        events = []
        hooked = []
        monkeypatch.setattr(hooks, "enqueue", lambda kind, key, payload=None: hooked.append((kind, key, payload)))
        _result, _calls = _run_one_batch(monkeypatch, events, batch_key_prefix="batch:job-1:L0", enqueued=False)
        assert hooked == [("samples", "batch:job-1:L0:0", [100])]
        assert events.index("commit") < len(events)


# --- stage 6: the inline sync ---------------------------------------------------------------------

def _outcomes():
    return {
        "A": RowOutcome(status="success", sample_id=11),
        "B": RowOutcome(status="skipped", reason="duplicate", sample_id=12),
        "C": RowOutcome(status="failed", reason="insert returned no id"),
        "D": RowOutcome(status="success", reason="updated", sample_id=10),
    }


class TestTheIdsStageSixSyncs:

    def test_every_outcome_with_a_sample_id(self):
        """Inserted, updated and skipped-duplicate rows alike; a failed row has no id and nothing to sync."""
        assert orch.graph_sample_ids(_outcomes()) == [10, 11, 12]

    def test_no_outcomes_is_no_ids(self):
        assert orch.graph_sample_ids({}) == []


class TestStageSixCallsSyncSamples:

    def test_the_ids_the_db_and_the_lock_wait(self, monkeypatch):
        from nextseek_api.graph_sync import targeted
        seen = {}

        def _sync(driver, db, ids, **kwargs):
            seen.update(driver=driver, db=db, ids=list(ids), kwargs=kwargs)
            return {"status": "ok"}

        monkeypatch.setattr(targeted, "sync_samples", _sync)
        monkeypatch.setattr(orch.Neo4jConfig, "from_django_settings", classmethod(lambda cls: _config()))
        with pytest.MonkeyPatch.context() as mp:
            fake = _fake_driver(mp)
            status = orch._run_graph_sync([10, 11, 12])
        assert status == "ok"
        assert seen["db"] == "graphdb"
        assert seen["ids"] == [10, 11, 12]
        assert seen["kwargs"]["lock_timeout_s"] == orch.GRAPH_LOCK_WAIT_S == 60
        assert seen["driver"] is fake

    def test_a_graph_that_is_not_configured_is_never_connected_to(self, monkeypatch):
        disabled = _config(enabled=False)
        monkeypatch.setattr(orch.Neo4jConfig, "from_django_settings", classmethod(lambda cls: disabled))
        with pytest.MonkeyPatch.context() as mp:
            _fake_driver(mp, fail=True)
            assert orch._run_graph_sync([10]) == "not_configured"

    def test_a_failure_never_reaches_the_caller(self, monkeypatch):
        monkeypatch.setattr(orch.Neo4jConfig, "from_django_settings", classmethod(lambda cls: _config()))
        from nextseek_api.graph_sync import targeted

        def _boom(*args, **kwargs):
            raise RuntimeError("Neo4j is down")

        monkeypatch.setattr(targeted, "sync_samples", _boom)
        with pytest.MonkeyPatch.context() as mp:
            _fake_driver(mp)
            assert orch._run_graph_sync([10]) == "error"


def _config(enabled=True):
    from types import SimpleNamespace
    return SimpleNamespace(NEO4J_UPLOAD_ENABLED=enabled, URI="neo4j://127.0.0.1", NEO4J_USER="u",
                           PASSWORD="p", NEO4J_DB="graphdb", MISSING_KEYS=["URI"] if not enabled else [])


def _fake_driver(mp, fail=False):
    """Replace ``neo4j.GraphDatabase.driver`` with one that hands back a sentinel; ``fail=True`` makes connecting
    at all an error, which is how a test proves nothing connected."""
    import neo4j

    sentinel = object()

    class _Ctx:
        def __enter__(self):
            return sentinel

        def __exit__(self, *args):
            return False

    def _driver(*args, **kwargs):
        if fail:
            raise AssertionError("the graph was connected to")
        return _Ctx()

    mp.setattr(neo4j.GraphDatabase, "driver", _driver)
    return sentinel


@pytest.mark.django_db
class TestStageSixMarksTheJobsRowsDone:

    JOB = "job-1"

    def _enqueue_rows(self):
        state.enqueue("samples", f"batch:{self.JOB}:L0:0", [11, 12])
        state.enqueue("samples", f"batch:{self.JOB}:L1:0", [10])
        state.enqueue("samples", "batch:another-job:L0:0", [99])
        state.enqueue("samples", "sample:7")

    def _run(self, monkeypatch, status):
        monkeypatch.setattr(orch, "_run_graph_sync", lambda ids: status)
        return orch._sync_graph_for_job(self.JOB, _outcomes())

    def test_a_synced_job_reports_the_count_and_closes_its_rows(self, monkeypatch):
        self._enqueue_rows()
        assert self._run(monkeypatch, "ok") == "synced (3)"
        done = set(GraphSyncOutbox.objects.filter(done_at__isnull=False).values_list("key", flat=True))
        assert done == {f"batch:{self.JOB}:L0:0", f"batch:{self.JOB}:L1:0"}

    def test_a_lock_timeout_reports_pending_and_leaves_every_row(self, monkeypatch):
        self._enqueue_rows()
        assert self._run(monkeypatch, "lock_timeout") == "pending (3)"
        assert GraphSyncOutbox.objects.filter(done_at__isnull=False).count() == 0

    def test_a_graph_below_the_writers_version_reports_pending(self, monkeypatch):
        self._enqueue_rows()
        assert self._run(monkeypatch, "not_at_version") == "pending (3)"
        assert GraphSyncOutbox.objects.filter(done_at__isnull=False).count() == 0

    def test_an_error_reports_pending(self, monkeypatch):
        self._enqueue_rows()
        assert self._run(monkeypatch, "error") == "pending (3)"
        assert GraphSyncOutbox.objects.filter(done_at__isnull=False).count() == 0

    def test_a_job_with_nothing_to_sync_never_calls_the_graph(self, monkeypatch):
        called = []
        monkeypatch.setattr(orch, "_run_graph_sync", lambda ids: called.append(ids) or "ok")
        assert orch._sync_graph_for_job(self.JOB, {}) == "synced (0)"
        assert called == []

    def test_a_row_enqueued_after_the_sync_started_stays_pending(self, monkeypatch):
        """Only rows the inline sync's read covers are closed."""
        later = timezone.now() + timedelta(minutes=5)
        state.enqueue("samples", f"batch:{self.JOB}:L0:0", [11], now=later)
        assert self._run(monkeypatch, "ok") == "synced (3)"
        assert GraphSyncOutbox.objects.get(key=f"batch:{self.JOB}:L0:0").done_at is None

    def test_marking_done_never_fails_the_job(self, monkeypatch):
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute('DROP TABLE "graph_sync_outbox"')
        assert self._run(monkeypatch, "ok") == "synced (3)"


# --- neo4j_only -----------------------------------------------------------------------------------

@pytest.mark.django_db
class TestNeo4jOnly:

    def test_the_sheets_uids_are_resolved_to_ids_and_recorded(self, monkeypatch):
        """neo4j_only mode reads nothing from the sheet but its UIDs: the ids it hands stage 6 come from
        ``samples``, and its outbox row records them like any batch."""
        from contextlib import contextmanager

        @contextmanager
        def _conn():
            yield object()

        monkeypatch.setattr(orch, "get_connection", _conn)
        monkeypatch.setattr(orch, "load_existing_samples",
                            lambda uids, conn: {"NHP-260101TST-1": 41, "NHP-260101TST-2": 42})
        samples = [_sample("NHP-260101TST-1"), _sample("NHP-260101TST-2"), _sample("NHP-260101TST-9")]
        result = orch._build_neo4j_only_outcomes(samples, ErrorCollector(), job_id="job-9")

        assert orch.graph_sample_ids(result.outcomes) == [41, 42]
        row = GraphSyncOutbox.objects.get(key="batch:job-9:neo4j_only")
        assert row.kind == "samples"
        assert row.payload == [41, 42]

    def test_no_resolved_uid_writes_no_row(self, monkeypatch):
        from contextlib import contextmanager

        @contextmanager
        def _conn():
            yield object()

        monkeypatch.setattr(orch, "get_connection", _conn)
        monkeypatch.setattr(orch, "load_existing_samples", lambda uids, conn: {})
        orch._build_neo4j_only_outcomes([_sample("NHP-260101TST-1")], ErrorCollector(), job_id="job-9")
        assert not GraphSyncOutbox.objects.exists()


# --- a cancel between the stages ------------------------------------------------------------------

class TestACancelAfterStageFive:

    def test_the_rows_stay_pending_and_nothing_syncs(self, monkeypatch):
        """The transactional row is what survives a cancel: stage 6 never runs, so the loop does the work."""
        from nextseek_api.batch_upload.tests.test_orchestrator_levels import FakeDB, _make_row, _run_orchestrator

        db = FakeDB()
        synced = []
        monkeypatch.setattr(orch, "_sync_graph_for_job", lambda job_id, outcomes: synced.append(job_id) or "synced")
        result = _run_orchestrator([_make_row("NHP-260101TST-1")], db,
                                   should_stop=lambda: bool(db.inserted_samples))

        assert result["totals"]["cancelled"] is True
        assert synced == []
        assert [r["kind"] for r in db.graph_sync_outbox] == ["samples"]
        assert db.graph_sync_outbox[0]["done_at"] is None


# --- what the v1.0 writers left behind ------------------------------------------------------------

DELETED = (
    "upload_all",
    "ensure_constraints",
    "bulk_merge_nodes",
    "bulk_merge_sample_type_nodes",
    "bulk_merge_relationships",
    "bulk_merge_of_type_relationships",
    "bulk_merge_in_study_relationships",
    "bulk_merge_study_nodes",
    "bulk_merge_investigation_nodes",
    "bulk_merge_in_investigation_relationships",
    "delete_derived_from_for_uuids",
    "delete_stale_derived_from_for_uuids",
    "find_missing_derived_from_endpoints",
    "find_missing_in_study_endpoints",
)

SCANNED = ("ci", "dmac", "nextseek_api", "scripts", "seek", "startup")


def _deleted_names_used(path: Path) -> set:
    """Which deleted functions this module imports from ``neo4j_sync`` or calls on it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:  # not this test's business
        return set()
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "neo4j_sync":
            used.update(a.name for a in node.names if a.name in DELETED)
        elif isinstance(node, ast.Attribute) and node.attr in DELETED:
            target = node.value
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name == "neo4j_sync":
                used.add(node.attr)
    return used


class TestTheV10WritersAreGone:

    def test_none_of_them_is_defined_any_more(self):
        present = [name for name in DELETED if hasattr(neo4j_sync, name)]
        assert present == []

    def test_no_module_imports_or_calls_one(self):
        """The graph writes are graph_sync's now, so nothing may reach for the v1.0 ones.

        By what a module does, not what it says: an import of the name from ``neo4j_sync`` or a
        ``neo4j_sync.<name>`` attribute. Prose is left alone, so this module's own docstring can go on recording
        what went and where the writes live now, and graph_sync's unrelated ``ensure_constraints_v11`` is not a
        match for the constraint DDL batch upload used to issue.
        """
        found = {}
        for folder in SCANNED:
            for path in sorted((REPO_ROOT / folder).rglob("*.py")):
                named = sorted(_deleted_names_used(path))
                if named:
                    found[str(path.relative_to(REPO_ROOT))] = named
        assert found == {}
