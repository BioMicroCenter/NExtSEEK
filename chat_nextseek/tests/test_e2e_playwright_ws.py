"""WebSocket sniffer tests — use a fake page/ws that simulates frame arrival."""
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from e2e.playwright.ws import WSCapture, wait_for_query_complete


class _FakeWS:
    def __init__(self):
        self.url = "ws://localhost:8000/ws/chat/"
        self._listeners: dict[str, list] = {"framereceived": [], "framesent": []}

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    def emit(self, event, payload):
        for cb in self._listeners.get(event, []):
            cb(payload)


class _FakePage:
    def __init__(self):
        self._listeners: dict[str, list] = {"websocket": []}

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    def emit_ws(self, ws):
        for cb in self._listeners.get("websocket", []):
            cb(ws)


def test_ws_capture_records_received_frames():
    page = _FakePage()
    cap = WSCapture()
    cap.attach(page)
    ws = _FakeWS()
    page.emit_ws(ws)

    frame = json.dumps({"event": "agent_started", "data": {"agent": "entity"}})
    ws.emit("framereceived", frame)

    assert len(cap.frames) == 1
    assert cap.frames[0]["event"] == "agent_started"


def test_ws_capture_ignores_non_json_frames():
    page = _FakePage()
    cap = WSCapture()
    cap.attach(page)
    ws = _FakeWS()
    page.emit_ws(ws)

    ws.emit("framereceived", "not json {{{")
    assert cap.frames == []


def test_wait_for_query_complete_resolves_when_event_arrives():
    page = _FakePage()
    cap = WSCapture()
    cap.attach(page)
    ws = _FakeWS()
    page.emit_ws(ws)

    def fire_after_delay():
        time.sleep(0.1)
        ws.emit("framereceived", json.dumps({
            "event": "query_complete",
            "data": {"reply": "done", "session_id": "abc"},
        }))

    threading.Thread(target=fire_after_delay, daemon=True).start()
    payload = wait_for_query_complete(cap, timeout_s=2)
    assert payload["reply"] == "done"
    assert payload["session_id"] == "abc"


def test_wait_for_query_complete_times_out():
    page = _FakePage()
    cap = WSCapture()
    cap.attach(page)
    _FakeWS()  # never attached or emits

    with pytest.raises(TimeoutError) as exc:
        wait_for_query_complete(cap, timeout_s=0.5)
    assert "query_complete" in str(exc.value)


def test_dump_frames_writes_jsonl(tmp_path):
    page = _FakePage()
    cap = WSCapture()
    cap.attach(page)
    ws = _FakeWS()
    page.emit_ws(ws)

    ws.emit("framereceived", json.dumps({"event": "a", "data": {}}))
    ws.emit("framereceived", json.dumps({"event": "b", "data": {}}))

    out = tmp_path / "ws_frames.jsonl"
    cap.dump(out)
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "a"
    assert json.loads(lines[1])["event"] == "b"
