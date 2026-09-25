"""Thin WS client to the sidecar (U-13). Synchronous (the runner is a one-shot CLI).
Maps sidecar errors + transport failures to the §12 code/exit taxonomy."""
from __future__ import annotations

import json
import os
import time
import uuid

# Sibling imports (same dir on PATH inside the image; sys.path patched by the runner)
from _turn_deadline import (TURN_DEADLINE_HEADROOM_S, no_time_to_retry_message, out_of_turn_message,
                            seconds_left, wait_s)
from _ws_contract import ERROR_EXIT, SIDECAR_OPS

# The longest a call waits for the sidecar's answer, and the wait when the turn's deadline is
# absent or unreadable. Finite so a stalled sidecar cannot hang the op (Important-1).
_RECV_CEILING_S: float = 300.0
# Module level so tests can freeze it.
_wallclock = time.time


class SidecarCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = ERROR_EXIT.get(code, 7)


def _connect(url: str):
    # max_size MUST match the sidecar server (ns-sidecar/app/server.py). The
    # websockets default recv cap is 1 MiB, which truncated large op RESPONSES
    # (e.g. an api-read of all NHP samples) into a WS 1009 "message too big"
    # close, surfaced to the agent as TRANSPORT_ERROR (2026-07-06 T6 blocker).
    from websockets.sync.client import connect
    return connect(url, open_timeout=10, close_timeout=5, max_size=16 * 1024 * 1024)


def recv_timeout_s(now: float | None = None) -> float:
    """Seconds call_op waits for the sidecar's answer: the time left in this turn, less the
    headroom the assistant client keeps back too, and never more than _RECV_CEILING_S (13b.2,
    _turn_deadline.py). The sidecar ops carry every sample question (nextseek-graph); a wait
    that outlives the turn is killed with it and the agent never reports what happened."""
    return wait_s(_wallclock() if now is None else now, fallback_s=_RECV_CEILING_S,
                  ceiling_s=_RECV_CEILING_S)


def _timeout_message(now: float, timeout_s: float) -> str:
    """What a wait that ran out says. The turn's deadline set the wait whenever it is below the
    ceiling, and then the turn has only the headroom left, so no retry fits in it. An op that
    started late (under twice the headroom left, as r5-682's 10 s wait did) ran out of turn, not
    service: say so. One that started with most of the turn left met a slow service: say that."""
    left = seconds_left(now)
    if left is None or timeout_s >= _RECV_CEILING_S:
        return f"the sidecar did not answer within {timeout_s:.0f} s"
    if left < 2 * TURN_DEADLINE_HEADROOM_S:
        return out_of_turn_message(left, timeout_s)
    return no_time_to_retry_message(timeout_s)


def call_op(op: str, args: dict, *, ns_login: tuple[str, str], sidecar_url: str,
            request_id: str | None = None) -> dict:
    if op not in SIDECAR_OPS:
        raise SidecarCallError("VALIDATION", f"not a sidecar op: {op!r}")
    request_id = request_id or str(uuid.uuid4())
    payload = json.dumps({
        "op": op, "args": args,
        "ns_login": {"api_user": ns_login[0], "api_pass": ns_login[1]},
        "request_id": request_id,
    })
    try:
        ws = _connect(sidecar_url)
    except Exception as exc:  # noqa: BLE001 — DNS/refused/unavailable
        raise SidecarCallError("TRANSPORT_ERROR", f"sidecar unreachable: {type(exc).__name__}") from exc
    now = _wallclock()
    timeout_s = recv_timeout_s(now)
    try:
        ws.send(payload)
        raw = ws.recv(timeout=timeout_s)
    except TimeoutError as exc:
        raise SidecarCallError("TRANSPORT_ERROR", _timeout_message(now, timeout_s)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SidecarCallError("TRANSPORT_ERROR", f"sidecar I/O failed: {type(exc).__name__}") from exc
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        resp = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SidecarCallError("TRANSPORT_ERROR", "malformed sidecar response") from exc
    if not isinstance(resp, dict):  # Minor-4: valid non-object JSON must not leak AttributeError
        raise SidecarCallError("TRANSPORT_ERROR", "malformed sidecar response")
    if resp.get("status") == "ok":
        return resp.get("result") or {}
    err = resp.get("error") or {}
    raise SidecarCallError(err.get("code", "TRANSPORT_ERROR"), err.get("message", "sidecar error"))


def sidecar_url_from_env() -> str:
    host = os.environ.get("NEXTSEEK_SIDECAR_HOST", "nextseek-sidecar")
    port = os.environ.get("NEXTSEEK_SIDECAR_PORT", "8765")
    return f"ws://{host}:{port}"
