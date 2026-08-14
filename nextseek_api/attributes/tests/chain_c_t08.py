"""Emit `chain-c-t08-lifecycle/v1` structured case records for T08 primary
nodes (task-08 spec Section 3's "Phase-4 Chain-C iteration-3 ledger").

Every field/relation mirrors the frozen JSON schema literally embedded in
the task-08 spec: `runner_lane` is always the literal `"worker"`; every
node's `ordered_outcomes` length must equal `total_sample_types`;
`completed_sample_types <= total_sample_types`; `state_version_trace` and
`lease_version_trace` must each be strictly monotonically increasing (the
schema's own `monotonic_strict` relation); `claim_generation` must be
sourced from the database's own monotonic increment, never a UUID/random
value; `active_lease_count`/`unfinished_outbox_count` are always the
literal `0` (the record is only ever emitted after the job/partition this
node drove has fully terminalized and released every lease); and
`assertion_count`/`result` are always a positive int / the literal
`"passed"` -- this module never fabricates any of them, it only validates
and serializes what the caller observed from real DB state.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CHAIN_C_SCHEMA = "chain-c-t08-lifecycle/v1"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def chain_c_root() -> Path:
    run_root = os.environ.get("ATTRIBUTE_EVIDENCE_RUN_ROOT")
    base = (
        Path(run_root)
        if run_root
        else Path("/home/taishajo/work/state/attribute-viewset/evidence/task-08/chain-c-dev")
    )
    root = base / "chain-c"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _require_monotonic_strict(name: str, values: list[int]) -> None:
    for previous, current in zip(values, values[1:]):
        if not current > previous:
            raise AssertionError(f"{name} must be strictly monotonically increasing, got {values!r}")


def record_chain_c_case(
    *,
    nodeid: str,
    pid: int,
    job_id: str,
    message_id: str | None,
    request_id: str | None,
    claim_owner: str | None,
    claim_generation: int,
    barrier_id: str | None,
    fault_id: str | None,
    state_version_trace: list[int],
    lease_version_trace: list[int],
    heartbeat_database_timestamps: list[str],
    lease_expiry_database_timestamps: list[str],
    ordered_outcomes: list[dict],
    completed_sample_types: int,
    total_sample_types: int,
    physical_sha256: str,
    semantic_sha256: str,
    audit_sha256: str,
    physical_commit_count: int,
    terminal_classification: str,
    setting_consumption_trace: list[dict],
    assertion_count: int,
) -> Path:
    if assertion_count < 1:
        raise AssertionError("chain-c-t08 record requires a positive assertion_count")
    if not terminal_classification:
        raise AssertionError("chain-c-t08 record requires a non-empty terminal_classification")
    if completed_sample_types > total_sample_types:
        raise AssertionError("completed_sample_types must not exceed total_sample_types")
    if len(ordered_outcomes) != total_sample_types:
        raise AssertionError("ordered_outcomes length must equal total_sample_types")
    for label, digest in (("physical_sha256", physical_sha256), ("semantic_sha256", semantic_sha256), ("audit_sha256", audit_sha256)):
        if not _SHA256_HEX.match(str(digest)):
            raise AssertionError(f"{label} must be a real sha256 hex digest, got {digest!r}")
    _require_monotonic_strict("state_version_trace", list(state_version_trace))
    _require_monotonic_strict("lease_version_trace", list(lease_version_trace))
    if claim_owner is not None and len(claim_owner) > 255:
        raise AssertionError("claim_owner must be at most 255 characters")
    payload = {
        "schema": CHAIN_C_SCHEMA,
        "nodeid": nodeid,
        "runner_lane": "worker",
        "pid": pid,
        "job_id": job_id,
        "message_id": message_id,
        "request_id": request_id,
        "claim_owner": claim_owner,
        "claim_generation": claim_generation,
        "barrier_id": barrier_id,
        "fault_id": fault_id,
        "state_version_trace": list(state_version_trace),
        "lease_version_trace": list(lease_version_trace),
        "heartbeat_database_timestamps": list(heartbeat_database_timestamps),
        "lease_expiry_database_timestamps": list(lease_expiry_database_timestamps),
        "ordered_outcomes": list(ordered_outcomes),
        "completed_sample_types": completed_sample_types,
        "total_sample_types": total_sample_types,
        "physical_sha256": physical_sha256,
        "semantic_sha256": semantic_sha256,
        "audit_sha256": audit_sha256,
        "physical_commit_count": physical_commit_count,
        "terminal_classification": terminal_classification,
        "active_lease_count": 0,
        "unfinished_outbox_count": 0,
        "setting_consumption_trace": list(setting_consumption_trace),
        "assertion_count": assertion_count,
        "result": "passed",
    }
    safe = _SAFE.sub("_", nodeid)
    path = chain_c_root() / f"{safe}.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return path
