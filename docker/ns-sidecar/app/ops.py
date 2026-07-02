"""The 7 granular sidecar ops, each calling the NExtSEEK native HTTP endpoint via ns_client.

Call shapes mirror the pre-sidecar runner dispatchers so behavior is preserved.
write_gate(op, api_plan_endpoint, api_plan_method, confirmed_write) -> None|raises.
stage(op, result) -> result' (T7 path-copy stage; used when no download block).
stage_bytes(op, key, data) -> staged_path (bytes writer for download block; writes NO marker).
commit_bytes() -> None (writes the atomic .complete marker once after all artifacts staged).
"""
from __future__ import annotations

import json
import tempfile
import os
from typing import Any, Callable

from sidecar.app import ns_client
from sidecar.app.contract import SIDECAR_OPS
from sidecar.app.exceptions import (
    AgentFailedError,
    AuthFailedError,
    OpValidationError,
    TransportError,
    WriteBlockedError,
)

# Re-export for callers that import exceptions from ops (server.py, write_gate.py, etc.)
__all__ = [
    "AgentFailedError", "AuthFailedError", "OpValidationError",
    "TransportError", "WriteBlockedError",
    "ALLOW_ALL", "NO_STAGE", "NO_STAGE_BYTES", "NO_COMMIT", "run_op",
]


def ALLOW_ALL(*a, **k) -> None:  # T4 default; T5 replaces with the real gate
    return None


def NO_STAGE(op: str, result: dict) -> dict:  # T4 default; T7 replaces
    return result


def NO_STAGE_BYTES(op: str, key: str, data: bytes) -> str:
    """Default no-op bytes writer for tests: writes to a temp file, returns the path.
    Writes NO .complete marker (that's commit_bytes's job)."""
    fd, path = tempfile.mkstemp(prefix=f"sidecar_{op}_{key}_", suffix=".bin")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def NO_COMMIT() -> None:
    """Default no-op marker committer."""
    return None


def _load_parser_plan(args: dict) -> Any:
    """Parse args["parser_plan"] as JSON; malformed input → OpValidationError so the
    server maps it to VALIDATION/exit 3 (pre-sidecar runner parity), not AGENT_FAILED."""
    try:
        return json.loads(args["parser_plan"])
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise OpValidationError(f"parser_plan is not valid JSON: {exc}") from exc


def run_op(op: str, args: dict, *, config: Any, session: Any,
           write_gate: Callable, stage: Callable,
           stage_bytes: Callable = NO_STAGE_BYTES,
           commit_bytes: Callable = NO_COMMIT) -> dict:
    if op not in SIDECAR_OPS:
        raise OpValidationError(f"not a sidecar op: {op!r}")
    handler = _HANDLERS[op]
    return handler(args, config, session, write_gate, stage, stage_bytes, commit_bytes)


def _entity(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    envelope = ns_client.call_op("entity", {"query": args["query"]},
                                 base_url=config.base_url, auth=config.auth)
    return envelope["result"]


def _parse(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    envelope = ns_client.call_op("parse", {"query": args["query"]},
                                 base_url=config.base_url, auth=config.auth)
    return envelope["result"]


def _graph(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    envelope = ns_client.call_op("graph", {"query": args["query"]},
                                 base_url=config.base_url, auth=config.auth)
    return envelope["result"]


def _api_read(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    # Validate parser_plan JSON before forwarding (OpValidationError parity)
    _load_parser_plan(args)  # raises OpValidationError on bad JSON
    body = {"parser_plan": args["parser_plan"]}
    envelope = ns_client.call_op("api-read", body,
                                 base_url=config.base_url, auth=config.auth)
    return envelope["result"]


def _api_write(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    confirmed = args.get("confirmed_write", False)
    # Local pre-check (defense-in-depth; NExtSEEK gates again server-side — DD-A5-4)
    write_gate("api-write", None, None, confirmed)  # raises WriteBlockedError if not True
    # Validate parser_plan JSON before forwarding
    _load_parser_plan(args)  # raises OpValidationError on bad JSON
    body = {"parser_plan": args["parser_plan"], "confirmed_write": confirmed}
    if "query" in args and args["query"]:
        body["query"] = args["query"]
    envelope = ns_client.call_op("api-write", body,
                                 base_url=config.base_url, auth=config.auth)
    return envelope["result"]


def _report(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    body = {"mode": args["mode"], "project": args["project"]}
    envelope = ns_client.call_op("report", body,
                                 base_url=config.base_url, auth=config.auth)
    result = envelope["result"]
    download = envelope.get("download")
    if download:
        # DD-A5-5: loop per-artifact, then commit once after the loop (F-T16-2-B)
        for art in download["artifacts"]:
            data = ns_client.fetch_artifact(art["url"],
                                            base_url=config.base_url, auth=config.auth)
            staged_path = stage_bytes("report", art["key"], data)
            result["saved_files"][art["key"]] = staged_path
        commit_bytes()  # exactly once after ALL artifacts are staged
        return result
    return stage("report", result)


def _generate_submission(args, config, session, write_gate, stage, stage_bytes, commit_bytes):
    body = {"type": args["type"], "uids": args["uids"]}
    if "query" in args and args["query"]:
        body["query"] = args["query"]
    envelope = ns_client.call_op("generate-submission", body,
                                 base_url=config.base_url, auth=config.auth)
    result = envelope["result"]
    download = envelope.get("download")
    if download:
        # DD-A5-5: for generate-submission, write staged_files (new dict, not saved_files)
        staged_files = {}
        for art in download["artifacts"]:
            data = ns_client.fetch_artifact(art["url"],
                                            base_url=config.base_url, auth=config.auth)
            staged_path = stage_bytes("generate-submission", art["key"], data)
            staged_files[art["key"]] = staged_path
        commit_bytes()  # exactly once after ALL artifacts are staged
        result["staged_files"] = staged_files
        return result
    return stage("generate-submission", result)


_HANDLERS: dict[str, Callable] = {
    "entity": _entity, "parse": _parse, "graph": _graph,
    "api-read": _api_read, "api-write": _api_write,
    "report": _report, "generate-submission": _generate_submission,
}
