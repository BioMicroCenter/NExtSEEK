"""WebSocket frame capture + wait_for_query_complete().

Playwright exposes `page.on("websocket", ws => ws.on("framereceived", ...))`.
This module wraps that into a simple buffer + blocking wait API.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class WSCapture:
    """Buffer every JSON-decodable WS frame the page receives."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._complete_payload: dict | None = None

    def attach(self, page: Any) -> None:
        """Wire page.on('websocket') → ws.on('framereceived') → self._on_frame."""
        def _on_ws(ws: Any) -> None:
            ws.on("framereceived", self._on_frame)
        page.on("websocket", _on_ws)

    def _on_frame(self, payload: Any) -> None:
        # Playwright passes either str or bytes; the WS event envelope is JSON.
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8")
            frame = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return
        if not isinstance(frame, dict):
            return
        with self._lock:
            self.frames.append(frame)
            if frame.get("event") == "query_complete":
                self._complete_payload = frame.get("data") or {}
                self._event.set()

    def reset_for_next_turn(self) -> None:
        """Clear the complete flag so a second wait_for_query_complete works."""
        with self._lock:
            self._complete_payload = None
            self._event.clear()

    def dump(self, path: Path) -> None:
        with self._lock:
            frames = list(self.frames)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for f in frames:
                fh.write(json.dumps(f) + "\n")


def wait_for_query_complete(cap: WSCapture, *, timeout_s: float = 90.0) -> dict:
    """Block until a query_complete frame arrives; return its data payload.

    Raises TimeoutError on timeout.
    """
    if cap._event.wait(timeout=timeout_s):
        return cap._complete_payload or {}
    raise TimeoutError(f"query_complete frame did not arrive within {timeout_s}s")
