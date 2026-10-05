"""The road the 11 op tools take to NExtSEEK (approach 1, piece 2). No chat_nextseek, no NExtSEEK code.

Django sets NEXTSEEK_CC_OPS_ROAD in this container's env:

* ``direct`` (the default): POST /nextseek_api/assistant/<op>/ through nginx with the turn pass, on the client
  nextseek-query uses (``_assistant_client.py``). report, generate-submission and build-upload-xlsx send the chat's
  session id and download each file they made through the owner-checked artifact GET into
  /data/scratch/nextseek-artifacts/, which the turn's publish reads. Nothing trusted writes into this container's
  folders.
* ``sidecar`` (kept one release, the rollback): the WebSocket sidecar, as before, its frame carrying the turn pass.

A failure is an ``OpCallError`` whose code the runner turns into its exit (``_op_errors.EXIT``).
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from _op_errors import EXIT, REASONS
from _turn_deadline import (TURN_DEADLINE_HEADROOM_S, no_time_to_retry_message, out_of_turn_message,
                            seconds_left, wait_s)

ROAD_ENV = "NEXTSEEK_CC_OPS_ROAD"
DIRECT = "direct"
SIDECAR = "sidecar"

#: NessieAI/ns/op_limits.py OP_LIMITS_S, copied; NessieAI/tests/cc/test_op_road_parity.py pins the two together.
OP_LIMITS_S: dict[str, float] = {
    "entity": 55.0,
    "parse": 55.0,
    "graph": 55.0,
    "aggregate": 55.0,
    "api-read": 55.0,
    "api-write": 55.0,
    "generate-submission": 150.0,
    "report": 150.0,
    "graph-schema": 60.0,
    "run-ls": 60.0,
    "build-upload-xlsx": 60.0,
}
#: The tool waits this much past the op's own limit: Django answers by the limit, the rest is the reply's trip.
TOOL_WAIT_MARGIN_S = 10.0
#: The ops whose outputs are files: they send the chat's session id and download what they made.
ARTIFACT_OPS = frozenset({"report", "generate-submission", "build-upload-xlsx"})
ARTIFACTS_SUBDIR = "nextseek-artifacts"
ARTIFACT_TIMEOUT_S = 30.0
_KEY_RE = re.compile(r"^\w+$")
# Module level so tests can freeze it.
_wallclock = time.time


class OpCallError(RuntimeError):
    """An op that did not answer: its code (a key of EXIT, or a sidecar code), the message the agent reads, the
    reason with AGENT_FAILED, the field list with VALIDATION."""

    def __init__(self, code: str, message: str, *, reason: str | None = None, errors: list | None = None,
                 exit_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason if reason in REASONS else None
        self.errors = errors
        self.exit_code = exit_code if exit_code is not None else EXIT.get(code, EXIT["TRANSPORT_ERROR"])


def road() -> str:
    """``SIDECAR`` when the env says so (any case, spaces trimmed), else ``DIRECT``."""
    return SIDECAR if os.environ.get(ROAD_ENV, "").strip().lower() == SIDECAR else DIRECT


def wait_for(op: str, now: float) -> float:
    """Seconds the tool waits for the op's answer: the op's limit plus the margin, and never past the turn's own
    deadline less the headroom every client keeps back (``_turn_deadline.wait_s``, with its 10 s floor)."""
    ceiling = OP_LIMITS_S[op] + TOOL_WAIT_MARGIN_S
    return min(ceiling, wait_s(now, fallback_s=ceiling))


def scratch_dir() -> Path:
    return Path(os.environ.get("NEXTSEEK_SCRATCH_DIR", "/data/scratch"))


def call(op: str, body: dict, *, client_factory: Callable[[], Any]) -> dict:
    """Run one op on the road the env names and return its ``result``."""
    if road() == SIDECAR:
        return _via_sidecar(op, body)
    return _direct(op, body, client_factory())


def _via_sidecar(op: str, body: dict) -> dict:
    import _sidecar_client as sc
    import _turn_pass as tp

    username = os.environ.get("NEXTSEEK_USERNAME") or os.environ.get("API_USER", "")
    try:
        return sc.call_op(op, body, ns_turn=(username, tp.turn_pass_from_env()),
                          sidecar_url=sc.sidecar_url_from_env())
    except sc.SidecarCallError as e:
        raise OpCallError(e.code, e.message, reason=e.reason, exit_code=e.exit_code) from e


def _timeout_message(op: str, now: float, timeout_s: float) -> str:
    left = seconds_left(now)
    if left is None or timeout_s >= OP_LIMITS_S[op] + TOOL_WAIT_MARGIN_S:
        return f"NExtSEEK did not answer within {timeout_s:.0f} s."
    if left < 2 * TURN_DEADLINE_HEADROOM_S:
        return out_of_turn_message(left, timeout_s)
    return no_time_to_retry_message(timeout_s, service="NExtSEEK")


def _direct(op: str, body: dict, client) -> dict:
    if op in ARTIFACT_OPS:
        chat = os.environ.get("NEXTSEEK_CHAT_SESSION_ID", "").strip()
        if chat:
            body = {**body, "session_id": chat}
    now = _wallclock()
    timeout_s = wait_for(op, now)
    try:
        resp = client.post_op(op, body, timeout_s=timeout_s)
    except httpx.TimeoutException as exc:
        raise OpCallError("TRANSPORT_ERROR", _timeout_message(op, now, timeout_s)) from exc
    except httpx.TransportError as exc:
        raise OpCallError("TRANSPORT_ERROR", f"NExtSEEK could not be reached ({type(exc).__name__}).") from exc
    if not resp.is_success:
        raise error_from_response(resp)
    try:
        envelope = resp.json()
    except ValueError as exc:
        raise OpCallError("TRANSPORT_ERROR", "NExtSEEK sent a reply the tool could not read.") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
        raise OpCallError("TRANSPORT_ERROR", "NExtSEEK sent a reply the tool could not read.")
    result = envelope["result"]
    download = envelope.get("download")
    if op in ARTIFACT_OPS and download:
        staged = fetch_artifacts(download, client)
        if op == "report":
            saved = result.get("saved_files") if isinstance(result.get("saved_files"), dict) else {}
            result["saved_files"] = {**saved, **staged}
        else:
            result["staged_files"] = staged
    return result


def error_from_response(resp: httpx.Response) -> OpCallError:
    """The error a non-2xx NExtSEEK reply means: its own code when it sent one, else by status."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = body.get("code")
    if code in EXIT:
        errors = body.get("errors") if code == "VALIDATION" else None
        return OpCallError(code, str(body.get("message") or code), reason=body.get("reason"), errors=errors)
    status = resp.status_code
    if status == 401:
        return OpCallError("AUTH_FAILED", "NExtSEEK did not accept this turn's pass.")
    if status == 403:
        if "PASS_NOT_ALLOWED" in str(body.get("detail") or ""):
            return OpCallError("PASS_NOT_ALLOWED", "This turn's pass does not allow this request.")
        return OpCallError("PASS_NOT_ALLOWED",
                           "NExtSEEK refused this request (HTTP 403): the user may not use this project or route.")
    if status in (502, 503, 504):
        return OpCallError("TRANSPORT_ERROR", f"NExtSEEK could not be reached (HTTP {status}).")
    return OpCallError("AGENT_FAILED", f"NExtSEEK answered HTTP {status} with no error code.", reason="internal")


def fetch_artifacts(download: dict, client) -> dict[str, str]:
    """Download every artifact the op registered into scratch/nextseek-artifacts/; ``{key: path}``."""
    try:
        session_id = str(uuid.UUID(str(download.get("session_id"))))
    except ValueError as exc:
        raise OpCallError("STAGING_ERROR", "The op's download list carries no valid session id.") from exc
    bundle_id = download.get("bundle_id")
    if not isinstance(bundle_id, int) or isinstance(bundle_id, bool) or bundle_id < 1:
        raise OpCallError("STAGING_ERROR", "The op's download list carries no valid bundle id.")
    dest_dir = scratch_dir() / ARTIFACTS_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged: dict[str, str] = {}
    for artifact in download.get("artifacts") or []:
        key = artifact.get("key") if isinstance(artifact, dict) else None
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise OpCallError("STAGING_ERROR", "The op's download list named an unusable artifact key.")
        try:
            timeout_s = min(ARTIFACT_TIMEOUT_S, wait_s(_wallclock(), fallback_s=ARTIFACT_TIMEOUT_S))
            staged[key] = str(client.download_artifact_to(session_id, bundle_id, key, dest_dir, timeout_s=timeout_s))
        except httpx.HTTPStatusError as exc:
            raise OpCallError("STAGING_ERROR", f"The file {key} could not be downloaded "
                                               f"(HTTP {exc.response.status_code}).") from exc
        except (httpx.TransportError, OSError) as exc:
            raise OpCallError("STAGING_ERROR", f"The file {key} could not be downloaded "
                                               f"({type(exc).__name__}).") from exc
    return staged
