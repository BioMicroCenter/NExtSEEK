"""Synchronous HTTP client for NExtSEEK native granular-op endpoints (T15, A-5).

Calls POST /nextseek_api/assistant/{op}/ with HTTP Basic auth and maps the
NExtSEEK error envelope ({code, errors}) onto the sidecar exception classes
defined in ops.py that server.py's catch-ladder turns into §12 codes.

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
    OpValidationError,
    TransportError,
    WriteBlockedError,
)

# Timeout (seconds) for all NExtSEEK granular-op calls (DD-A5-1).
_TIMEOUT = 60.0


def _map_error(resp: httpx.Response) -> None:
    """Inspect a non-2xx response and raise the appropriate sidecar exception.

    Error mapping (DD-A5-2):
      - 401                       → AuthFailedError
      - 4xx/5xx with body code:
          VALIDATION              → OpValidationError
          WRITE_BLOCKED           → WriteBlockedError
          CONFIG_ERROR /
          CONFIG_MISSING          → AgentFailedError
          any other 403           → AuthFailedError  (non-participant)
          everything else         → AgentFailedError
    """
    status = resp.status_code
    if status == 401:
        raise AuthFailedError("NExtSEEK returned 401 Unauthorized")

    # Try to parse the error envelope.
    try:
        body = resp.json()
    except Exception:
        body = {}

    code = body.get("code", "")

    if code == "VALIDATION":
        raise OpValidationError(f"NExtSEEK VALIDATION: {body.get('errors', [])}")
    if code == "WRITE_BLOCKED":
        raise WriteBlockedError(f"NExtSEEK WRITE_BLOCKED: {body.get('errors', [])}")
    if code in ("CONFIG_ERROR", "CONFIG_MISSING"):
        raise AgentFailedError(f"NExtSEEK {code}: {body.get('errors', [])}")

    # Any other 403 (e.g. non-participant, no code match) → AuthFailedError.
    if status == 403:
        raise AuthFailedError(f"NExtSEEK returned 403 (code={code!r}): {body.get('errors', [])}")

    # Everything else (5xx, unexpected 4xx, …) → AgentFailedError.
    raise AgentFailedError(
        f"NExtSEEK returned HTTP {status} (code={code!r}): {body.get('errors', [])}"
    )


def call_op(
    op: str,
    body: dict,
    *,
    base_url: str,
    auth: tuple[str, str],
) -> dict:
    """POST a granular op to NExtSEEK and return the parsed JSON response dict.

    Args:
        op:       Sidecar op name (e.g. "entity", "report").
        body:     Request payload dict (forwarded as JSON).
        base_url: NExtSEEK base URL, e.g. "http://nextseek_nginx".
        auth:     (api_user, api_pass) tuple for HTTP Basic auth.

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
        resp = httpx.post(url, json=body, auth=auth, timeout=_TIMEOUT)
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
    auth: tuple[str, str],
) -> bytes:
    """GET a report/submission artifact from NExtSEEK and return raw bytes.

    Args:
        rel_url:  Server-relative URL from download.artifacts[].url.
        base_url: NExtSEEK base URL, e.g. "http://nextseek_nginx".
        auth:     (api_user, api_pass) tuple for HTTP Basic auth.

    Returns:
        Raw artifact bytes.

    Raises:
        AuthFailedError:  401 or non-participant 403.
        AgentFailedError: Unexpected HTTP error.
        TransportError:   httpx.HTTPError (connect / read timeout).
    """
    url = f"{base_url}{rel_url}"
    try:
        resp = httpx.get(url, auth=auth, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise TransportError(f"HTTP transport error fetching artifact {url}: {exc}") from exc

    if resp.is_success:
        return resp.content

    _map_error(resp)
    raise AgentFailedError(f"Unhandled artifact fetch error for {rel_url!r}")  # pragma: no cover
