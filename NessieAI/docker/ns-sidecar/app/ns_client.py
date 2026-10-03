"""Synchronous HTTP client for NExtSEEK native granular-op endpoints (T15, A-5; approach 1, piece 2). Calls POST
/nextseek_api/assistant/{op}/ with the turn pass (Authorization: NextseekTurn) and maps NExtSEEK's error reply onto the
sidecar exception classes that server.py's catch-ladder turns into §12 codes; a reply carrying one of NExtSEEK's op
error codes is passed through with its fixed message and reason.

IMPORT STYLE IS LOAD-BEARING: this module does ``import httpx`` (module-qualified)
and calls ``httpx.post(...)`` / ``httpx.get(...)``.  Do NOT change to
``from httpx import post, get`` — the unit tests monkeypatch via
``ns_client.httpx.post`` / ``ns_client.httpx.get``, which requires the
``ns_client.httpx`` attribute to exist.  A from-import form makes that
attribute absent and the monkeypatch raises AttributeError (F-T15-3).
"""
from __future__ import annotations

import httpx

from sidecar.app.exceptions import (
    AgentFailedError,
    AuthFailedError,
    PassThroughError,
    TransportError,
)

# Timeout (seconds) for all NExtSEEK granular-op calls (DD-A5-1).
_TIMEOUT = 60.0

#: NExtSEEK's op error codes and reasons (nextseek_api/assistant/op_errors.py CODES and REASONS).
_NEXTSEEK_CODES = frozenset({"VALIDATION", "WRITE_BLOCKED", "AUTH_FAILED", "PASS_NOT_ALLOWED", "BUSY", "TIME_UP",
                             "AGENT_FAILED"})
_REASONS = frozenset({"model_unavailable", "deadline", "bad_output", "internal"})


def _headers(turn_pass: str) -> dict:
    return {"Authorization": f"NextseekTurn {turn_pass}"}


def _map_error(resp: httpx.Response) -> None:
    """Inspect a non-2xx response and raise the matching sidecar exception.

    Error mapping:
      - a body with one of NExtSEEK's op error codes → PassThroughError (code, message, reason as sent)
      - 401                                           → AuthFailedError
      - CONFIG_ERROR / CONFIG_MISSING                 → AgentFailedError
      - any other 403                                 → AuthFailedError  (non-participant)
      - everything else                               → AgentFailedError
    """
    status = resp.status_code
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = body.get("code", "")

    if code in _NEXTSEEK_CODES:
        message = str(body.get("message") or code)
        if code == "VALIDATION":
            named = [f"{item.get('field')} ({item.get('type')})" for item in body.get("errors") or []
                     if isinstance(item, dict) and item.get("field")]
            if named:
                # BRAIN-PENDING: agent-visible on the sidecar road, not in BRAIN-CHANGES (preflight B5).
                message = f"{message} Fields: {', '.join(named)}."
        reason = body.get("reason") if body.get("reason") in _REASONS else None
        raise PassThroughError(code, message, reason)
    if status == 401:
        raise AuthFailedError("NExtSEEK returned 401 Unauthorized")
    if code in ("CONFIG_ERROR", "CONFIG_MISSING"):
        raise AgentFailedError(f"NExtSEEK {code}: {body.get('errors', [])}")
    if status == 403:
        raise AuthFailedError(f"NExtSEEK returned 403 (code={code!r}): {body.get('errors', [])}")
    raise AgentFailedError(
        f"NExtSEEK returned HTTP {status} (code={code!r}): {body.get('errors', [])}"
    )


def call_op(
    op: str,
    body: dict,
    *,
    base_url: str,
    turn_pass: str,
) -> dict:
    """POST a granular op to NExtSEEK and return the parsed JSON response dict.

    Args:
        op:       Sidecar op name (e.g. "entity", "report").
        body:     Request payload dict (forwarded as JSON).
        base_url: NExtSEEK base URL, e.g. "http://nextseek_nginx".
        turn_pass: this turn's pass, sent as Authorization: NextseekTurn.

    Returns:
        The full response dict ({"op": ..., "result": ..., "download"?: ...}).

    Raises:
        AuthFailedError:      401 or non-participant 403.
        OpValidationError:    422 / VALIDATION code.
        WriteBlockedError:    403 / WRITE_BLOCKED code.
        AgentFailedError:     CONFIG_ERROR, CONFIG_MISSING, or unexpected error.
        TransportError:       httpx.HTTPError (connect / read timeout).
    """
    url = f"{base_url}/nextseek_api/assistant/{op}/"
    try:
        resp = httpx.post(url, json=body, headers=_headers(turn_pass), timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise TransportError(f"HTTP transport error calling {url}: {exc}") from exc

    if resp.is_success:
        return resp.json()

    _map_error(resp)
    # _map_error always raises; this line is unreachable but satisfies type checkers.
    raise AgentFailedError(f"Unhandled NExtSEEK error for op {op!r}")  # pragma: no cover


def fetch_artifact(
    rel_url: str,
    *,
    base_url: str,
    turn_pass: str,
) -> bytes:
    """GET a report/submission artifact from NExtSEEK and return raw bytes.

    Args:
        rel_url:  Server-relative URL from download.artifacts[].url.
        base_url: NExtSEEK base URL, e.g. "http://nextseek_nginx".
        turn_pass: this turn's pass, sent as Authorization: NextseekTurn.

    Returns:
        Raw artifact bytes.

    Raises:
        AuthFailedError:  401 or non-participant 403.
        AgentFailedError: Unexpected HTTP error.
        TransportError:   httpx.HTTPError (connect / read timeout).
    """
    url = f"{base_url}{rel_url}"
    try:
        resp = httpx.get(url, headers=_headers(turn_pass), timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise TransportError(f"HTTP transport error fetching artifact {url}: {exc}") from exc

    if resp.is_success:
        return resp.content

    _map_error(resp)
    raise AgentFailedError(f"Unhandled artifact fetch error for {rel_url!r}")  # pragma: no cover
