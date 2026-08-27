"""Sidecar WS server: accept → validate (T2) → build per-user HTTP config →
dispatch (T16 ops via NExtSEEK HTTP) → typed response.
Per-call NS login => Basic auth on each HTTP request (U-2, no env mutation, T16).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import websockets
from pydantic import ValidationError

from sidecar.app import ops
from sidecar.app.config import SidecarConfig
from sidecar.app.contract import NsLogin, SidecarError, SidecarRequest, SidecarResponse
from sidecar.app.staging import StagingError  # top-level safe: staging.py has no heavy NS runtime imports

_CFG: SidecarConfig | None = None


@dataclass(frozen=True, repr=False)
class NsHttpConfig:
    """Lightweight per-request HTTP config: base_url + Basic auth credentials.
    Replaces the old in-process NS runtime config (T16, DD-A5-6). No env mutation."""
    base_url: str
    auth: tuple[str, str]

    def __repr__(self) -> str:
        return f"NsHttpConfig(base_url={self.base_url!r}, auth=('<redacted>', '<redacted>'))"


def _err_response(request_id: str, code: str, message: str, retryable: bool = False) -> str:
    return SidecarResponse(request_id=request_id, status="error", result=None,
                           error=SidecarError(code=code, message=message, retryable=retryable)
                           ).model_dump_json()


def _build_user_config(login: NsLogin) -> NsHttpConfig:
    """Build a per-request NsHttpConfig from the caller's NS login (U-2, T16).

    No os.environ mutation — credentials are passed as per-request Basic auth
    on each HTTP call via ns_client (DD-A5-6), eliminating the cross-user-bleed
    race class that the previous ChatConfig approach carried.
    """
    return NsHttpConfig(base_url=_CFG.nextseek_base_url, auth=(login.api_user, login.api_pass))


def _build_write_gate():
    from sidecar.app.write_gate import build_gate  # T5/T16: no-arg, confirmed_write only
    return build_gate()


def _build_stage(request_id: str, login: NsLogin):
    from sidecar.app.staging import make_stage  # T7
    return make_stage(_CFG, login, request_id)


def _build_stage_bytes(request_id: str, login: NsLogin):
    """Return the (stage_bytes, commit) pair for downloading artifacts over HTTP (T16, DD-A5-5)."""
    from sidecar.app.staging import make_stage_bytes  # T16
    return make_stage_bytes(_CFG, login, request_id)


async def handle_message(raw: str) -> str:
    request_id = "00000000-0000-4000-8000-000000000000"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return _err_response(request_id, "TRANSPORT_ERROR", "malformed JSON frame")
    # Adopt the client's request_id only when it is a str: SidecarResponse(request_id=int)
    # would itself raise ValidationError inside the except handler below, escaping
    # handle_message and killing the connection (1011) instead of replying VALIDATION.
    if isinstance(data, dict) and isinstance(data.get("request_id"), str):
        request_id = data["request_id"]
    try:
        req = SidecarRequest(**data)
    except ValidationError as exc:
        return _err_response(request_id, "VALIDATION", f"bad request: {exc.error_count()} errors")
    except TypeError:  # e.g. top-level JSON array: SidecarRequest(**list) is a TypeError
        return _err_response(request_id, "VALIDATION", "bad request: envelope must be a JSON object")

    # §12: reject malformed per-op args BEFORE touching shared resources (vet finding 15).
    try:
        from sidecar.app.contract import validate_op_args
        validate_op_args(req.op, req.args)
    except (ValidationError, ValueError) as exc:
        return _err_response(req.request_id, "VALIDATION", f"bad args for {req.op}: {exc}")

    try:
        config = _build_user_config(req.ns_login)
        # NOTE: _build_user_session has been REMOVED (T16) — granular HTTP ops are stateless POSTs.
        gate = _build_write_gate()
        stage = _build_stage(req.request_id, req.ns_login)
        stage_bytes, commit_bytes = _build_stage_bytes(req.request_id, req.ns_login)
    except Exception as exc:  # noqa: BLE001
        return _err_response(req.request_id, "CONFIG_ERROR", f"setup failed: {type(exc).__name__}")

    try:
        result = await asyncio.to_thread(
            ops.run_op, req.op, req.args,
            config=config,
            session=None,  # T16: ops are stateless HTTP forwarders; no in-process session
            write_gate=gate,
            stage=stage,
            stage_bytes=stage_bytes,
            commit_bytes=commit_bytes,
        )
    except ops.OpValidationError as exc:
        return _err_response(req.request_id, "VALIDATION", str(exc))
    except ops.WriteBlockedError as exc:
        return _err_response(req.request_id, "WRITE_BLOCKED", str(exc))
    except ops.AuthFailedError as exc:
        return _err_response(req.request_id, "AUTH_FAILED", str(exc))
    except ops.TransportError as exc:
        return _err_response(req.request_id, "TRANSPORT_ERROR", str(exc), retryable=True)
    except StagingError as exc:
        return _err_response(req.request_id, "STAGING_ERROR", str(exc))
    except Exception as exc:  # noqa: BLE001 — downstream LLM/API/Neo4j failure
        return _err_response(req.request_id, "AGENT_FAILED", f"{type(exc).__name__}")

    return SidecarResponse(request_id=req.request_id, status="ok", result=result,
                           error=None).model_dump_json()


async def _serve_conn(ws) -> None:  # pragma: no cover — requires a real socket (pytest-socket disabled)
    async for raw in ws:
        await ws.send(await handle_message(raw))


async def serve() -> None:  # pragma: no cover — requires a real socket (pytest-socket disabled)
    global _CFG
    _CFG = SidecarConfig.from_env()
    async with websockets.serve(_serve_conn, "0.0.0.0", _CFG.ws_port, max_size=16 * 1024 * 1024):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(serve())
