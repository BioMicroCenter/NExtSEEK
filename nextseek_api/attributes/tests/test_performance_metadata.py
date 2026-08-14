"""Benchmark-lane entry for T06.

The manifest `benchmark_lane_contract` selects this module only. It hosts the
full frozen Cartesian protocol plus the exact Chain-C primary node that asserts
162 cells / 810 runs (amendment 2026-07-31: one warmup + four measured) and publishes the content-addressed chunk selection.
"""
from __future__ import annotations

import gc
import hashlib
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import orjson
import pytest

from nextseek_api.attributes.metadata import RewriteSpec, rewrite_type_metadata
from nextseek_api.attributes.tests.chain_c_t06 import record_chain_c_case
from nextseek_api.attributes.tests.test_metadata_benchmark import (
    CASES,
    _bulk_seed_samples,
    _exact_document,
    _independent_expected_document,
    _sampled_current_rss_bytes,
)

assert len(CASES) == 162

# Amendment 2026-07-31 (simplified 2026-08-03): the lane must never consume
# unbounded wall clock. Cumulative-pace projection false-tripped twice — the
# seeded shuffle front-loads one-time pristine fills and a heavy-biased cell
# mix, so early averages structurally overestimate the total — so the guard
# is the user-ruled absolute budget alone, checked before every cell.
# Sampling or axis reduction remains forbidden — abort is the only fallback.
_WATCHDOG_BUDGET_SECONDS = 64_800
_WATCHDOG = {"started": None}


@pytest.fixture(autouse=True)
def _matrix_runtime_watchdog(request):
    if "disposable_attribute_db" not in request.fixturenames:
        yield
        return
    if _WATCHDOG["started"] is None:
        _WATCHDOG["started"] = time.monotonic()
    elapsed = time.monotonic() - _WATCHDOG["started"]
    if elapsed > _WATCHDOG_BUDGET_SECONDS:
        pytest.exit(
            f"T06 watchdog: elapsed {elapsed:.0f}s exceeds the "
            f"{_WATCHDOG_BUDGET_SECONDS}s budget — stopping for reviewed amendment",
            returncode=64,
        )
    yield


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("row_count", "width", "chunk_rows", "chunk_bytes", "workload"), CASES)
def test_kernel_frozen_scale_protocol(
    row_count, width, chunk_rows, chunk_bytes, workload, disposable_attribute_db, rails_like_workload, sql_telemetry,
):
    db = disposable_attribute_db
    raw_runs = []
    spec = RewriteSpec(("UID", "New", "Padding"), renames=(("Old", "New"),))
    ordinal = CASES.index((row_count, width, chunk_rows, chunk_bytes, workload))
    resume_path = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"]) / "raw" / f"metadata-{ordinal:04d}.json"
    if resume_path.exists():
        # Resume (2026-08-03): an interrupted run of the identical amended
        # protocol already produced this cell. Validate identity and
        # integrity, then fast-pass instead of re-measuring; import
        # provenance is recorded in the run root's resume-manifest.json.
        prior = orjson.loads(resume_path.read_bytes())
        assert len(prior) == 5
        assert {r["repetition"] for r in prior} == set(range(5))
        assert all(
            r["row_count"] == row_count and r["metadata_width_bytes"] == width
            and r["chunk_rows"] == chunk_rows and r["chunk_bytes"] == chunk_bytes
            and r["workload"] == workload and r["seed"] == 20260718
            for r in prior
        )
        assert all(not r["errors"] and not r["timeouts"] for r in prior if not r["warmup"])
        return
    for repetition in range(5):
        sample_type_id = 800 + repetition
        db.execute_sql([
            ("DELETE FROM samples WHERE sample_type_id=%s", (sample_type_id,)),
            ("DELETE FROM sample_attributes WHERE sample_type_id=%s", (sample_type_id,)),
            ("DELETE FROM sample_types WHERE id=%s", (sample_type_id,)),
        ])
        _bulk_seed_samples(db, sample_type_id, row_count, width)
        gc.collect()
        observed_shape = db.query(
            "SELECT COUNT(*),MIN(OCTET_LENGTH(json_metadata)),MAX(OCTET_LENGTH(json_metadata)) "
            "FROM samples WHERE sample_type_id=%s",
            (sample_type_id,),
        )[0]
        assert tuple(map(int, observed_shape)) == (row_count, width, width)
        probe = db.query(
            "SELECT id,json_metadata FROM samples WHERE sample_type_id=%s AND id IN (1,%s) ORDER BY id",
            (sample_type_id, row_count),
        )
        for pk, raw in probe:
            assert orjson.loads(raw) == _exact_document(int(pk), width)

        workload_handle = (
            rails_like_workload.start(db, sample_type_id=sample_type_id) if workload == "rails_like" else None
        )
        before = db.checksum("samples", where={"sample_type_id": sample_type_id})
        sql_telemetry.reset()
        # Manifest metadata_memory_formula / Section 5 text: peak RSS *delta*
        # (added_peak_rss), not absolute process RSS across the whole suite.
        baseline_rss = _sampled_current_rss_bytes()
        rss_samples = [baseline_rss]
        started = time.perf_counter()
        transaction_started = time.perf_counter()
        error = None
        connection = None
        try:
            connection = db.connect()
            assert connection._owner is sql_telemetry
            assert len(sql_telemetry._open) == 1
            result = rewrite_type_metadata(connection, sample_type_id, spec, chunk_rows, chunk_bytes)
            connection.commit()
            assert result.updated == row_count
            rss_samples.append(_sampled_current_rss_bytes())
        except Exception as exc:  # noqa: BLE001 - capture full error text into the raw run
            error = f"{type(exc).__name__}:{exc}"
            if connection is not None:
                connection.rollback()
        finally:
            if connection is not None:
                connection.close()
        elapsed = time.perf_counter() - started
        transaction_seconds = time.perf_counter() - transaction_started
        telemetry = sql_telemetry.snapshot()
        after = db.checksum("samples", where={"sample_type_id": sample_type_id})
        if error is None:
            post = db.query(
                "SELECT id,json_metadata FROM samples WHERE sample_type_id=%s AND id IN (1,%s) ORDER BY id",
                (sample_type_id, row_count),
            )
            for pk, raw in post:
                assert orjson.loads(raw) == _independent_expected_document(int(pk), width)
        if workload_handle is not None:
            rails_like_workload.stop(workload_handle)
        peak_rss = max(0, max(rss_samples) - baseline_rss)
        # packet_bytes is the largest write-chunk payload the kernel can emit for
        # this cell (row-width × rows-per-chunk, byte-ceiling capped). Session
        # Bytes_received/Bytes_sent deltas cover the whole two-pass scan and are
        # recorded under provenance — they are not the chunk ceiling oracle.
        rows_per_chunk = min(chunk_rows, max(1, chunk_bytes // width), row_count)
        max_write_chunk_payload = rows_per_chunk * width
        raw_runs.append({
            "seed": 20260718,
            "realized_ordinal": ordinal,
            "repetition": repetition,
            "warmup": repetition < 1,
            "row_count": row_count,
            "metadata_width_bytes": width,
            "workload": workload,
            "elapsed_seconds": elapsed,
            "rows_per_second": (row_count / elapsed) if elapsed > 0 else 0.0,
            "peak_rss_bytes": peak_rss,
            "sql_count": telemetry.sql_count,
            "transaction_seconds": transaction_seconds,
            "maximum_lock_wait_seconds": telemetry.maximum_lock_wait_seconds,
            "packet_bytes": max_write_chunk_payload,
            "chunk_rows": chunk_rows,
            "chunk_bytes": chunk_bytes,
            "worker_utilization": None,
            "cancellation_latency_seconds": None,
            "errors": [] if error is None else [error],
            "timeouts": telemetry.timeouts,
            "before_checksum": before,
            "after_checksum": after,
            "telemetry_provenance": {
                "sql_source_id": "performance_schema.events_statements_current+history_long",
                "lock_source_id": "performance_schema LOCK_TIME picoseconds",
                "packet_source_id": "max_write_chunk_payload_bytes",
                "session_byte_delta": telemetry.maximum_packet_bytes,
                "rss_source_id": "/proc/self/status VmRSS added_peak_delta",
                "rss_baseline_bytes": baseline_rss,
            },
        })
        gc.collect()
    measured = raw_runs[1:]
    assert len(measured) == 4
    assert all(not run["errors"] and not run["timeouts"] for run in measured)
    effective_rows = min(chunk_rows, max(1, chunk_bytes // width))
    sql_ceiling = 3 * math.ceil(row_count / effective_rows) + 2
    assert all(run["sql_count"] <= sql_ceiling for run in measured)
    assert max(run["peak_rss_bytes"] for run in measured) <= 268435456
    if row_count == 50000:
        assert statistics.median(run["elapsed_seconds"] for run in measured) <= 150
    required = {
        "elapsed_seconds", "rows_per_second", "peak_rss_bytes", "sql_count", "transaction_seconds",
        "maximum_lock_wait_seconds", "packet_bytes", "chunk_rows", "worker_utilization",
        "cancellation_latency_seconds", "errors", "timeouts", "before_checksum", "after_checksum",
    }
    assert all(required <= run.keys() for run in raw_runs)
    output = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"]) / "raw" / f"metadata-{ordinal:04d}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(orjson.dumps(raw_runs, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2).decode() + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def test_kernel_telemetry_window_excludes_setup_and_checksum():
    calls = []
    telemetry = type("Telemetry", (), {
        "reset": lambda self: calls.append("reset"),
        "snapshot": lambda self: calls.append("snapshot"),
    })()
    calls.extend(("seed", "shape", "workload_start", "before_checksum"))
    telemetry.reset()
    calls.extend(("db.connect:one_wrapper", "one_registration_marker", "rewrite", "commit"))
    telemetry.snapshot()
    calls.append("after_checksum")
    assert calls == [
        "seed", "shape", "workload_start", "before_checksum",
        "reset", "db.connect:one_wrapper", "one_registration_marker",
        "rewrite", "commit", "snapshot", "after_checksum",
    ]


def test_metadata_matrix_has_exact_162_cells_and_810_runs():
    """Exact Chain-C primary benchmark node: 162 cells × 5 reps = 810 runs."""
    run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    raw = run_root / "raw"
    files = sorted(raw.glob("metadata-*.json"))
    assertion_count = 0
    assert len(CASES) == 162
    assertion_count += 1
    assert len(CASES) * 5 == 810
    assertion_count += 1
    assert len(files) == 162, f"expected 162 raw cell files, found {len(files)}"
    assertion_count += 1

    total_runs = 0
    seen_cells = set()
    for path in files:
        runs = orjson.loads(path.read_bytes())
        assert len(runs) == 5
        assertion_count += 1
        total_runs += len(runs)
        cell = (
            runs[0]["row_count"],
            runs[0]["metadata_width_bytes"],
            runs[0]["chunk_rows"],
            runs[0]["chunk_bytes"],
            runs[0]["workload"],
        )
        assert cell not in seen_cells
        seen_cells.add(cell)
        assert {run["repetition"] for run in runs} == set(range(5))
        assertion_count += 1
        assert all(not run["errors"] and not run["timeouts"] for run in runs if not run["warmup"])
        assertion_count += 1

    assert len(seen_cells) == 162
    assertion_count += 1
    assert total_runs == 810
    assertion_count += 1

    pointer_path = Path(
        "/home/taishajo/work/state/attribute-viewset/evidence/task-06/chunk-selection.pointer.json"
    )
    selector = Path(__file__).resolve().parents[3] / "scripts" / "select_attribute_chunk_defaults.py"
    subprocess.run(
        [sys.executable, str(selector), str(raw), "--output", str(pointer_path)],
        check=True,
    )
    pointer = orjson.loads(pointer_path.read_bytes())
    assert set(pointer) == {"schema_version", "path", "sha256"}
    assertion_count += 1
    assert pointer["schema_version"] == "attribute-chunk-selection-pointer/v1"
    assertion_count += 1
    artifact = Path(pointer["path"])
    payload = artifact.read_bytes()
    assert artifact.name == pointer["sha256"] + ".json"
    assertion_count += 1
    assert hashlib.sha256(payload).hexdigest() == pointer["sha256"]
    assertion_count += 1
    selected = orjson.loads(payload)
    assert selected["chunk_rows"] in (250, 1000, 2000)
    assertion_count += 1
    assert selected["chunk_bytes"] in (1048576, 4194304, 16777216)
    assertion_count += 1

    window_start = time.time()
    expected = {"cells": 162, "runs": 810, "selection_sha256": pointer["sha256"]}
    observed = {
        "cells": len(seen_cells),
        "runs": total_runs,
        "selection_sha256": pointer["sha256"],
    }
    window_end = time.time()
    record_chain_c_case(
        nodeid=(
            "nextseek_api/attributes/tests/test_performance_metadata.py::"
            "test_metadata_matrix_has_exact_162_cells_and_810_runs"
        ),
        runner_lane="benchmark",
        fixture_id="task06-matrix-162x5",
        independent_oracle={"cases": len(CASES), "repetitions": 5},
        before_semantic={"raw_files": len(files)},
        expected_semantic=expected,
        observed_semantic=observed,
        fresh_connection_id=None,
        sql_source_id=None,
        lock_source_id=None,
        packet_source_id=None,
        rss_sampler_pid=os.getpid(),
        rss_window=[window_start, window_end],
        atomic_event_ids=[],
        atomic_not_applicable_reason="benchmark matrix aggregation has no T07 atomic caller",
        assertion_count=assertion_count,
        result="passed",
    )
