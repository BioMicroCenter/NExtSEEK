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
from nextseek_api.batch_upload.prefetch import clear_caches as _clear_prefetch_caches


@pytest.fixture(autouse=True)
def _isolate_prefetch_caches():
    """Module-level prefetch caches survive between tests and across files;
    contamination from other tests can make this file's tests flaky. Clear
    before and after each test in this module."""
    _clear_prefetch_caches()
    yield
    _clear_prefetch_caches()


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
        sample_attributes: Dict[int, Set[str]] | None = None,
    ):
        self.sample_types = sample_types or {"NHP_blood": 1}
        self.existing_samples = dict(existing_samples or {})
        self.fail_uuids = fail_uuids or set()
        # Per-sample_type allowed attribute titles (PR #2 attribute-name check).
        # Default covers keys produced by _make_row (Parent only).
        self.sample_attributes = sample_attributes or {1: {"Parent"}}

        # Auto-increment counters
        self._next_sample_id = max(self.existing_samples.values(), default=0) + 1
        self._next_policy_id = 1

        # State tracking
        self.inserted_samples: Dict[str, int] = {}  # uuid -> id
        self.inserted_policies: List[int] = []
        self.projects_samples: Set[Tuple[int, int]] = set()  # (pid, sid)
        self.assay_assets: Set[Tuple[int, int]] = set()  # (assay_id, asset_id)
        # Stage 5's graph_sync outbox rows, written on this same connection
        self.graph_sync_outbox: List[Dict[str, Any]] = []

        # Track call order for assertions
        self.insert_sample_calls: List[List[str]] = []  # list of uuid lists per INSERT

    def route(self, sql_text: str, params: Dict[str, Any]) -> FakeResultProxy:
        """Route a SQL query to the appropriate handler."""
        sql = sql_text.strip()

        # ── the graph_sync outbox (stage 5, by qualified name) ──
        if "graph_sync_outbox" in sql and "INSERT" in sql:
            self.graph_sync_outbox.append({
                "kind": params.get("kind"), "key": params.get("key"),
                "payload": params.get("payload"), "done_at": None,
            })
            return FakeResultProxy()

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

        # ── sample_attributes (PR #2 attribute-name check) ──
        if "FROM sample_attributes" in sql and "SELECT" in sql:
            id_params = [v for k, v in params.items() if k.startswith("id_")]
            rows = []
            for st_id in id_params:
                for title in self.sample_attributes.get(st_id, set()):
                    rows.append((st_id, title))
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

    def begin_nested(self):
        """The savepoint stage 5's outbox row is written to."""
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
    neo4j_only: bool = False,
    should_stop=None,
    row_errors=None,
) -> Dict:
    """Run the full orchestrator with a FakeDB.

    Patches: get_connection, get_engine, merge_files (CONVERT stage), Neo4j, checkpoint I/O,
    write_summary_csv, Django settings.
    """
    if config is None:
        config = BatchUploadConfig()

    def _mock_merge_files(paths):
        extra = {"row_errors": row_errors} if row_errors else {}
        return ConvertedBatch(rows=rows, source_files=paths or ["dummy"], **extra)

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
                neo4j_only=neo4j_only,
                should_stop=should_stop,
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


class TestNeo4jOnlyMode:
    """Integration tests for neo4j_only mode: skip INSERT, resolve from DB."""

    UID_A = "NHP-260101TST-1"
    UID_B = "NHP-260101TST-2"
    UID_C = "NHP-260101TST-3"

    def test_neo4j_only_skips_insert(self):
        """Pre-existing samples: all resolve, no INSERT calls."""
        rows = [_make_row(self.UID_A), _make_row(self.UID_B), _make_row(self.UID_C)]
        db = FakeDB(existing_samples={self.UID_A: 10, self.UID_B: 20, self.UID_C: 30})

        result = _run_orchestrator(rows, db, neo4j_only=True)

        assert result["totals"]["success"] == 3
        assert result["totals"]["failed"] == 0
        # No INSERT calls — neo4j_only bypasses INSERT stage
        assert db.insert_sample_calls == []
        assert db.inserted_samples == {}

    def test_neo4j_only_rejects_rows_without_uids(self):
        """Rows without UIDs are rejected with error, rows with UIDs succeed."""
        row_with_uid = _make_row(self.UID_A)
        row_without_uid = InputRowModel(
            UID=None, SampleType="NHP_blood", json_metadata="{}", assay_ids=[],
        )
        db = FakeDB(existing_samples={self.UID_A: 10})

        result = _run_orchestrator([row_with_uid, row_without_uid], db, neo4j_only=True)

        assert result["totals"]["success"] == 1
        error_messages = [e["message"] for e in result["errors"]]
        uid_msgs = [m for m in error_messages if "neo4j_only mode requires pre-assigned UID" in m]
        assert len(uid_msgs) >= 1

    def test_neo4j_only_marks_missing_uids_as_failed(self):
        """UID not in DB is marked failed with uid_not_found_in_db."""
        rows = [_make_row(self.UID_A), _make_row(self.UID_B)]
        db = FakeDB(existing_samples={self.UID_A: 10})  # UID_B missing

        result = _run_orchestrator(rows, db, neo4j_only=True)

        assert result["totals"]["success"] == 1
        assert result["totals"]["failed"] == 1
        error_messages = [e["message"] for e in result["errors"]]
        missing_msgs = [m for m in error_messages if "uid_not_found_in_db" in m]
        assert len(missing_msgs) >= 1


class TestTransformAttributeNameCheck:
    """End-to-end check that TRANSFORM-stage AttributeNameError flows into
    one error_collector entry per bad key (not a single joined message)."""

    UID_A = "NHP-260101TST-1"

    def test_attribute_name_check_emits_one_error_per_bad_key(self):
        """A row whose json_metadata has N keys outside the SampleType's
        sample_attributes set produces N error_collector entries of type
        VALIDATION_ATTRIBUTE_NAME, not a single joined error."""
        # FakeDB default allows only "Parent" for sample_type_id 1 — so
        # "Subjet ID" and "Heigth" will both be flagged.
        row = InputRowModel(
            UID=self.UID_A,
            SampleType="NHP_blood",
            json_metadata='{"Subjet ID": "S1", "Heigth": "180"}',
            assay_ids=[],
        )
        db = FakeDB()

        result = _run_orchestrator([row], db)

        attr_name_errors = [
            e for e in result["errors"] if e["type"] == "VALIDATION_ATTRIBUTE_NAME"
        ]
        assert len(attr_name_errors) == 2
        messages = [e["message"] for e in attr_name_errors]
        assert any("Subjet ID" in m for m in messages)
        assert any("Heigth" in m for m in messages)
        # All messages should reference the SampleType
        assert all("NHP_blood" in m for m in messages)
        # Row should not have been inserted
        assert self.UID_A not in db.inserted_samples


class TestFailedRowsAreReported:
    """A failed row fails its children and is counted; totals always add up."""

    A = "NHP-260101TST-1"
    B = "NHP-260101TST-2"
    C = "NHP-260101TST-3"
    X = "NHP-260101TST-10"
    Y = "NHP-260101TST-11"
    Z = "NHP-260101TST-12"

    @staticmethod
    def _bad(uid):
        return InputRowModel(
            UID=uid, SampleType="NHP_blood",
            json_metadata='{"Subjet ID": "x"}', assay_ids=[],
        )

    @staticmethod
    def _totals_add_up(t, result):
        # counted independently of the totals: outcomes via the error entries and the known inputs
        assert t["processed"] == 4  # A, B, C and the dropped row
        assert t["failed"] == 3     # A (transform), B (child of A), the dropped row
        assert t["success"] + t["skipped"] == 1  # C

    def test_transform_failure_fails_its_children(self):
        db = FakeDB()
        result = _run_orchestrator([self._bad(self.A), _make_row(self.B, parent_uid=self.A)], db)

        assert self.B not in db.inserted_samples
        assert result["totals"]["failed"] == 2
        assert result["totals"]["processed"] == 2
        assert any(f"parent {self.A} failed to insert" in e["message"] for e in result["errors"])

    def test_child_of_unresolvable_cycle_is_not_inserted(self):
        rows = [
            _make_row(self.X, parent_uid=self.Y),
            _make_row(self.Y, parent_uid=self.X),
            _make_row(self.Z, parent_uid=self.X),
        ]
        db = FakeDB()
        result = _run_orchestrator(rows, db)

        assert self.Z not in db.inserted_samples
        assert result["totals"]["failed"] == 3
        assert any(
            e["type"] == "PARENT_FAILED" and e["message"].startswith("parent ")
            for e in result["errors"]
        )
        assert sum(e["type"] == "CYCLE_UNRESOLVABLE" for e in result["errors"]) == 2

    def test_chain_below_preexisting_cycle_is_inserted_in_order(self):
        """X<->Y already in the DB: Z (child of X) and C (child of Z) go in, in order."""
        rows = [
            _make_row(self.X, parent_uid=self.Y),
            _make_row(self.Y, parent_uid=self.X),
            _make_row(self.Z, parent_uid=self.X),
            _make_row(self.C, parent_uid=self.Z),
        ]
        db = FakeDB(existing_samples={self.X: 50, self.Y: 51})
        result = _run_orchestrator(rows, db)

        assert self.Z in db.inserted_samples and self.C in db.inserted_samples
        flat = [u for call in db.insert_sample_calls for u in call]
        assert flat.index(self.Z) < flat.index(self.C)
        assert result["totals"]["failed"] == 0

    def test_dropped_row_message_carries_its_row_number(self):
        result = _run_orchestrator([_make_row(self.A)], FakeDB(), row_errors=[(4, ["bad cell"])])
        msgs = [e["message"] for e in result["errors"] if "bad cell" in e["message"]]
        assert msgs and "Row 4" in msgs[0]

    def test_child_of_failed_row_already_in_db_is_still_inserted(self):
        db = FakeDB(existing_samples={self.A: 50})
        result = _run_orchestrator([self._bad(self.A), _make_row(self.B, parent_uid=self.A)], db)
        assert self.B in db.inserted_samples
        assert result["totals"]["failed"] == 1

    def test_cycle_child_with_orphan_parent_is_inserted_and_warned(self):
        O = "NHP-260101TST-99"  # in neither the batch nor the DB
        two = InputRowModel(
            UID=self.Z, SampleType="NHP_blood", assay_ids=[],
            json_metadata=f'{{"Parent":"{self.X}","Parent Sample":"{O}"}}',
        )
        db = FakeDB(existing_samples={self.X: 50, self.Y: 51},
                    sample_attributes={1: {"Parent", "Parent Sample"}})
        result = _run_orchestrator(
            [_make_row(self.X, parent_uid=self.Y), _make_row(self.Y, parent_uid=self.X), two], db)
        assert self.Z in db.inserted_samples
        assert any("treating as root sample" in e["message"] for e in result["errors"])

    def test_failed_existing_row_in_a_cycle_gets_one_error(self):
        rows = [
            InputRowModel(UID=self.X, SampleType="NHP_blood", assay_ids=[],
                          json_metadata=f'{{"Subjet ID": "x", "Parent": "{self.Y}"}}'),
            _make_row(self.Y, parent_uid=self.X),
        ]
        db = FakeDB(existing_samples={self.X: 50, self.Y: 51})
        result = _run_orchestrator(rows, db)
        assert not [e for e in result["errors"] if e["type"] in ("CYCLE_UNRESOLVABLE", "PARENT_FAILED")]
        assert sum(e["type"] == "VALIDATION_ATTRIBUTE_NAME" for e in result["errors"]) == 1

    def test_cancel_inside_the_cycle_pass_marks_nothing_failed(self):
        rows = [
            _make_row(self.X, parent_uid=self.Y),
            _make_row(self.Y, parent_uid=self.X),
            _make_row(self.Z, parent_uid=self.X),
            _make_row(self.C, parent_uid=self.Z),
            _make_row("NHP-260101TST-13", parent_uid=self.C),
        ]
        db = FakeDB(existing_samples={self.X: 50, self.Y: 51})
        seen = {}

        def _capture(*a, **kw):
            seen["ec"] = kw.get("error_collector")
            return {"totals": {}, "errors": []}

        with patch("nextseek_api.batch_upload.orchestrator._cancelled_result", side_effect=_capture):
            _run_orchestrator(rows, db, should_stop=lambda: self.Z in db.inserted_samples)
        assert self.Z in db.inserted_samples and self.C not in db.inserted_samples
        assert not [e for e in seen["ec"].all_errors() if e.error_type.value == "PARENT_FAILED"]

    def test_flat_row_failure_is_reported(self):
        db = FakeDB()
        result = _run_orchestrator(
            [_make_row(self.A)], db,
            row_errors=[(1, ["UID mismatch with json_metadata"])],
        )
        assert result["totals"]["failed"] == 1
        assert result["totals"]["processed"] == 2
        assert any(e["type"] == "VALIDATION_JSON" and "UID mismatch" in e["message"] for e in result["errors"])

    @pytest.mark.parametrize("neo4j_only", [False, True])
    def test_totals_add_up_on_a_mixed_run(self, neo4j_only):
        rows = [
            self._bad(self.A), _make_row(self.B, parent_uid=self.A),
            _make_row(self.C),
        ]
        db = FakeDB(existing_samples={self.C: 9} if neo4j_only else {})
        result = _run_orchestrator(rows, db, neo4j_only=neo4j_only, row_errors=[(7, ["bad row"])])
        t = result["totals"]
        self._totals_add_up(t, result)
        assert any("bad row" in e["message"] for e in result["errors"])
        assert any(f"parent {self.A} failed to insert" in e["message"] or "uid_not_found_in_db" in e["message"]
                   for e in result["errors"])
