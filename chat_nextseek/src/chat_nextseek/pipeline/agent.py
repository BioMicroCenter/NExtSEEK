"""Full-agentic nf-core pipeline agent.

One BedrockClient.chat_with_tools conversation per session. The LLM picks the
pipeline, judges data-type fit, builds cohorts, and asks the user when unsure.
Five tools (resolve_samples, write_samplesheet, configure_run, submit_to_luria,
conclude) do the deterministic I/O; write_samplesheet builds the CSV (rejecting
refs the agent didn't resolve), configure_run builds params.yml + launch.yml.

Public surface (unchanged contract with the orchestrator):
- is_active(session) -> bool
- start(session, config, *, user_query, parser_plan, reporter_plan, log_dir=None) -> {action, reply, params}
- handle_turn(session, config, user_text, *, log_dir=None) -> {action, reply, params}
- clear(session)
- snapshot_for_chat_log(session) -> dict
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy
    from ..config import ChatConfig
    from ..session import SessionState

from .agent_tools import build_pipeline_tool_schemas, dispatch_pipeline_tool_call
from ..helpers import summarize_pinned_bundle
from ..seqera.catalog import catalog_for_prompt

PIPELINE_AGENT_KEY = "pipeline_agent"
MAX_ITER = 12
CANCEL_TOKENS = {"cancel", "/cancel", "abort", "never mind", "drop", "drop it"}


def _state(session) -> dict[str, Any]:
    raw = session.get(PIPELINE_AGENT_KEY)
    return raw if isinstance(raw, dict) else {}


def _save(session, state: dict) -> None:
    session[PIPELINE_AGENT_KEY] = state


def is_active(session) -> bool:
    return bool(_state(session).get("active"))


def clear(session) -> None:
    session[PIPELINE_AGENT_KEY] = {}


def snapshot_for_chat_log(session) -> dict[str, Any]:
    state = _state(session)
    if not state:
        return {}
    artifacts = state.get("artifacts") or {}
    return {
        "active": state.get("active"),
        "pipeline_key": state.get("pipeline_key"),
        "cohort_count": len(artifacts.get("cohorts") or []),
        "message_count": len(state.get("messages") or []),
    }


def _wants_cancel(text: str) -> bool:
    norm = (text or "").strip().lower()
    return norm in CANCEL_TOKENS or norm.startswith("/cancel")


def _text_of(content: list) -> str:
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()


def start(session, config: "ChatConfig", *, user_query: str, parser_plan: Any = None,
          reporter_plan: Any = None, log_dir: str | None = None) -> dict[str, Any]:
    """Launch a fresh pipeline conversation.

    ``parser_plan``/``reporter_plan`` are accepted for the reporter-branch caller
    (orchestrator.py) but are unused — the wizard seeds from ``user_query`` + pinned
    context only. The CC bridge (run_pipeline_launch) calls this with neither.
    """
    pinned = summarize_pinned_bundle(session)
    seed = user_query if not pinned else f"{user_query}\n\n[context] {pinned}"
    state = {
        "active": True,
        "messages": [{"role": "user", "content": seed}],
        "resolved": {"uids": [], "accessions": []},
        "artifacts": {},
        "pipeline_key": None,
    }
    _save(session, state)
    return _run_loop(session, config, log_dir=log_dir)


def handle_turn(session, config: "ChatConfig", user_text: str, *, log_dir: str | None = None) -> dict[str, Any]:
    state = _state(session)
    if not state.get("active"):
        return {"action": "passthrough", "reply": "", "params": None}
    if _wants_cancel(user_text):
        clear(session)
        return {"action": "cancel", "reply": "Cancelled. Ask me a fresh question whenever you're ready.", "params": None}
    state.setdefault("messages", []).append({"role": "user", "content": user_text})
    _save(session, state)
    return _run_loop(session, config, log_dir=log_dir)


def _run_loop(session, config: "ChatConfig", *, log_dir: str | None) -> dict[str, Any]:
    state = _state(session)
    client, model_name, _ = config.get_agent_model(PIPELINE_AGENT_KEY)
    if not callable(getattr(client, "chat_with_tools", None)):
        clear(session)
        return {"action": "ask", "params": None,
                "reply": "The pipeline agent needs a tool-capable (Bedrock) model. "
                         "It isn't configured in this profile — set AWS_BEARER_TOKEN_BEDROCK or use an anth/aws profile."}

    system_prompt = (config._load_prompt("pipeline_agent.txt")
                     .replace("{catalog}", catalog_for_prompt())
                     .replace("{launch_mode}", str(getattr(config, "PIPELINE_LAUNCH_MODE", "tower"))))
    messages = state["messages"]
    log_resolved_dir = log_dir or getattr(config, "LOG_DIR", ".")

    for _ in range(MAX_ITER):
        resp = client.chat_with_tools(messages=messages, tools=build_pipeline_tool_schemas(config),
                                      system=system_prompt, model=model_name)
        content = resp.get("content", []) or []
        tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]

        if not tool_use_blocks:
            # The agent produced text for the user (clarify / propose / report). Pause, stay active.
            reply = _text_of(content) or "(no response)"
            messages.append({"role": "assistant", "content": content})
            _save(session, state)
            return {"action": "ask", "reply": reply, "params": None}

        messages.append({"role": "assistant", "content": content})
        tool_results: list[dict] = []
        for block in tool_use_blocks:
            name, tool_input, tuid = block.get("name"), block.get("input", {}), block.get("id")
            if name == "conclude":
                return _conclude(session, state, tool_input)
            if name == "handoff":
                # The user moved on. Drop the build and let the orchestrator run
                # its normal parser for this turn (it clears us on passthrough).
                print(f"[DEBUG][PIPELINE] handoff: {tool_input.get('reason')!r}")
                clear(session)
                return {"action": "passthrough", "reply": "", "params": None}
            try:
                result = dispatch_pipeline_tool_call(config=config, session=session, state=state,
                                                     name=name, tool_input=tool_input, log_dir=log_resolved_dir)
            except Exception as exc:
                result = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            tool_results.append({"type": "tool_result", "tool_use_id": tuid, "content": result})
        messages.append({"role": "user", "content": tool_results})
        _save(session, state)

    _save(session, state)
    return {"action": "ask", "params": None,
            "reply": "I couldn't finish within my step budget. Tell me how to proceed, or say 'cancel'."}


def _conclude(session, state: dict, tool_input: dict) -> dict[str, Any]:
    outcome = tool_input.get("outcome") or "answered"
    message = tool_input.get("message") or ""
    artifacts = state.get("artifacts") or {}
    state["active"] = False
    _save(session, state)
    if outcome == "submitted":
        return {"action": "execute", "reply": message, "params": {"artifacts": artifacts}}
    if outcome == "cancelled":
        return {"action": "cancel", "reply": message or "Cancelled.", "params": None}
    return {"action": "ask", "reply": message, "params": None}
