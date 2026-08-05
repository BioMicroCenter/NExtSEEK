"""Emit `chain-c-t07-execution/v1` structured case records for T07 primary
nodes (task-07 spec Section 3).

Every field/relation below mirrors the frozen JSON schema literally embedded
in the task-07 spec: ``schema``, ``nodeid``, ``runner_lane``, ``plan_sha256``,
``request_sha256``, ``ordered_input_fingerprints``,
``ordered_output_fingerprints`` (same length as the input list, and -- like
the input list -- real sha256 hex digests, never a status string or other
placeholder), ``fault_point``, ``classification``, ``physical_commit_count``,
``claim_owner``/``claim_generation``/``lease_version``/``state_version``,
``lease_terminal`` (the frozen schema's own relation requires this to always
equal ``True``; this module never hardcodes it -- the caller must derive it
from real, freshly-read DB state and pass it in, and a caller that has not
actually terminalized its claimed partition gets a loud `AssertionError`
here rather than a silently fabricated record), ``atomic_event_ids``,
``connection_ids`` (non-empty for the ``db`` lane), ``token_ids``,
``assertion_count``, and ``result`` (``"passed"``). ``lane_by_node`` is
derived from the nodeid exactly as the schema's ``relations`` require:
``/test_executor_db.py::`` -> ``"db"``, else ``"unit"``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

CHAIN_C_SCHEMA = "chain-c-t07-execution/v1"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _sha256_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def chain_c_root() -> Path:
    run_root = os.environ.get("ATTRIBUTE_EVIDENCE_RUN_ROOT")
    base = (
        Path(run_root)
        if run_root
        else Path("/home/taishajo/work/state/attribute-viewset/evidence/task-07/chain-c-dev")
    )
    root = base / "chain-c"
    root.mkdir(parents=True, exist_ok=True)
    return root


def lane_for_nodeid(nodeid: str) -> str:
    return "db" if "/test_executor_db.py::" in nodeid else "unit"


def record_chain_c_case(
    *,
    nodeid: str,
    plan,
    request_payload,
    ordered_input_fingerprints: list,
    ordered_output_fingerprints: list,
    fault_point: str | None,
    classification: str,
    physical_commit_count: int,
    claim_owner: str | None,
    claim_generation: int,
    lease_version: int,
    state_version: int,
    lease_terminal: bool,
    atomic_event_ids: list[str],
    connection_ids: list[str],
    token_ids: list[str],
    assertion_count: int,
) -> Path:
    runner_lane = lane_for_nodeid(nodeid)
    if assertion_count < 1:
        raise AssertionError("chain-c record requires a positive assertion_count")
    if len(ordered_output_fingerprints) != len(ordered_input_fingerprints):
        raise AssertionError("ordered_output_fingerprints must match ordered_input_fingerprints length")
    for value in (*ordered_input_fingerprints, *ordered_output_fingerprints):
        if not _SHA256_HEX.match(str(value)):
            raise AssertionError(
                f"fingerprint list entries must be real sha256 hex digests, got {value!r} "
                "-- a status string or other non-hash placeholder is never valid here"
            )
    if runner_lane == "db" and not connection_ids:
        raise AssertionError("db-lane chain-c record requires at least one connection id")
    if lease_terminal is not True:
        raise AssertionError(
            "lease_terminal must be observed True from real, freshly-read DB state -- "
            "terminalize any claimed partition (record_failure/record_commit, or an "
            "explicit cleanup CAS) before calling record_chain_c_case; this function "
            "never fabricates the field"
        )
    payload = {
        "schema": CHAIN_C_SCHEMA,
        "nodeid": nodeid,
        "runner_lane": runner_lane,
        "plan_sha256": _sha256_json(plan),
        "request_sha256": _sha256_json(request_payload),
        "ordered_input_fingerprints": list(ordered_input_fingerprints),
        "ordered_output_fingerprints": list(ordered_output_fingerprints),
        "fault_point": fault_point,
        "classification": classification,
        "physical_commit_count": physical_commit_count,
        "claim_owner": claim_owner,
        "claim_generation": claim_generation,
        "lease_version": lease_version,
        "state_version": state_version,
        "lease_terminal": lease_terminal,
        "atomic_event_ids": list(atomic_event_ids),
        "connection_ids": list(connection_ids),
        "token_ids": list(token_ids),
        "assertion_count": assertion_count,
        "result": "passed",
    }
    safe = _SAFE.sub("_", nodeid)
    path = chain_c_root() / f"{safe}.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return path
