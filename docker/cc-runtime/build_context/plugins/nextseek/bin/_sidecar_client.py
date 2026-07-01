"""Thin WS client to the sidecar (U-13). Synchronous (the runner is a one-shot CLI).
Maps sidecar errors + transport failures to the §12 code/exit taxonomy."""
from __future__ import annotations

import json
import os
import uuid

# Sibling imports (same dir on PATH inside the image; sys.path patched by the runner)
from _ws_contract import ERROR_EXIT, SIDECAR_OPS


class SidecarCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = ERROR_EXIT.get(code, 7)


def _connect(url: str):
    from websockets.sync.client import connect
    return connect(url, open_timeout=10, close_timeout=5)


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
    try:
        ws.send(payload)
        raw = ws.recv(timeout=300)  # Important-1: finite timeout prevents hang on stalled sidecar
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
