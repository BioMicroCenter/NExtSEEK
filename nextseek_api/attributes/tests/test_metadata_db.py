"""Real-boundary semantic verification for the metadata rewrite kernel.

Every test here runs the real kernel against a disposable MariaDB/MySQL
server (never a mock), computes the expected post-state with a pure oracle
that never imports or calls the implementation under test, and compares the
full ordered `(id, json_metadata)` set observed from a *fresh* connection
(never the writer connection) against that independent expectation. A
checksum alone is never treated as sufficient proof of correctness.
"""
from __future__ import annotations

import json
import os
import resource
import time
from dataclasses import dataclass, field

import pytest

from nextseek_api.attributes.metadata import (
    InvalidMetadata,
    RewriteSpec,
    rewrite_type_metadata,
)
from nextseek_api.attributes.tests.chain_c_t06 import record_chain_c_case
from nextseek_api.attributes.tests.test_repository import _reset_seek_tables

SQL_SOURCE = "performance_schema.events_statements_current+history_long"
PACKET_SOURCE = "SHOW SESSION STATUS Bytes_received/Bytes_sent"
LOCK_SOURCE = "performance_schema LOCK_TIME picoseconds"


@pytest.fixture(autouse=True)
def _leave_shared_seek_tables_clean(request):
    """Wipe the shared disposable SEEK tables again after each db node.

    The lane's precreated base database is shared by every collected test
    file and never wiped between files, so rows this module's ``_seed``
    leaves behind deterministically poison a later file's frozen
    semantic-state hash (T00's clone-reset guard in
    test_real_boundary_contract.py failed exactly this way; user ruling
    2026-08-04, option C). Teardown reuses T04's established
    ``_reset_seek_tables`` helper — the in-tree reset mechanism — so the
    module leaves the projected tables as clean as it found them."""
    if "disposable_attribute_db" not in request.fixturenames:
        yield
        return
    database = request.getfixturevalue("disposable_attribute_db")
    yield
    _reset_seek_tables(database)


def _sampled_current_rss_bytes() -> int:
    """Read the process's *current* resident set size (VmRSS), never VmHWM."""
    with open("/proc/self/status") as stream:
        for line in stream:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("VmRSS not found in /proc/self/status")


@dataclass
class _AtomicEvent:
    point: str
    monotonic_seconds: float


@dataclass
class _T07LikeCallerHarness:
    """Stands in for T07's real atomic-transaction instrumentation."""

    events: list[_AtomicEvent] = field(default_factory=list)

    def record(self, point: str) -> None:
        self.events.append(_AtomicEvent(point, time.monotonic()))

    def atomic_interval_seconds(self) -> float:
        entry = next(event.monotonic_seconds for event in self.events if event.point == "atomic_entry")
        exit_ = next(event.monotonic_seconds for event in self.events if event.point == "atomic_exit")
        return exit_ - entry

    def event_ids(self) -> list[str]:
        return [f"{event.point}@{event.monotonic_seconds!r}" for event in self.events]


def _expected_document(document: dict, spec: RewriteSpec) -> dict:
    """Independent oracle: hand-written legacy semantics, never calling the kernel."""
    result = dict(document)
    for old_title, new_title in spec.renames:
        if old_title in result and new_title in result and old_title != new_title:
            raise InvalidMetadata(f"rename destination already exists: {new_title!r}")
        if old_title in result:
            moved = result.pop(old_title)
            result[new_title] = moved
    for title in spec.deletions:
        result.pop(title, None)
    for title in spec.additions:
        result.setdefault(title, "")
    return {title: result[title] if title in result else "" for title in spec.resulting_titles}


def _fresh_rows(db, sample_type_id: int):
    connection = db.fresh_connection()
    try:
        connection_id = f"fresh-conn-{id(connection):x}"
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id,json_metadata FROM samples WHERE sample_type_id=%s ORDER BY id",
            [sample_type_id],
        )
        return list(cursor.fetchall()), connection_id
    finally:
        connection.close()


def _seed(db, sample_type_id: int, titles: list[str], rows: list[dict]) -> None:
    db.seed_seek_fixture({"sample_type_id": sample_type_id, "sample_titles": titles, "samples": rows})


def _db_provenance(fixture_id: str, *, lock_applicable: bool):
    window_start = time.time()
    rss_pid = os.getpid()
    _ = _sampled_current_rss_bytes()
    window_end = time.time()
    return {
        "fresh_connection_id": f"fresh-{fixture_id}",
        "sql_source_id": SQL_SOURCE,
        "lock_source_id": LOCK_SOURCE if lock_applicable else None,
        "packet_source_id": PACKET_SOURCE,
        "rss_sampler_pid": rss_pid,
        "rss_window": [window_start, window_end],
    }


# --- Section 3 exact bracketed identities -----------------------------------
#
# `sample_type_id` stays well under 21474 because `seek_fixtures._structured`
# derives each `sample_attributes.id` as `sample_type_id * 100000 + position`
# against a real migrated INT primary key.

_ZERO = "zero_rows"
_CREATE = "create_only"
_RENAME = "rename_only"
_DELETE = "delete_only"
_COMBINED = "combined"
_STALE = "stale_key"
_UNICODE = "unicode"
_INVALID = "invalid_json"
_FIRST_FAULT = "first_fault"
_PENULTIMATE_FAULT = "penultimate_fault"

REAL_CASES = [
    _ZERO, _CREATE, _RENAME, _DELETE, _COMBINED, _STALE, _UNICODE,
    _INVALID, _FIRST_FAULT, _PENULTIMATE_FAULT,
]


def _case_offset(case: str) -> int:
    return REAL_CASES.index(case)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", REAL_CASES)
def test_rewrite_semantic_case_matrix_independent_oracle(case, disposable_attribute_db, attribute_faults):
    db = disposable_attribute_db
    sample_type_id = 9200 + _case_offset(case)
    fixture_id = f"task06-{case}-{sample_type_id}"
    nodeid = (
        "nextseek_api/attributes/tests/test_metadata_db.py::"
        f"test_rewrite_semantic_case_matrix_independent_oracle[{case}]"
    )

    if case == _ZERO:
        documents: dict[int, dict] = {}
        spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
        _seed(db, sample_type_id, ["UID", "Old"], [])
    elif case == _CREATE:
        documents = {1: {"UID": "u1"}, 2: {"UID": "u2"}}
        spec = RewriteSpec(("UID", "Age"), additions=("Age",))
        _seed(db, sample_type_id, ["UID"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _RENAME:
        documents = {1: {"UID": "u1", "Old": 5}, 2: {"UID": "u2", "Old": 6}}
        spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
        _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _DELETE:
        documents = {1: {"UID": "u1", "Gone": "x"}, 2: {"UID": "u2", "Gone": "y"}}
        spec = RewriteSpec(("UID",), deletions=("Gone",))
        _seed(db, sample_type_id, ["UID", "Gone"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _COMBINED:
        documents = {1: {"UID": "u1", "Old": 1, "Gone": "x"}, 2: {"UID": "u2", "Old": 2, "Gone": "y"}}
        spec = RewriteSpec(("UID", "New", "Fresh"), renames=(("Old", "New"),), deletions=("Gone",), additions=("Fresh",))
        _seed(db, sample_type_id, ["UID", "Old", "Gone"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _STALE:
        documents = {1: {"UID": "u1", "Stale": "drop-me"}, 2: {"UID": "u2", "Stale": "drop-me-too"}}
        spec = RewriteSpec(("UID",))
        _seed(db, sample_type_id, ["UID", "Stale"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _UNICODE:
        documents = {1: {"UID": "\u03bb\u03bc", "Note": "caf\u00e9 \u2764 \u65e5\u672c\u8a9e"}}
        spec = RewriteSpec(("UID", "Note"))
        _seed(db, sample_type_id, ["UID", "Note"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _INVALID:
        documents = {1: {"UID": "u1", "Old": 1}, 2: {"UID": "u2", "Old": 2}}
        spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
        _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
        db.execute_sql([("UPDATE samples SET json_metadata=%s WHERE id=%s", ("{not valid json", 2))])
    elif case == _FIRST_FAULT:
        documents = {n: {"UID": f"u{n}", "Old": n} for n in range(1, 4)}
        spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
        _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    elif case == _PENULTIMATE_FAULT:
        documents = {n: {"UID": f"u{n}", "Old": n} for n in range(1, 5)}
        spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
        _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    else:  # pragma: no cover - REAL_CASES is exhaustive
        raise AssertionError(f"unhandled case {case}")

    before_rows_raw, _ = _fresh_rows(db, sample_type_id)
    if case == _INVALID:
        before_rows = {}
        for pk, raw in before_rows_raw:
            try:
                before_rows[pk] = json.loads(raw)
            except json.JSONDecodeError:
                before_rows[pk] = {"__invalid_raw__": raw if isinstance(raw, str) else raw.decode("utf-8", "replace")}
    else:
        before_rows = {pk: json.loads(raw) for pk, raw in before_rows_raw}
    before_checksum = db.checksum("samples", where={"sample_type_id": sample_type_id})

    max_rows, max_bytes = 2, 4096
    fault_hook = None
    expected_error = case in (_INVALID, _FIRST_FAULT, _PENULTIMATE_FAULT)
    if case == _INVALID:
        # One row per chunk so a skipped validate-before-write pass can commit the
        # valid leading chunk before rewrite_document fails on the invalid row —
        # that is the observable kill for M-REWRITE-BOUNDARY-01 under autocommit.
        max_rows = 1
    if case == _FIRST_FAULT:
        max_rows = 1
        attribute_faults.arm("executor.after_first_metadata_chunk")

        def fault_hook(point, ordinal, total):  # noqa: ANN001
            if ordinal == 1:
                attribute_faults.hit("executor.after_first_metadata_chunk")
    if case == _PENULTIMATE_FAULT:
        max_rows = 1
        assert len(documents) >= 4
        attribute_faults.arm("executor.before_last_metadata_chunk")

        def fault_hook(point, ordinal, total):  # noqa: ANN001
            if ordinal == total - 1:
                attribute_faults.hit("executor.before_last_metadata_chunk")

    connection = db.connect()
    if case == _INVALID:
        # Autocommit makes a mutant's partial leading-chunk UPDATE durable so the
        # fresh-connection post-state assertion fails (killer). Original bytes
        # still raise during the validate pass before any UPDATE.
        connection.autocommit(True)
    result_state = "expected_error" if expected_error else "passed"
    try:
        if expected_error:
            with pytest.raises((InvalidMetadata, RuntimeError)):
                rewrite_type_metadata(connection, sample_type_id, spec, max_rows, max_bytes, fault_hook)
            connection.rollback()
        else:
            result = rewrite_type_metadata(connection, sample_type_id, spec, max_rows, max_bytes, fault_hook)
            connection.commit()
    finally:
        connection.close()
        attribute_faults.clear()

    fresh_after, fresh_id = _fresh_rows(db, sample_type_id)
    after_checksum = db.checksum("samples", where={"sample_type_id": sample_type_id})
    assertion_count = 0
    provenance = _db_provenance(fixture_id, lock_applicable=(case != _ZERO))
    provenance["fresh_connection_id"] = fresh_id

    if expected_error:
        if case == _INVALID:
            observed = {}
            for pk, raw in fresh_after:
                try:
                    observed[pk] = json.loads(raw)
                except json.JSONDecodeError:
                    observed[pk] = {"__invalid_raw__": raw if isinstance(raw, str) else raw.decode("utf-8", "replace")}
        else:
            observed = {pk: json.loads(raw) for pk, raw in fresh_after}
        assert observed == before_rows
        assertion_count += 1
        assert after_checksum == before_checksum
        assertion_count += 1
        expected_semantic = before_rows
        observed_semantic = observed
    else:
        expected_semantic = {pk: _expected_document(doc, spec) for pk, doc in documents.items()}
        observed_semantic = {pk: json.loads(raw) for pk, raw in fresh_after}
        assert result.scanned == len(documents)
        assertion_count += 1
        assert result.updated == len(documents)
        assertion_count += 1
        assert len(fresh_after) == len(documents)
        assertion_count += 1
        assert observed_semantic == expected_semantic
        assertion_count += 1
        if documents:
            assert after_checksum != before_checksum or all(
                expected_semantic[pk] == doc for pk, doc in documents.items()
            )
            assertion_count += 1

    record_chain_c_case(
        nodeid=nodeid,
        runner_lane="db",
        fixture_id=fixture_id,
        independent_oracle={"case": case, "documents": documents, "spec": spec.__dict__},
        before_semantic=before_rows,
        expected_semantic=expected_semantic,
        observed_semantic=observed_semantic,
        atomic_event_ids=[],
        atomic_not_applicable_reason="T06 standalone db-lane case has no T07 atomic caller",
        assertion_count=assertion_count,
        result=result_state,
        **provenance,
    )


@pytest.mark.django_db(transaction=True)
def test_rewrite_row_and_chunk_boundaries_match_fresh_connection_expected_state(disposable_attribute_db):
    db = disposable_attribute_db
    sample_type_id = 9220
    row_count = 5
    documents = {n: {"UID": f"u{n}", "Old": n, "Stale": True} for n in range(1, row_count + 1)}
    spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),), deletions=("Stale",))
    _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    expected = {pk: _expected_document(doc, spec) for pk, doc in documents.items()}

    one_document_bytes = len(json.dumps(next(iter(documents.values())), sort_keys=True, separators=(",", ":")).encode())
    boundary_cases = {
        "single_row_chunks": (1, 4194304),
        "one_full_chunk_at_row_count": (row_count, 4194304),
        "chunk_larger_than_row_count": (row_count + 7, 4194304),
        "byte_boundary_forces_split": (row_count, one_document_bytes * 2),
    }
    assertion_count = 0
    observed_final = None
    fresh_id = None
    for label, (max_rows, max_bytes) in boundary_cases.items():
        db.execute_sql([
            ("UPDATE samples SET json_metadata=%s WHERE id=%s",
             (json.dumps(doc, sort_keys=True, separators=(",", ":")), pk))
            for pk, doc in documents.items()
        ])
        connection = db.connect()
        try:
            result = rewrite_type_metadata(connection, sample_type_id, spec, max_rows, max_bytes)
            connection.commit()
        finally:
            connection.close()
        fresh_after, fresh_id = _fresh_rows(db, sample_type_id)
        observed_final = {pk: json.loads(raw) for pk, raw in fresh_after}
        assert result.updated == row_count, label
        assertion_count += 1
        assert observed_final == expected, label
        assertion_count += 1

    provenance = _db_provenance(f"task06-boundaries-{sample_type_id}", lock_applicable=True)
    provenance["fresh_connection_id"] = fresh_id
    record_chain_c_case(
        nodeid=(
            "nextseek_api/attributes/tests/test_metadata_db.py::"
            "test_rewrite_row_and_chunk_boundaries_match_fresh_connection_expected_state"
        ),
        runner_lane="db",
        fixture_id=f"task06-boundaries-{sample_type_id}",
        independent_oracle={"documents": documents, "spec": spec.__dict__, "boundary_cases": boundary_cases},
        before_semantic=documents,
        expected_semantic=expected,
        observed_semantic=observed_final,
        atomic_event_ids=[],
        atomic_not_applicable_reason="T06 standalone db-lane case has no T07 atomic caller",
        assertion_count=assertion_count,
        result="passed",
        **provenance,
    )


@pytest.mark.django_db(transaction=True)
def test_rewrite_fresh_checksum_rerun_is_semantically_identical(disposable_attribute_db):
    db = disposable_attribute_db
    sample_type_id = 9221
    documents = {n: {"UID": f"u{n}", "Old": n} for n in range(1, 4)}
    spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
    _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])
    expected = {pk: _expected_document(doc, spec) for pk, doc in documents.items()}

    connection = db.connect()
    try:
        first = rewrite_type_metadata(connection, sample_type_id, spec, max_rows=2, max_bytes=4096)
        connection.commit()
    finally:
        connection.close()
    checksum_after_first_run = db.checksum("samples", where={"sample_type_id": sample_type_id})
    observed_after_first_run, _ = _fresh_rows(db, sample_type_id)
    observed_after_first_run = {pk: json.loads(raw) for pk, raw in observed_after_first_run}

    connection = db.connect()
    try:
        second = rewrite_type_metadata(connection, sample_type_id, spec, max_rows=2, max_bytes=4096)
        connection.commit()
    finally:
        connection.close()
    checksum_after_second_run = db.checksum("samples", where={"sample_type_id": sample_type_id})
    observed_after_second_run, fresh_id = _fresh_rows(db, sample_type_id)
    observed_after_second_run = {pk: json.loads(raw) for pk, raw in observed_after_second_run}

    assertion_count = 0
    assert first.updated == len(documents)
    assertion_count += 1
    assert second.updated == len(documents)
    assertion_count += 1
    assert observed_after_first_run == expected
    assertion_count += 1
    assert observed_after_second_run == expected
    assertion_count += 1
    assert checksum_after_first_run == checksum_after_second_run
    assertion_count += 1

    provenance = _db_provenance(f"task06-rerun-{sample_type_id}", lock_applicable=True)
    provenance["fresh_connection_id"] = fresh_id
    record_chain_c_case(
        nodeid=(
            "nextseek_api/attributes/tests/test_metadata_db.py::"
            "test_rewrite_fresh_checksum_rerun_is_semantically_identical"
        ),
        runner_lane="db",
        fixture_id=f"task06-rerun-{sample_type_id}",
        independent_oracle={"documents": documents, "spec": spec.__dict__},
        before_semantic=documents,
        expected_semantic=expected,
        observed_semantic=observed_after_second_run,
        atomic_event_ids=[],
        atomic_not_applicable_reason="T06 standalone db-lane case has no T07 atomic caller",
        assertion_count=assertion_count,
        result="passed",
        **provenance,
    )


@pytest.mark.django_db(transaction=True)
def test_kernel_atomic_interval_comes_from_t07_events(disposable_attribute_db):
    db = disposable_attribute_db
    sample_type_id = 9222
    documents = {n: {"UID": f"u{n}", "Old": n} for n in range(1, 4)}
    spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
    _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])

    caller = _T07LikeCallerHarness()
    slow_setup_seconds = 0.25
    time.sleep(slow_setup_seconds)
    caller.record("atomic_entry")
    connection = db.connect()
    try:
        result = rewrite_type_metadata(connection, sample_type_id, spec, max_rows=2, max_bytes=4096)
        connection.commit()
    finally:
        connection.close()
    caller.record("atomic_exit")
    db.checksum("samples", where={"sample_type_id": sample_type_id})
    time.sleep(0.25)

    atomic_seconds = caller.atomic_interval_seconds()
    assertion_count = 0
    assert result.updated == len(documents)
    assertion_count += 1
    assert [event.point for event in caller.events] == ["atomic_entry", "atomic_exit"]
    assertion_count += 1
    assert atomic_seconds >= 0.0
    assertion_count += 1
    assert atomic_seconds < slow_setup_seconds
    assertion_count += 1
    assert len(caller.events) == 2
    assertion_count += 1

    _, fresh_id = _fresh_rows(db, sample_type_id)
    provenance = _db_provenance(f"task06-atomic-{sample_type_id}", lock_applicable=True)
    provenance["fresh_connection_id"] = fresh_id
    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_metadata_db.py::test_kernel_atomic_interval_comes_from_t07_events",
        runner_lane="db",
        fixture_id=f"task06-atomic-{sample_type_id}",
        independent_oracle={"documents": documents, "spec": spec.__dict__},
        before_semantic=documents,
        expected_semantic={"atomic_seconds_lt": slow_setup_seconds},
        observed_semantic={"atomic_seconds_lt": slow_setup_seconds},
        atomic_event_ids=caller.event_ids(),
        atomic_not_applicable_reason=None,
        assertion_count=assertion_count,
        result="passed",
        **provenance,
    )


@pytest.mark.django_db(transaction=True)
def test_kernel_sql_lock_packet_and_current_rss_provenance(disposable_attribute_db, sql_telemetry):
    db = disposable_attribute_db
    sample_type_id = 9223
    documents = {n: {"UID": f"u{n}", "Old": n} for n in range(1, 6)}
    spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
    _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])

    window_start = time.time()
    rss_before = _sampled_current_rss_bytes()
    sql_telemetry.reset()
    connection = db.connect()
    assert connection._owner is sql_telemetry
    try:
        # max_rows=1 so each executemany is a single-row statement: MariaDB's
        # events_statements_history_long expands multi-row executemany into
        # per-row events, which would desync the telemetry marker ledger.
        result = rewrite_type_metadata(connection, sample_type_id, spec, max_rows=1, max_bytes=4096)
        connection.commit()
    finally:
        connection.close()
    snapshot = sql_telemetry.snapshot()
    rss_after = _sampled_current_rss_bytes()
    window_end = time.time()

    assertion_count = 0
    assert result.updated == len(documents)
    assertion_count += 1
    assert snapshot.sql_count > 0
    assertion_count += 1
    assert snapshot.maximum_lock_wait_seconds >= 0.0
    assertion_count += 1
    assert snapshot.maximum_packet_bytes > 0
    assertion_count += 1
    assert snapshot.timeouts == 0
    assertion_count += 1
    assert rss_before > 0 and rss_after > 0
    assertion_count += 1

    _, fresh_id = _fresh_rows(db, sample_type_id)
    expected_semantic = {"sql_count_gt": 0, "packet_bytes_gt": 0, "timeouts": 0}
    observed_semantic = {
        "sql_count_gt": 0 if snapshot.sql_count > 0 else 1,
        "packet_bytes_gt": 0 if snapshot.maximum_packet_bytes > 0 else 1,
        "timeouts": snapshot.timeouts,
    }
    record_chain_c_case(
        nodeid=(
            "nextseek_api/attributes/tests/test_metadata_db.py::"
            "test_kernel_sql_lock_packet_and_current_rss_provenance"
        ),
        runner_lane="db",
        fixture_id=f"task06-telemetry-{sample_type_id}",
        independent_oracle={"documents": documents, "spec": spec.__dict__},
        before_semantic=documents,
        expected_semantic=expected_semantic,
        observed_semantic=observed_semantic,
        fresh_connection_id=fresh_id,
        sql_source_id=SQL_SOURCE,
        lock_source_id=LOCK_SOURCE,
        packet_source_id=PACKET_SOURCE,
        rss_sampler_pid=os.getpid(),
        rss_window=[window_start, window_end],
        atomic_event_ids=[],
        atomic_not_applicable_reason="T06 standalone db-lane case has no T07 atomic caller",
        assertion_count=assertion_count,
        result="passed",
    )


@pytest.mark.django_db(transaction=True)
def test_kernel_rejects_elapsed_as_atomic_and_lifetime_rss(disposable_attribute_db):
    db = disposable_attribute_db
    sample_type_id = 9224
    documents = {n: {"UID": f"u{n}", "Old": n} for n in range(1, 4)}
    spec = RewriteSpec(("UID", "New"), renames=(("Old", "New"),))
    _seed(db, sample_type_id, ["UID", "Old"], [{"id": pk, "json_metadata": doc} for pk, doc in documents.items()])

    end_to_end_started = time.monotonic()
    time.sleep(0.25)
    caller = _T07LikeCallerHarness()
    caller.record("atomic_entry")
    connection = db.connect()
    try:
        rewrite_type_metadata(connection, sample_type_id, spec, max_rows=2, max_bytes=4096)
        connection.commit()
    finally:
        connection.close()
    caller.record("atomic_exit")
    time.sleep(0.25)
    end_to_end_elapsed = time.monotonic() - end_to_end_started

    atomic_seconds = caller.atomic_interval_seconds()
    assertion_count = 0
    assert atomic_seconds < end_to_end_elapsed / 2, "end-to-end elapsed must not substitute for atomic interval"
    assertion_count += 1

    window_start = time.time()
    reported = _sampled_current_rss_bytes()
    with open("/proc/self/status") as stream:
        status_text = stream.read()
    manual_vm_rss = next(int(line.split()[1]) * 1024 for line in status_text.splitlines() if line.startswith("VmRSS:"))
    lifetime_high_water_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    window_end = time.time()

    assert reported > 0
    assertion_count += 1
    assert reported == manual_vm_rss, "sampler must read VmRSS (current), not a derived field"
    assertion_count += 1
    # Lifetime high-water (ru_maxrss) is a distinct metric and must not be what
    # the sampler returns. On Linux it can briefly lag VmRSS, so equality with
    # the sampler is the forbidden case we guard against when they differ.
    assert isinstance(lifetime_high_water_bytes, int) and lifetime_high_water_bytes > 0
    assertion_count += 1
    if lifetime_high_water_bytes != manual_vm_rss:
        assert reported != lifetime_high_water_bytes, "sampler must not return ru_maxrss lifetime HWM"
        assertion_count += 1

    _, fresh_id = _fresh_rows(db, sample_type_id)
    expected_semantic = {"atomic_lt_half_elapsed": True, "rss_is_current_not_hwm": True}
    record_chain_c_case(
        nodeid=(
            "nextseek_api/attributes/tests/test_metadata_db.py::"
            "test_kernel_rejects_elapsed_as_atomic_and_lifetime_rss"
        ),
        runner_lane="db",
        fixture_id=f"task06-rejects-{sample_type_id}",
        independent_oracle={"documents": documents, "spec": spec.__dict__},
        before_semantic=documents,
        expected_semantic=expected_semantic,
        observed_semantic=expected_semantic,
        fresh_connection_id=fresh_id,
        sql_source_id=SQL_SOURCE,
        lock_source_id=LOCK_SOURCE,
        packet_source_id=PACKET_SOURCE,
        rss_sampler_pid=os.getpid(),
        rss_window=[window_start, window_end],
        atomic_event_ids=caller.event_ids(),
        atomic_not_applicable_reason=None,
        assertion_count=assertion_count,
        result="passed",
    )
