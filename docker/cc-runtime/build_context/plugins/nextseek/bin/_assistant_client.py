"""Typed httpx client for NExtSEEK's assistant viewset (U-3, OD-6). No chat_nextseek.

Auth = per-call user NS login as Basic (recon:nsApi §2 _check_auth accepts Basic).
Transport = POST query/async/ -> 202 AsyncQueryResponse, then GET tasks/{task_id}/progress/
polling until a terminal event (query_complete | query_error) appears in the progress list.
The assistant_prefix (with/without the i18n locale segment) is resolved by T0a.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from _assistant_models import (
    AsyncQueryResponse,
    QueryCompleteEvent,
    QueryErrorEvent,
    QueryRequest,
    SessionDetailResponse,
    TaskProgressResponse,
)

# Sentinel string emitted when polling ends without a terminal event.
# runner_ns._STREAM_ENDED_WITHOUT_TERMINAL must equal this value (drift-pinned in test_runner_ns.py).
STREAM_ENDED_SENTINEL = "stream ended without terminal event"

# Module-level sleep and monotonic clock so tests can monkeypatch them.
_sleep = time.sleep
_monotonic = time.monotonic

_DEFAULT_POLL_INTERVAL: float = 0.5

# Bounded retry budget for the idempotent progress GET. Over the
# agent-container -> host.docker.internal link the progress endpoint
# intermittently stalls past request_timeout, raising httpx.ReadTimeout
# (a TransportError, NOT an HTTPStatusError). The GET is idempotent and
# seen_count dedups re-issued responses, so we retry it a bounded number of
# times with short backoff -- always under the run_query polling deadline.
# (W3 deferred follow-up.)
# NOTE: retry count and request_timeout are COUPLED. A fully-stalled link burns
# up to _PROGRESS_GET_MAX_RETRIES * request_timeout (4 * 30s = 120s) per poll
# iteration, plus backoff, against the 300s poll deadline. Raising this without
# revisiting request_timeout shrinks the polling headroom.
_PROGRESS_GET_MAX_RETRIES: int = 4
# Linear backoff base between progress-GET retry attempts (seconds). Kept well
# under the poll deadline; injectable via the module-level _sleep so tests
# never sleep for real.
_PROGRESS_GET_RETRY_BACKOFF: float = 0.5


class AssistantClient:
    def __init__(self, *, base_url: str, assistant_prefix: str, auth: tuple[str, str],
                 timeout: float = 300.0, request_timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None,
                 poll_interval: float = _DEFAULT_POLL_INTERVAL) -> None:
        """Create an AssistantClient.

        Args:
            timeout: Total polling deadline in seconds. The run_query loop gives up
                after this many seconds have elapsed since the POST (default 300).
            request_timeout: Per-request httpx timeout in seconds applied to every
                individual HTTP call -- POST query/async/, GET progress, and the
                session/bundle/artifact helpers (default 30). Kept short so a single
                hung GET does not consume the entire polling deadline. Artifact downloads
                use the same 30 s value; this is intentionally conservative -- callers
                that need longer artifact downloads can raise this knob.
        """
        self._base = base_url.rstrip("/")
        self._prefix = assistant_prefix.strip("/")
        self._auth = auth
        self._timeout = timeout
        self._request_timeout = request_timeout
        self._transport = transport
        self._poll_interval = poll_interval

    def _url(self, suffix: str) -> str:
        return f"{self._base}/{self._prefix}/{suffix.lstrip('/')}"

    def _client(self) -> httpx.Client:
        return httpx.Client(auth=self._auth, timeout=self._request_timeout, transport=self._transport)

    def run_query(self, query: str, *, mode: str, session_id: str | None = None,
                  force_new: bool = False,
                  on_event: Callable[[str, dict], None] | None = None,
                  ) -> tuple[dict, list[tuple[str, dict]]]:
        """POST query/async/ then poll tasks/{task_id}/progress/ until terminal.

        Returns (terminal_payload, [(event_name, data), ...]).
        The terminal_payload shapes are:
          - query_complete:        dict(data) from the progress event
          - query_error:           {"__error__": ..., "agent": ..., "session_id": ...}
          - status=completed+result: dict(result) passed through directly
          - status=error+result:   {"__error__": ..., "agent": ..., "session_id": ...}
          - no terminal / timeout: {"__error__": STREAM_ENDED_SENTINEL, "agent": None}
        If on_event is provided, it is called for each new event as it arrives
        (incremental, no duplicates). NOTE: on_event is called BEFORE terminal model
        validation; the runner's allowlist filter means terminal event names never
        reach the on_event callback in the runner use-case. Exceptions from on_event
        propagate.
        """
        body = QueryRequest(query=query, mode=mode,
                            session_id=session_id, force_new=force_new).model_dump(
                                mode="json", exclude_none=True)
        events: list[tuple[str, dict]] = []
        terminal: dict | None = None

        with self._client() as client:
            # Step 1: POST query/async/
            post_resp = client.post(self._url("query/async/"), json=body)
            post_resp.raise_for_status()
            async_resp = AsyncQueryResponse(**post_resp.json())
            task_id = str(async_resp.task_id)

            # Step 2: Poll loop
            seen_count = 0  # index into the append-only progress list
            deadline = _monotonic() + self._timeout

            while True:
                if _monotonic() >= deadline:
                    terminal = {"__error__": STREAM_ENDED_SENTINEL, "agent": None}
                    break

                # The progress GET is idempotent (seen_count dedups re-issued
                # responses), so transport/timeout stalls are retried with
                # bounded short backoff, never past the polling deadline. A
                # real 4xx/5xx (HTTPStatusError from raise_for_status) is NOT
                # retried -- it must surface unchanged. If every attempt raises
                # a transport/timeout error and the deadline has not been hit,
                # the last exception is re-raised so runner_ns's
                # `except httpx.TransportError` branch maps it to the
                # TRANSPORT_ERROR typed failure (a clear transport outcome,
                # not a misleading query_complete-with-error). If the deadline
                # is reached mid-retry, we fall back to the same stream-ended
                # sentinel the timeout path uses.
                task_progress = None
                last_transport_exc: BaseException | None = None
                for attempt in range(_PROGRESS_GET_MAX_RETRIES):
                    if _monotonic() >= deadline:
                        break
                    try:
                        progress_resp = client.get(
                            self._url(f"tasks/{task_id}/progress/")
                        )
                        progress_resp.raise_for_status()
                        task_progress = TaskProgressResponse(**progress_resp.json())
                        break
                    except httpx.TransportError as exc:
                        # Idempotent GET stalled (ReadTimeout/ConnectError/etc.);
                        # retry under the deadline. httpx.TimeoutException is a
                        # SUBCLASS of TransportError, so this covers all timeouts.
                        # HTTPStatusError is NOT a TransportError and is
                        # intentionally NOT caught here (real 4xx/5xx still surface).
                        last_transport_exc = exc
                        if attempt + 1 < _PROGRESS_GET_MAX_RETRIES:
                            _sleep(_PROGRESS_GET_RETRY_BACKOFF * (attempt + 1))

                if task_progress is None:
                    # All attempts exhausted (or deadline hit during retries).
                    if _monotonic() >= deadline:
                        # Deadline ceiling reached: same terminal as the
                        # top-of-loop timeout path.
                        terminal = {"__error__": STREAM_ENDED_SENTINEL, "agent": None}
                        break
                    # Retries exhausted before the deadline with a real
                    # transport/timeout failure -- surface it (do NOT treat as
                    # success). runner_ns maps it to TRANSPORT_ERROR.
                    assert last_transport_exc is not None
                    raise last_transport_exc

                # Guard against server restart / task eviction shrinking the list.
                if len(task_progress.progress) < seen_count:
                    raise RuntimeError(
                        "progress list shrank; server restarted or task evicted"
                    )

                # Process any new events since the last poll
                new_events = task_progress.progress[seen_count:]
                for pe in new_events:
                    name = pe.event
                    data = dict(pe.data)
                    events.append((name, data))
                    # NOTE: on_event is called before terminal model validation below.
                    if on_event is not None:
                        on_event(name, data)
                    if name == "query_complete":
                        QueryCompleteEvent(**data)
                        terminal = dict(data)
                    elif name == "query_error":
                        QueryErrorEvent(**data)
                        terminal = {
                            "__error__": data.get("error", ""),
                            "agent": data.get("agent"),
                            "session_id": data.get("session_id"),
                        }
                seen_count = len(task_progress.progress)

                if terminal is not None:
                    break

                # Check if the task reached a terminal status without a terminal event
                # in progress (fallback: use result dict)
                if task_progress.status in ("completed", "error"):
                    result = task_progress.result
                    if result:
                        if task_progress.status == "error":
                            terminal = {
                                "__error__": str(result.get("error") or "task ended with status=error"),
                                "agent": result.get("agent"),
                                "session_id": result.get("session_id"),
                            }
                        else:
                            terminal = dict(result)
                    else:
                        terminal = {"__error__": STREAM_ENDED_SENTINEL, "agent": None}
                    break

                _sleep(self._poll_interval)

        return terminal, events

    def session_detail(self, session_id: str, *, include_turns: bool = False) -> dict:
        params = {"include": "turns"} if include_turns else None
        with self._client() as client:
            r = client.get(self._url(f"sessions/{session_id}/"), params=params)
            r.raise_for_status()
            data = r.json()
            SessionDetailResponse(**data)
            return data

    def download_bundle(self, session_id: str, bundle_id: int) -> dict:
        with self._client() as client:
            r = client.get(self._url(f"sessions/{session_id}/bundles/{bundle_id}/"))
            r.raise_for_status()
            return r.json()

    def download_artifact(self, session_id: str, bundle_id: int, artifact_key: str) -> bytes:
        with self._client() as client:
            r = client.get(self._url(f"sessions/{session_id}/bundles/{bundle_id}/artifacts/{artifact_key}/"))
            r.raise_for_status()
            return r.content
