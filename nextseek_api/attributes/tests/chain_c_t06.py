"""Emit `chain-c-t06-rewrite/v1` structured case records for T06 primary nodes."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

CHAIN_C_SCHEMA = "chain-c-t06-rewrite/v1"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sha256_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def chain_c_root() -> Path:
    run_root = os.environ.get("ATTRIBUTE_EVIDENCE_RUN_ROOT")
    base = (
        Path(run_root)
        if run_root
        else Path("/home/taishajo/work/state/attribute-viewset/evidence/task-06/chain-c-dev")
    )
    root = base / "chain-c"
    root.mkdir(parents=True, exist_ok=True)
    return root


def record_chain_c_case(
    *,
    nodeid: str,
    runner_lane: str,
    fixture_id: str,
    independent_oracle,
    before_semantic,
    expected_semantic,
    observed_semantic,
    fresh_connection_id: str | None,
    sql_source_id: str | None,
    lock_source_id: str | None,
    packet_source_id: str | None,
    rss_sampler_pid: int | None,
    rss_window: list[float] | None,
    atomic_event_ids: list[str],
    atomic_not_applicable_reason: str | None,
    assertion_count: int,
    result: str,
) -> Path:
    if assertion_count < 1:
        raise AssertionError("chain-c record requires a positive assertion_count")
    if runner_lane not in {"unit", "db", "benchmark"}:
        raise AssertionError(f"invalid runner_lane {runner_lane!r}")
    if result not in {"passed", "expected_error"}:
        raise AssertionError(f"invalid result {result!r}")
    has_events = len(atomic_event_ids) >= 2
    has_reason = bool(atomic_not_applicable_reason)
    if has_events == has_reason:
        raise AssertionError("atomic_event_ids XOR atomic_not_applicable_reason")
    observed_sha = None if observed_semantic is None else _sha256_json(observed_semantic)
    expected_sha = _sha256_json(expected_semantic)
    if result == "passed" and observed_sha != expected_sha:
        raise AssertionError("passed chain-c record requires observed==expected semantic sha")
    if "/test_metadata_db.py::" in nodeid:
        required = (
            fresh_connection_id,
            sql_source_id,
            packet_source_id,
            rss_sampler_pid,
            rss_window,
        )
        if any(value is None for value in required):
            raise AssertionError("db-lane chain-c provenance fields must be non-null")
        if rss_sampler_pid is not None and rss_sampler_pid < 1:
            raise AssertionError("rss_sampler_pid must be a positive integer")
    payload = {
        "schema": CHAIN_C_SCHEMA,
        "nodeid": nodeid,
        "runner_lane": runner_lane,
        "fixture_id": fixture_id,
        "independent_oracle_sha256": _sha256_json(independent_oracle),
        "before_semantic_sha256": _sha256_json(before_semantic),
        "expected_semantic_sha256": expected_sha,
        "observed_semantic_sha256": observed_sha,
        "fresh_connection_id": fresh_connection_id,
        "sql_source_id": sql_source_id,
        "lock_source_id": lock_source_id,
        "packet_source_id": packet_source_id,
        "rss_sampler_pid": rss_sampler_pid,
        "rss_window": rss_window,
        "atomic_event_ids": list(atomic_event_ids),
        "atomic_not_applicable_reason": atomic_not_applicable_reason,
        "assertion_count": assertion_count,
        "result": result,
    }
    safe = _SAFE.sub("_", nodeid)
    path = chain_c_root() / f"{safe}.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return path
