"""Translate Claude Code ``stream-json`` events into the NExtSEEK assistant
``{event, data}`` progress vocabulary that ``chat_frontend`` already renders.

This is the load-bearing adapter for the Container-CC route. The frontend only
understands these events (``useProcessingState.ts``): ``agent_started``,
``agent_complete``, ``search_started``, ``search_complete``, ``query_complete``,
``query_error``. Unknown events are ignored, and the final answer is whatever
arrives in ``query_complete.reply`` (a single Markdown string) — there is no
token streaming. So this translator maps Claude's native stream-json
(``system/init`` -> ``assistant`` text+tool_use blocks -> ``result``) onto that
contract: tool uses surface as ``search_started``/``search_complete`` steps,
assistant text is accumulated, and the terminal ``result`` becomes one
``query_complete``.

Pure stdlib (no Django, no docker, no dmac imports) so it is unit-testable in
isolation. Claude stream-json event shapes are documented in
dmac_assistant/src/dmac_assistant/streamjson.py and ws.py.
"""
from __future__ import annotations

from typing import Any

Frame = tuple[str, dict[str, Any]]


class CCStreamTranslator:
    """Stateful translator from Claude stream-json events to {event,data} frames.

    Usage::

        t = CCStreamTranslator()
        for payload in parsed_events:
            for event, data in t.handle(payload):
                send_event(event, data)
        for event, data in t.finalize():   # safety net if no `result` arrived
            send_event(event, data)
    """

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._reply_parts: list[str] = []
        # Pending tool_use ids -> tool name, so a later tool_result can close
        # the matching search_started with a search_complete.
        self._open_tools: dict[str, str] = {}
        self._started = False
        self._terminated = False

    # ------------------------------------------------------------------ public
    def handle(self, payload: dict[str, Any]) -> list[Frame]:
        """Map one parsed stream-json event to zero or more {event,data} frames."""
        if not isinstance(payload, dict):
            return []
        etype = payload.get("type")
        if etype == "system":
            return self._handle_system(payload)
        if etype == "assistant":
            return self._handle_assistant(payload)
        if etype == "user":
            return self._handle_user(payload)
        if etype == "result":
            return self._handle_result(payload)
        return []

    def finalize(self) -> list[Frame]:
        """Emit a terminal frame if the stream ended without a ``result`` event."""
        if self._terminated:
            return []
        self._terminated = True
        return [(
            "query_complete",
            {"reply": self._joined_reply() or "(no response)", "bundle_id": None,
             "session_id": self.session_id},
        )]

    @property
    def accumulated_reply(self) -> str:
        return self._joined_reply()

    # ----------------------------------------------------------------- handlers
    def _handle_system(self, payload: dict[str, Any]) -> list[Frame]:
        sid = payload.get("session_id")
        if isinstance(sid, str):
            self.session_id = sid
        if payload.get("subtype") == "init" and not self._started:
            self._started = True
            data: dict[str, Any] = {"agent": "container_cc"}
            model = payload.get("model")
            if isinstance(model, str):
                data["model"] = model
            return [("agent_started", data)]
        return []

    def _handle_assistant(self, payload: dict[str, Any]) -> list[Frame]:
        frames: list[Frame] = []
        content = (payload.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                self._reply_parts.append(block["text"])
            elif btype == "tool_use":
                name = block.get("name") or "tool"
                tool_id = block.get("id")
                if isinstance(tool_id, str):
                    self._open_tools[tool_id] = name
                frames.append(("search_started", {"source": name}))
        return frames

    def _handle_user(self, payload: dict[str, Any]) -> list[Frame]:
        # tool_result blocks arrive on synthetic `user` events; close the
        # matching search step so the frontend stops the spinner for it.
        frames: list[Frame] = []
        content = (payload.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                name = self._open_tools.pop(tool_id, None) if isinstance(tool_id, str) else None
                frames.append(("search_complete", {"source": name or "tool"}))
        return frames

    def _handle_result(self, payload: dict[str, Any]) -> list[Frame]:
        self._terminated = True
        sid = payload.get("session_id")
        if isinstance(sid, str):
            self.session_id = sid
        is_error = bool(payload.get("is_error")) or (
            isinstance(payload.get("subtype"), str)
            and payload.get("subtype") != "success"
        )
        if is_error:
            detail = payload.get("result") or payload.get("error") or payload.get("subtype") or "container error"
            return [(
                "query_error",
                {"error": str(detail), "agent": "container_cc", "session_id": self.session_id},
            )]
        # Prefer Claude's own final `result` text; fall back to accumulated text.
        reply = payload.get("result")
        if not isinstance(reply, str) or not reply.strip():
            reply = self._joined_reply()
        return [(
            "query_complete",
            {"reply": reply or "(no response)", "bundle_id": None,
             "session_id": self.session_id},
        )]

    # ------------------------------------------------------------------ helpers
    def _joined_reply(self) -> str:
        return "\n\n".join(p for p in self._reply_parts if p).strip()
