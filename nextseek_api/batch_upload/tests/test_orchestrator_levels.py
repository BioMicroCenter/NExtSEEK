"""Integration tests for orchestrator level-by-level insertion ordering.

Uses DB-level mocking: all stages run with real code against a FakeDB
that routes SQL queries to in-memory state.
"""
from __future__ import annotations

import os
import re
import tempfile
from contextlib import contextmanager
from functools import wraps
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.orchestrator import run_batch_upload
from nextseek_api.batch_upload.config import BatchUploadConfig
from nextseek_api.batch_upload.insert import process_batches as _real_process_batches
from nextseek_api.batch_upload.models import ConvertedBatch, InputRowModel


# ── FakeDB ──────────────────────────────────────────────────────────────


class FakeResultProxy:
    """Mimics SQLAlchemy result proxy."""

    def __init__(self, rows: List[Tuple] | None = None, scalar_val: Any = None):
        self._rows = rows or []
        self._scalar_val = scalar_val

    def fetchall(self) -> List[Tuple]:
        return self._rows

    def fetchone(self) -> Tuple | None:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        if self._scalar_val is not None:
            return self._scalar_val
        return self._rows[0][0] if self._rows and self._rows[0] else None


class FakeDB:
    """In-memory fake database routing SQL queries to state dicts.

    Tracks samples, sample_types, policies, projects_samples, assay_assets,
    permissions, and assays.

    Configure via constructor params:
    - sample_types: {title: id}
    - existing_samples: {uuid: id} — samples already in DB before upload
    - fail_uuids: set of UUIDs whose INSERT should silently "fail" (no id returned)
    """

    def __init__(
        self,
        sample_types: Dict[str, int] | None = None,
        existing_samples: Dict[str, int] | None = None,
        fail_uuids: Set[str] | None = None,
    ):
        self.sample_types = sample_types or {"NHP_blood": 1}
        self.existing_samples = dict(existing_samples or {})
        self.fail_uuids = fail_uuids or set()

        # Auto-increment counters
        self._next_sample_id = max(self.existing_samples.values(), default=0) + 1
        self._next_policy_id = 1

        # State tracking
        self.inserted_samples: Dict[str, int] = {}  # uuid -> id
        self.inserted_policies: List[int] = []
        self.projects_samples: Set[Tuple[int, int]] = set()  # (pid, sid)
        self.assay_assets: Set[Tuple[int, int]] = set()  # (assay_id, asset_id)

        # Track call order for assertions
        self.insert_sample_calls: List[List[str]] = []  # list of uuid lists per INSERT

    def route(self, sql_text: str, params: Dict[str, Any]) -> FakeResultProxy:
        """Route a SQL query to the appropriate handler."""
        sql = sql_text.strip()

        # ── sample_types ──
        if "FROM sample_types" in sql and "SELECT" in sql:
            if "WHERE title =" in sql and "LIMIT 1" in sql:
                title = params.get("title", "")
                sid = self.sample_types.get(title)
                if sid is not None:
                    return FakeResultProxy(rows=[(sid,)])
                return FakeResultProxy(rows=[])
            if "WHERE title IN" in sql:
                rows = [
                    (t, tid)
                    for t, tid in self.sample_types.items()
                    if t in params.values()
                ]
                return FakeResultProxy(rows=rows)

        # ── assays ──
        if "FROM assays" in sql and "SELECT" in sql:
            return FakeResultProxy(rows=[])  # no assays in tests

        # ── projects_sample_types ──
        if "FROM projects_sample_types" in sql:
            return FakeResultProxy(rows=[])  # no existing links
        if "INSERT" in sql and "projects_sample_types" in sql:
            return FakeResultProxy()

        # ── samples SELECT (load_existing_samples, fallback SELECT) ──
        if re.search(r"SELECT\s+id\s*,\s*uuid\s+FROM\s+samples", sql):
            uuid_params = [v for k, v in params.items() if k.startswith("u_")]
            rows = []
            for uid in uuid_params:
                if uid in self.existing_samples:
                    rows.append((self.existing_samples[uid], uid))
                elif uid in self.inserted_samples:
                    rows.append((self.inserted_samples[uid], uid))
            return FakeResultProxy(rows=rows)

        # ── samples INSERT ──
        if "INSERT INTO samples" in sql:
            uuid_params = sorted(
                [(k, v) for k, v in params.items() if k.startswith("uuid_")],
                key=lambda x: x[0],
            )
            uuids = [v for _, v in uuid_params]
            self.insert_sample_calls.append(uuids)

            if "RETURNING" in sql:
                rows = []
                for uid in uuids:
                    if uid not in self.fail_uuids:
                        sid = self._next_sample_id
                        self._next_sample_id += 1
                        self.inserted_samples[uid] = sid
                        rows.append((sid, uid))
                return FakeResultProxy(rows=rows)
            else:
                # Fallback path: INSERT without RETURNING
                for uid in uuids:
                    if uid not in self.fail_uuids:
                        sid = self._next_sample_id
                        self._next_sample_id += 1
                        self.inserted_samples[uid] = sid
                return FakeResultProxy()

        # ── policies INSERT ──
        if "INSERT INTO policies" in sql:
            # Count how many value sets by counting occurrences of ":name"
            # Actually the values clause repeats "(:name, :access_type, NOW(), NOW())" N times
            count = sql.count("(:name")
            ids = []
            for _ in range(count):
                pid = self._next_policy_id
                self._next_policy_id += 1
                self.inserted_policies.append(pid)
                ids.append(pid)
            if "RETURNING" in sql:
                return FakeResultProxy(rows=[(pid,) for pid in ids])
            return FakeResultProxy()

        # ── LAST_INSERT_ID ──
        if "LAST_INSERT_ID" in sql:
            if self.inserted_policies:
                first_id = self.inserted_policies[-1]
                return FakeResultProxy(rows=[(first_id,)])
            return FakeResultProxy(rows=[(None,)])

        # ── projects_samples SELECT ──
        if "FROM projects_samples" in sql and "SELECT" in sql:
            return FakeResultProxy(rows=[])
        if "INSERT INTO projects_samples" in sql:
            return FakeResultProxy()

        # ── assay_assets ──
        if "FROM assay_assets" in sql:
            return FakeResultProxy(rows=[])
        if "INSERT INTO assay_assets" in sql:
            return FakeResultProxy()

        # ── permissions ──
        if "FROM permissions" in sql:
            return FakeResultProxy(rows=[])
        if "INSERT INTO permissions" in sql:
            return FakeResultProxy()

        # ── policies DELETE ──
        if "DELETE FROM policies" in sql:
            return FakeResultProxy()

        # ── advisory locks (UID_GEN) ──
        if "GET_LOCK" in sql:
            return FakeResultProxy(scalar_val=1)
        if "RELEASE_LOCK" in sql:
            return FakeResultProxy(scalar_val=1)

        # ── UID_GEN: max index query ──
        if "COALESCE(MAX(" in sql:
            return FakeResultProxy(scalar_val=0)

        # ── UID_GEN: bulk resolve from DB ──
        if "SELECT uuid, title FROM samples WHERE title IN" in sql:
            return FakeResultProxy(rows=[])

        # Default: return empty
        return FakeResultProxy()


class FakeConnection:
    """Mock SQLAlchemy connection that routes queries through FakeDB."""

    def __init__(self, db: FakeDB):
        self._db = db

    def execute(self, sql_obj, params=None):
        if params is None:
            params = {}
        sql_text = str(sql_obj) if not isinstance(sql_obj, str) else sql_obj
        return self._db.route(sql_text, params)

    def begin(self):
        return FakeTransaction()


class FakeTransaction:
    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_row(uid: str, parent_uid: str | None = None, sample_type: str = "NHP_blood") -> InputRowModel:
    """Build an InputRowModel with optional parent."""
    if parent_uid:
        meta = f'{{"Parent":"{parent_uid}"}}'
    else:
        meta = "{}"
    return InputRowModel(UID=uid, SampleType=sample_type, json_metadata=meta, assay_ids=[])


def _run_orchestrator(
    rows: List[InputRowModel],
    db: FakeDB,
    config: BatchUploadConfig | None = None,
) -> Dict:
    """Run the full orchestrator with a FakeDB.

    Patches: get_connection, get_engine, merge_files (CONVERT stage), Neo4j, checkpoint I/O,
    write_summary_csv, Django settings.
    """
    if config is None:
        config = BatchUploadConfig()

    def _mock_merge_files(paths):
        return ConvertedBatch(rows=rows, source_files=paths or ["dummy"])

    @contextmanager
    def fake_conn():
        yield FakeConnection(db)

    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path = os.path.join(tmpdir, "test.xlsx")
        # Create a dummy file so path exists
        with open(xlsx_path, "w") as f:
            f.write("")

        # Wrap process_batches to inject fake conn_factory
        # (default param captures the original function at definition time)
        @wraps(_real_process_batches)
        def _wrapped_process_batches(*args, **kwargs):
            kwargs.setdefault("conn_factory", fake_conn)
            return _real_process_batches(*args, **kwargs)

        patches = {
            # DB connection — must patch in every module that imports it
            "nextseek_api.batch_upload.orchestrator.get_connection": fake_conn,
            "nextseek_api.batch_upload.insert.get_connection": fake_conn,
            "nextseek_api.batch_upload.parallel.get_connection": fake_conn,
            "nextseek_api.batch_upload.db_engine.get_engine": MagicMock(),
            # process_batches wrapper to inject conn_factory
            "nextseek_api.batch_upload.orchestrator.process_batches": _wrapped_process_batches,
            # CONVERT stage: return our test rows (run_batch_upload_multi uses merge_files)
            "nextseek_api.batch_upload.orchestrator.merge_files": _mock_merge_files,
            # NEO4J: disabled
            "nextseek_api.batch_upload.orchestrator.Neo4jConfig.from_django_settings": MagicMock(
                return_value=MagicMock(NEO4J_UPLOAD_ENABLED=False, MISSING_KEYS=["all"]),
            ),
            # Checkpoint: no-op
            "nextseek_api.batch_upload.insert.write_checkpoint": MagicMock(),
            "nextseek_api.batch_upload.insert.determine_resume_uid": MagicMock(return_value=None),
            # Report CSV: write to tmpdir
            "nextseek_api.batch_upload.orchestrator.write_summary_csv": MagicMock(),
            # Django settings
            "django.conf.settings": MagicMock(MEDIA_ROOT=tmpdir),
            # Prefetch clear_caches: no-op
            "nextseek_api.batch_upload.insert.clear_caches": MagicMock(),
            # psutil for memory check
            "nextseek_api.batch_upload.insert.check_memory_limit": MagicMock(),
        }

        # Apply all patches
        stack = []
        for target, mock_val in patches.items():
            p = patch(target, mock_val)
            stack.append(p)
            p.start()

        try:
            result = run_batch_upload(
                xlsx_path=xlsx_path,
                project_id=1,
                contributor_id=1,
                lababbv="TST",
                config=config,
                output_dir=tmpdir,
            )
        finally:
            for p in stack:
                p.stop()

    return result


# ── Tests ───────────────────────────────────────────────────────────────


class TestOrchestratorLevels:
    """Integration tests exercising the full pipeline with DB-level mocking."""

    # Realistic UIDs matching the UID regex pattern
    UID_A = "NHP-260101TST-1"
    UID_B = "NHP-260101TST-2"
    UID_C = "NHP-260101TST-3"
    UID_X = "NHP-260101TST-10"
    UID_Y = "NHP-260101TST-11"

    def test_flat_upload_no_parents(self):
        """All roots, no parent references -> single level 0, all succeed."""
        rows = [_make_row(self.UID_A), _make_row(self.UID_B), _make_row(self.UID_C)]
        db = FakeDB()

        result = _run_orchestrator(rows, db)

        assert result["totals"]["success"] == 3
        assert result["totals"]["failed"] == 0
        assert result["totals"]["skipped"] == 0
        assert set(db.inserted_samples.keys()) == {self.UID_A, self.UID_B, self.UID_C}

    def test_parent_child_ordering(self):
        """Parent at level 0, child at level 1 -> two separate INSERT calls."""
        rows = [_make_row(self.UID_A), _make_row(self.UID_B, parent_uid=self.UID_A)]
        db = FakeDB()

        result = _run_orchestrator(rows, db)

        assert result["totals"]["success"] == 2
        assert result["totals"]["failed"] == 0
        # Both inserted
        assert self.UID_A in db.inserted_samples
        assert self.UID_B in db.inserted_samples
        # A was inserted before B (separate INSERT calls per level)
        assert len(db.insert_sample_calls) >= 2
        # First call should contain A
        all_first_call_uuids = db.insert_sample_calls[0]
        assert self.UID_A in all_first_call_uuids
        # Find B in a later call
        later_uuids = []
        for call in db.insert_sample_calls[1:]:
            later_uuids.extend(call)
        assert self.UID_B in later_uuids

    def test_cascade_failure_prevents_child_insert(self):
        """Parent fails at level 0 -> child never reaches INSERT, gets cascade failure."""
        rows = [_make_row(self.UID_A), _make_row(self.UID_B, parent_uid=self.UID_A)]
        db = FakeDB(fail_uuids={self.UID_A})

        result = _run_orchestrator(rows, db)

        # A failed (no id returned from INSERT)
        assert result["totals"]["failed"] == 2  # A + B
        assert result["totals"]["success"] == 0
        # B was never actually inserted into the DB
        assert self.UID_B not in db.inserted_samples
        assert self.UID_A not in db.inserted_samples
        # Check error messages mention cascade
        error_messages = [e["message"] for e in result["errors"]]
        cascade_msgs = [m for m in error_messages if f"parent {self.UID_A} failed to insert" in m]
        assert len(cascade_msgs) >= 1

    def test_duplicate_detection_via_preexisting(self):
        """Spreadsheet UID already in DB -> skipped as duplicate."""
        rows = [_make_row(self.UID_A), _make_row(self.UID_B)]
        db = FakeDB(existing_samples={self.UID_A: 42})

        result = _run_orchestrator(rows, db)

        # A is skipped (duplicate), B is inserted
        assert result["totals"]["skipped"] == 1
        assert result["totals"]["success"] == 1
        assert self.UID_A not in db.inserted_samples  # A was NOT re-inserted
        assert self.UID_B in db.inserted_samples

    def test_cycle_handling(self):
        """Cycle UIDs X<->Y: normal sample A succeeds, cycle members fail."""
        rows = [
            _make_row(self.UID_A),
            _make_row(self.UID_X, parent_uid=self.UID_Y),
            _make_row(self.UID_Y, parent_uid=self.UID_X),
        ]
        db = FakeDB()

        result = _run_orchestrator(rows, db)

        # A succeeds
        assert self.UID_A in db.inserted_samples
        # X and Y are in a cycle — neither parent is resolvable
        assert result["totals"]["success"] >= 1  # at least A
        # X and Y should be failed with cycle error
        error_messages = [e["message"] for e in result["errors"]]
        cycle_msgs = [m for m in error_messages if "cycle" in m.lower()]
        assert len(cycle_msgs) >= 2  # one for X, one for Y


