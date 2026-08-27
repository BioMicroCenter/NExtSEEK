"""Poll-based progress capture — the replacement for the dead WebSocket capture
(``ws.py:WSCapture`` / ``wait_for_query_complete``).

Why this exists
---------------
The merged NExtSEEK stack serves the assistant over Django/gunicorn (WSGI) with
``CHANNEL_LAYERS = InMemoryChannelLayer`` and no ASGI websocket worker, so the
frontend's ``query_complete`` websocket frame never arrives and ``WSCapture``
times out. The working progress channel is the HTTP poll endpoint
``GET /nextseek_api/cc-assistant/tasks/{task_id}/progress/`` (see
``nextseek_api/services/cc_assistant.py::CCAssistantViewSet.task_progress``),
which returns::

    {"task_id", "session_id", "status", "progress": [{"event", "data"}, ...], "result"}

``status`` is one of ``pending|running|completed|error`` (``QueryTask.STATUS_CHOICES``
in ``nextseek_api/assistant/models_db.py``); the terminal ``query_complete``
event lives inside ``progress``. ``PollCapture`` drives an injected
``get_progress`` callable until ``status`` is terminal and returns that final
payload. Time is injected (``now``/``sleep``) so tests stay deterministic.

Route awareness (load-bearing — NS and CC persist different shapes)
------------------------------------------------------------------
The merged chat UI POSTs to ``cc-assistant/query/async``, whose BAML router
(``nextseek_api/cc_assistant/router.py``) dispatches each turn to either:

- **NS route** (the deterministic ``chat_nextseek`` pipeline via
  ``pipeline_adapter._emit_complete``): ``query_complete.data`` carries a
  ``debug`` dict (``parser_plan``/``api_plan``/``entity_result``/
  ``api_result_meta``/``graph_plan``/``graph_result``/``reporter_plan``) plus
  ``files``. **No per-turn cost is persisted** — the NS branch in
  ``services/cc_assistant.py`` (``if decision.route == ROUTE_NS``) runs
  ``run_query(...)`` and its only persistence is ``adapter.save()``; there is no
  ``cc_engine.run_cc_turn`` / ``on_turn_complete`` and therefore no
  ``cc_traces`` row.
- **CC route** (``cc_engine.run_cc_turn(..., on_turn_complete=...)``):
  ``query_complete.data`` carries ``reply`` + ``total_cost_usd`` +
  ``cc_session_id`` and **no** ``debug``; the cost is also persisted to
  ``extra_state.cc_traces[*]``.

So the runner must classify each completed payload by shape and gate cost only
on the CC route. ``detect_route`` / ``cc_cost`` below do that classification.
"""
from __future__ import annotations

import time
from typing import Any, Callable

_TERMINAL = {"completed", "error"}


class PollCapture:
    """Poll a progress endpoint until a task reaches a terminal status."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sleep = sleep
        self._now = now

    def poll_until_complete(
        self,
        get_progress: Callable[[str], dict],
        task_id: str,
        *,
        timeout_s: float = 180.0,
        interval_s: float = 2.0,
    ) -> dict[str, Any]:
        """Poll ``get_progress(task_id)`` until terminal status; return that payload.

        Always polls at least once (an already-terminal task returns even with
        ``timeout_s == 0``). Raises ``TimeoutError`` once a non-terminal status
        persists past ``timeout_s``. ``timeout_s`` defaults to 180 — the CC hard
        cap; the NS route is ~15-20s, so it is generous.
        """
        deadline = self._now() + timeout_s
        last_status: Any = None
        while True:
            payload = get_progress(task_id) or {}
            last_status = payload.get("status")
            if last_status in _TERMINAL:
                return payload
            if self._now() >= deadline:
                raise TimeoutError(
                    f"task {task_id} still {last_status!r} after {timeout_s}s "
                    f"(polled every {interval_s}s)"
                )
            self._sleep(interval_s)


# ── payload → criteria adapters ──────────────────────────────────────────────


def _last_event(progress: list, event: str) -> dict | None:
    """Last event of a given name (``query_complete`` is terminal → one per task)."""
    found = None
    for ev in progress or []:
        if isinstance(ev, dict) and ev.get("event") == event:
            found = ev
    return found


def query_complete_data(payload: dict) -> dict:
    """The ``query_complete`` event's ``data``.

    NS: has ``debug`` + ``files``; CC: has ``total_cost_usd`` + ``cc_session_id``
    and no ``debug``. Both carry ``reply``. Returns ``{}`` if the task errored
    before emitting a ``query_complete`` event.
    """
    qc = _last_event(payload.get("progress") or [], "query_complete")
    return (qc or {}).get("data") or {}


def detect_route_from_data(data: dict) -> str:
    """Classify a ``query_complete.data`` dict by shape: ``ns`` | ``cc`` | ``unknown``."""
    if "debug" in data:
        return "ns"
    if "total_cost_usd" in data or "cc_session_id" in data:
        return "cc"
    return "unknown"


def detect_route(payload: dict) -> str:
    """Classify a completed poll payload by its ``query_complete.data`` shape."""
    return detect_route_from_data(query_complete_data(payload))


def cc_cost_from_data(data: dict) -> float | None:
    """CC per-turn spend from the terminal frame's accrued ``total_cost_usd``.

    ``None`` for an NS payload (NS persists no per-turn cost)."""
    v = data.get("total_cost_usd")
    return float(v) if isinstance(v, (int, float)) else None


def cc_cost(payload: dict) -> float | None:
    return cc_cost_from_data(query_complete_data(payload))


def build_debug(payload: dict) -> dict:
    """Return the criteria-shaped ``debug`` dict for a completed **NS-route** poll
    payload (the structure ``e2e/criteria.py::resolve_field`` navigates).

    Source of truth is ``query_complete.data.debug`` (persisted by
    ``pipeline_adapter.py``), defensively backfilling the ok-flags from the
    ``search_complete`` event when the persisted debug omits them (observed
    shape: ``search_complete.data.{source, ok}``). Not valid for a CC payload
    (which carries no ``debug``).
    """
    progress = payload.get("progress") or []
    qc_data = query_complete_data(payload)
    debug: dict[str, Any] = dict(qc_data.get("debug") or {})

    # Defensive backfill from the search_complete event (only when the persisted
    # debug lacks the ok flag). No-op for payloads whose debug is complete.
    sc = _last_event(progress, "search_complete")
    scd = (sc or {}).get("data") or {}
    if scd.get("source") == "api" and (debug.get("api_result_meta") or {}).get("ok") is None:
        arm = dict(debug.get("api_result_meta") or {})
        arm["ok"] = bool(scd.get("ok"))
        debug["api_result_meta"] = arm
    if scd.get("source") == "neo4j" and (debug.get("graph_result") or {}).get("ok") is None:
        gr = dict(debug.get("graph_result") or {})
        gr["ok"] = bool(scd.get("ok"))
        debug["graph_result"] = gr
    return debug


def artifact_files(payload: dict) -> list[dict]:
    """The artifacts the UI actually offers as downloads for this turn.

    The frontend renders download buttons from ``query_complete.data.artifacts``
    filtered to ``artifact_type in {file, table}`` (``useMessages.ts`` →
    ``ReportArtifacts.tsx``), and those entries carry the **un-suffixed** key
    that both the download button (``data-filename={file.key}``) and the download
    endpoint use (verified live: key ``geo_seq_workbooks`` → HTTP 200, but the
    flat ``data.files`` manifest suffixes list-valued artifacts to
    ``geo_seq_workbooks_0`` → 404). So prefer ``data.artifacts`` file/table
    entries; fall back to the flat ``data.files`` only when none are present.
    """
    data = query_complete_data(payload)
    arts = data.get("artifacts")
    if isinstance(arts, list):
        ui = [
            a for a in arts
            if isinstance(a, dict)
            and a.get("artifact_type") in ("file", "table")
            and a.get("key")
        ]
        if ui:
            return ui
    files = data.get("files")
    return files if isinstance(files, list) else []
