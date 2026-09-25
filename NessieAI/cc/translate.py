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
isolation; the one exception, ``chat_nextseek.model_prices`` (itself stdlib only), is
imported lazily to price a turn on the NS table. Claude stream-json event shapes are documented in
dmac_assistant/src/dmac_assistant/streamjson.py and ws.py.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

Frame = tuple[str, dict[str, Any]]

# Operator-approved (2026-09-25): what the user is told when a turn ended because the
# model was unavailable. The first when a second model was tried this turn (Claude Code
# switched to --fallback-model, which it does on a 5xx or a 529), the second otherwise
# (it never falls back on a 429 or a timeout, or no fallback model was set).
MODEL_UNAVAILABLE_TRIED = (
    "The AI model was unavailable during this turn (we also tried a second model), "
    "so I could not finish. Please ask again in a few minutes."
)
MODEL_UNAVAILABLE = (
    "The AI model was unavailable during this turn, so I could not finish. "
    "Please ask again in a few minutes."
)
MODEL_UNAVAILABLE_REASON = "model_unavailable"

# Claude Code's own text when the model could not be reached: "API Error: 503 Service
# Unavailable...", "API Error: Repeated 529 Overloaded errors...", "API Error: Request
# rejected (429)...", "Request timed out". A result frame's ``api_error_status`` of 429
# or any 5xx says the same thing.
_MODEL_UNAVAILABLE_TEXT = re.compile(
    r"API Error: (?:5\d\d\b|Repeated 529\b|Request rejected \(429\))|^\s*Request timed out\b"
)


def _cost_by_price_table(model_usage: Any, usage: Any) -> float | None:
    """What a Container-CC turn cost on this repo's price table, or None.

    Claude Code prices Bedrock on its own table: ``modelUsage`` says ``costBasis:
    "list"``, the first-party list price, with no US-geo premium, so its
    ``total_cost_usd`` does not compare with an NS turn's. This prices the same
    ``modelUsage`` on ``NessieAI/chat_nextseek/model_prices.json``, the table every NS
    turn and the router are priced on (``chat_nextseek.model_prices.call_cost``), per
    model: ``outputTokens`` already holds the thinking (``thinkingTokens`` is a part of
    it). Cache writes are not split by TTL per model; they are priced at the 5-minute
    and 1-hour rates in the proportion the frame's ``usage.cache_creation`` gives, else
    at 5 minutes.

    The auto-mode classifier's calls are NOT in ``modelUsage`` (a local 2.1.282 run;
    not yet confirmed live), so this number, like Claude Code's own, leaves them out.
    None when there is no ``modelUsage`` or any model in it has no price: a number
    that covers only some of the turn's models would not compare.
    """
    if not isinstance(model_usage, dict) or not model_usage:
        return None
    try:
        from chat_nextseek import model_prices
    except ImportError:
        return None
    creation = (usage or {}).get("cache_creation") if isinstance(usage, dict) else None
    one_hour = five_min = 0
    if isinstance(creation, dict):
        one_hour = int(creation.get("ephemeral_1h_input_tokens") or 0)
        five_min = int(creation.get("ephemeral_5m_input_tokens") or 0)
    share_1h = one_hour / (one_hour + five_min) if one_hour + five_min else 0.0
    total = 0.0
    try:
        for model, counts in model_usage.items():
            if not isinstance(counts, dict):
                return None
            written = int(counts.get("cacheCreationInputTokens") or 0)
            written_1h = round(written * share_1h)
            cost = model_prices.call_cost(str(model), {
                "prompt_tokens": counts.get("inputTokens"),
                "completion_tokens": counts.get("outputTokens"),
                "cache_read_tokens": counts.get("cacheReadInputTokens"),
                "cache_write_5m_tokens": written - written_1h,
                "cache_write_1h_tokens": written_1h,
            }).cost_usd
            if cost is None:
                return None
            total += cost
    except Exception:  # a missing or malformed price table: no comparable number
        return None
    return round(total, 10)


def _model_unavailable(payload: dict[str, Any], text: str) -> bool:
    status = payload.get("api_error_status")
    if isinstance(status, int) and not isinstance(status, bool) and (
            status == 429 or 500 <= status <= 599):
        return True
    return bool(_MODEL_UNAVAILABLE_TEXT.search(text))

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

    # Class-level defaults so a translator built without ``__init__`` (the result-meta
    # tests do) still answers ``_handle_result``.
    model_id: str | None = None
    api_retries: int = 0
    _init_model: str | None = None
    _fallbacks: tuple[dict[str, Any], ...] | list[dict[str, Any]] = ()
    # (type, system subtype) of the last frame handled, and the last api_retry frame.
    _last_frame: tuple[Any, Any] | None = None
    _last_api_retry: dict[str, Any] | None = None
    # Monotonic time the last api_retry frame arrived (see ``last_api_retry_at``).
    _last_api_retry_at: float | None = None

    def __init__(self, model_id: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # Read once per api_retry frame, so the engine can tell a turn still waiting on
        # the retried request from one whose retried request is streaming its answer.
        self._clock = clock
        # The ``--model`` id this turn was started with: what ``models_used`` names when
        # the result frame carries no ``modelUsage``.
        self.model_id = model_id
        # Each ``system/model_fallback`` frame, in the turn-record contract's shape.
        self._fallbacks = []
        # ``system/api_retry`` frames seen: Claude Code retrying a failed model call.
        self.api_retries = 0
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
        self._last_frame = (etype, payload.get("subtype") if etype == "system" else None)
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
             "cc_session_id": self.session_id,
             "models_used": self._models_used(None),
             "model_fallback": self.model_fallback},
        )]

    @property
    def accumulated_reply(self) -> str:
        return self._joined_reply()

    @property
    def model_fallback(self) -> list[dict[str, Any]]:
        """Every model switch this turn, ``[]`` when nothing fell back (a copy)."""
        return [dict(item) for item in self._fallbacks]

    @property
    def retrying_model(self) -> dict[str, Any] | None:
        """The last ``system/api_retry`` frame when it is the last frame of all, else None.

        Set, the turn was waiting on a model call Claude Code was retrying: nothing the
        agent did came after it. The engine reads it when its watchdog stops a turn.
        """
        if self._last_frame == ("system", "api_retry"):
            return self._last_api_retry
        return None

    @property
    def last_api_retry_at(self) -> float | None:
        """When (on the translator's clock) the last ``system/api_retry`` frame arrived.

        Claude Code prints no frame while a response streams, so a retry that worked
        stays the last frame until the answer is complete: ``retrying_model`` alone
        cannot tell waiting from streaming. The time can.
        """
        return self._last_api_retry_at

    def model_unavailable_error(self) -> str:
        """The approved text for a turn the model's unavailability ended: the "second
        model" wording only when a fallback was recorded this turn."""
        return MODEL_UNAVAILABLE_TRIED if self._fallbacks else MODEL_UNAVAILABLE

    # ----------------------------------------------------------------- handlers
    def _handle_system(self, payload: dict[str, Any]) -> list[Frame]:
        sid = payload.get("session_id")
        if isinstance(sid, str):
            self.session_id = sid
        subtype = payload.get("subtype")
        if subtype == "init" and not self._started:
            self._started = True
            data: dict[str, Any] = {"agent": "container_cc"}
            model = payload.get("model")
            if isinstance(model, str):
                data["model"] = model
                self._init_model = model
            return [("agent_started", data)]
        # Claude Code 2.1.282 reports a --fallback-model switch as its own frame. Recorded
        # for the turn record, never shown as a step or taken into the reply.
        if subtype == "model_fallback":
            self._fallbacks.append({
                "agent": "container_cc",
                "from": payload.get("original_model"),
                "to": payload.get("fallback_model"),
                "reason": payload.get("trigger"),
            })
        elif subtype == "api_retry":
            self.api_retries += 1
            self._last_api_retry = payload
            self._last_api_retry_at = self._clock()
        # "informational", "permission_denied" and any other notice: nothing to do.
        return []

    def _handle_assistant(self, payload: dict[str, Any]) -> list[Frame]:
        # Claude Code's own "API Error: ..." message, written as an assistant turn just
        # before an error result. It is not the agent's answer, so it must not become
        # the reply a stream that ends early falls back to.
        if payload.get("is_api_error_message") is True:
            return []
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
            detail = str(payload.get("result") or payload.get("error") or payload.get("subtype")
                         or "container error")
            if _model_unavailable(payload, detail):
                # The model could not be reached: the user gets the approved plain text,
                # and Claude Code's own words stay in ``detail`` for whoever triages it.
                return [(
                    "query_error",
                    {"error": self.model_unavailable_error(),
                     "reason": MODEL_UNAVAILABLE_REASON, "detail": detail,
                     "agent": "container_cc", "cc_session_id": self.session_id,
                     "model_fallback": self.model_fallback},
                )]
            return [(
                "query_error",
                {"error": detail, "agent": "container_cc", "cc_session_id": self.session_id,
                 "model_fallback": self.model_fallback},
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
             # The same turn on the NS price table, so the engines compare (fix 6a).
             "cost_by_price_table_usd": _cost_by_price_table(
                 payload.get("modelUsage"), payload.get("usage")),
             "num_turns": payload.get("num_turns"),
             "duration_ms": payload.get("duration_ms"),
             # The turn record: which models answered, and what fell back.
             "models_used": self._models_used(payload.get("modelUsage")),
             "model_fallback": self.model_fallback},
        )]

    # ------------------------------------------------------------------ helpers
    def _models_used(self, model_usage: Any) -> list[str]:
        """The model ids that answered this turn.

        The result frame's ``modelUsage`` keys when it has any. Otherwise the model
        that was answering when the turn ended: the last fallback's target, else the
        ``--model`` id, else the model the init frame named.
        """
        if isinstance(model_usage, dict) and model_usage:
            return [str(key) for key in model_usage]
        for item in reversed(self._fallbacks):
            if isinstance(item.get("to"), str) and item["to"]:
                return [item["to"]]
        model = self.model_id or self._init_model
        return [model] if model else []

    def _joined_reply(self) -> str:
        return "\n\n".join(p for p in self._reply_parts if p).strip()

    def partial_reply(self) -> str:
        """What the agent had said when a turn was stopped before it finished.

        F20: a timed-out turn publishes the files it wrote and sends them with the
        error, so the work survives, but the user gets no text at all -- no partial
        answer and no account of how far it got. The agent's own words exist only in
        the transcript row. This is the same accumulation the terminal reply falls
        back to, exposed so the timeout path can use it.
        """
        return self._joined_reply()
