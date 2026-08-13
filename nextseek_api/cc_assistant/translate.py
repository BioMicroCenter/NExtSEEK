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

# The tool-input key whose value is the most useful one-line summary, per tool.
_TOOL_DETAIL_KEY = {
    "bash": "command",
    "read": "file_path",
    "write": "file_path",
    "edit": "file_path",
    "multiedit": "file_path",
    "notebookedit": "notebook_path",
    "glob": "pattern",
    "grep": "pattern",
    "webfetch": "url",
    "websearch": "query",
    "task": "description",
}


def _clip(text: Any, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_tool_detail(name: str, tool_input: Any) -> str:
    """One-line summary of a tool_use's input (the bash command, the file path,
    the grep pattern, ...). Empty when there's nothing useful to show."""
    if not isinstance(tool_input, dict):
        return ""
    key = _TOOL_DETAIL_KEY.get((name or "").strip().lower())
    if key is not None:
        val = tool_input.get(key)
        return _clip(val) if isinstance(val, str) and val.strip() else ""
    # Unknown tool: first non-empty string value.
    for val in tool_input.values():
        if isinstance(val, str) and val.strip():
            return _clip(val)
    return ""


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
        # Claude Code's OWN in-container session UUID (from system.init/result).
        # Deliberately surfaced on terminal frames as ``cc_session_id`` — NOT
        # ``session_id`` — so ``make_db_event_callback``'s setdefault fills
        # ``session_id`` with the NExtSEEK ChatSession id. Leaking it as
        # ``session_id`` caused the multi-turn 404 (frontend promoted the new
        # chat's active session from this value). Kept for later ``--resume``.
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
             "cc_session_id": self.session_id},
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
        # Text in a message that also calls a tool is narration ("let me read
        # X"), not the answer — surface it as a thinking step. Text in a
        # tool-free message is answer text, accumulated for the reply.
        has_tool = any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content
        )
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if has_tool:
                    frames.extend(self._thinking_frames(text))
                else:
                    self._reply_parts.append(text)
            elif btype == "thinking" and isinstance(block.get("thinking"), str):
                frames.extend(self._thinking_frames(block["thinking"]))
            elif btype == "tool_use":
                name = block.get("name") or "tool"
                tool_id = block.get("id")
                if isinstance(tool_id, str):
                    self._open_tools[tool_id] = name
                data: dict[str, Any] = {"source": name}
                detail = _format_tool_detail(name, block.get("input"))
                if detail:
                    data["detail"] = detail
                frames.append(("search_started", data))
        return frames

    def _thinking_frames(self, text: str) -> list[Frame]:
        """A thinking/narration block renders as one completed step carrying the
        text (it is instantaneous, so start + complete back-to-back)."""
        if not text.strip():
            return []
        return [
            ("search_started", {"source": "thinking", "detail": _clip(text)}),
            ("search_complete", {"source": "thinking"}),
        ]

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
                data: dict[str, Any] = {"source": name or "tool"}
                if bool(block.get("is_error")):
                    data["ok"] = False
                frames.append(("search_complete", data))
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
                {"error": str(detail), "agent": "container_cc", "cc_session_id": self.session_id},
            )]
        # Prefer Claude's own final `result` text; fall back to accumulated text.
        reply = payload.get("result")
        if not isinstance(reply, str) or not reply.strip():
            reply = self._joined_reply()
        return [(
            "query_complete",
            {"reply": reply or "(no response)", "bundle_id": None,
             "cc_session_id": self.session_id,
             # Surface Claude Code's own accrued spend so the caller can ledger it
             # (the per-turn cost lives only on the terminal `result` frame).
             "total_cost_usd": payload.get("total_cost_usd"),
             "num_turns": payload.get("num_turns"),
             "duration_ms": payload.get("duration_ms")},
        )]

    # ------------------------------------------------------------------ helpers
    def _joined_reply(self) -> str:
        return "\n\n".join(p for p in self._reply_parts if p).strip()
