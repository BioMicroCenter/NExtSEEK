from __future__ import annotations

import json
from typing import Any

from ..config import ChatConfig
from ..helpers import (
    log_prompt,
)
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    WizardAgentOutput,
)


class WizardToolLoopError(Exception):
    """Raised when the builder-step tool-use loop exceeds MAX_TOOL_ITERATIONS,
    when the LLM stops without calling finalize_turn, or when the resolved
    model client lacks function-calling support."""


MAX_TOOL_ITERATIONS = 5


def _wizard_agent_builder(
    *,
    config: ChatConfig,
    session: SessionState | SessionStateProxy,
    user_text: str,
    chat_history: str = "",
    history_messages: list[dict] | None = None,
) -> WizardAgentOutput:
    """Run one builder-step turn through an Anthropic-style function-calling loop.

    The LLM may call any of the tools in BUILDER_TOOL_SCHEMAS; it MUST call
    finalize_turn as its last tool. We dispatch each non-finalize tool via
    `dispatch_tool_call`, feed the result back as a user message, and exit
    when finalize_turn is encountered.

    If `history_messages` is provided, those alternating user/assistant turns
    are prepended to the messages list before the current `user_text`, so
    the builder LLM sees prior builder-step turns and can resolve pronouns
    / follow-ups without re-prompting the user. Defaults to None (no
    history; behavior identical to the pre-history call shape).

    Cap: MAX_TOOL_ITERATIONS=5 per turn. Beyond that, raise WizardToolLoopError.
    """
    from ..builder_tools import BUILDER_TOOL_SCHEMAS, dispatch_tool_call

    print(f"[DEBUG][WIZARD_BUILDER] enter user_text={user_text!r} prior_turns={len(history_messages) // 2 if history_messages else 0}")
    client, model_name, _budget = config.get_agent_model("wizard_builder")
    print(f"[DEBUG][WIZARD_BUILDER] resolved model_name={model_name!r} client_type={type(client).__name__}")
    if not callable(getattr(client, "chat_with_tools", None)):
        print(f"[DEBUG][WIZARD_BUILDER] FAIL: client has no callable chat_with_tools")
        raise WizardToolLoopError(
            f"Resolved LLM client {type(client).__name__!r} does not support "
            "chat_with_tools; the wizard_builder agent must be mapped to a "
            "function-calling-capable model (e.g. Anthropic via BedrockClient)."
        )

    system_prompt = _build_wizard_builder_system_prompt(
        session=session, config=config, chat_history=chat_history,
    )

    messages: list[dict] = list(history_messages or [])
    messages.append({"role": "user", "content": user_text})

    for i in range(MAX_TOOL_ITERATIONS):
        print(f"[DEBUG][WIZARD_BUILDER] iter={i} messages_len={len(messages)}")
        resp = client.chat_with_tools(
            messages=messages,
            tools=BUILDER_TOOL_SCHEMAS,
            system=system_prompt,
            model=model_name,
        )
        print(f"[DEBUG][WIZARD_BUILDER] iter={i} stop_reason={resp.get('stop_reason')!r} content_blocks={len(resp.get('content', []))}")
        if resp.get("stop_reason") != "tool_use":
            # LLM produced a plain-text answer and stopped without finalize_turn.
            # Treat text-only end_turn as an implicit finalize_turn(action="stay")
            # — the chat_log only stores reply previews (not the underlying
            # finalize_turn tool_use), so when history is replayed the LLM
            # naturally mimics that "just answer in text" pattern. action=advance
            # and action=cancel still need an explicit finalize_turn signal.
            text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
            if resp.get("stop_reason") == "end_turn" and text_blocks:
                reply_text = "".join(b.get("text", "") for b in text_blocks).strip()
                print(f"[DEBUG][WIZARD_BUILDER] implicit finalize_turn (end_turn with text, len={len(reply_text)})")
                return WizardAgentOutput(
                    action="stay", selection_updates={}, reply=reply_text,
                )
            print(f"[DEBUG][WIZARD_BUILDER] FAIL: stop_reason={resp.get('stop_reason')!r} content={resp.get('content', [])!r}")
            raise WizardToolLoopError(
                "Builder LLM stopped without calling finalize_turn "
                f"(stop_reason={resp.get('stop_reason')!r})."
            )

        tool_use_blocks = [b for b in resp.get("content", []) if b.get("type") == "tool_use"]
        # Track the raw assistant message for the next iteration's history.
        assistant_content = resp.get("content", [])
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict] = []
        finalize_payload: dict | None = None
        for block in tool_use_blocks:
            name = block.get("name")
            tool_input = block.get("input", {})
            tool_use_id = block.get("id")
            if name == "finalize_turn":
                # finalize_turn is the control-flow signal; do NOT dispatch.
                finalize_payload = tool_input
                print(f"[DEBUG][WIZARD_BUILDER] finalize_turn action={finalize_payload.get('action')!r} selection_keys={list((finalize_payload.get('selection_updates') or {}).keys())}")
                continue
            print(f"[DEBUG][WIZARD_BUILDER] dispatching tool={name!r} input_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else 'non-dict'}")
            result = dispatch_tool_call(
                config=config, session=session,
                tool_name=name, tool_input=tool_input,
            )
            # The Bedrock Converse path expects tool_result content; the
            # normalized shape we return from chat_with_tools is anthropic-
            # native, so we feed it back in anthropic-native shape.
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result if isinstance(result, str) else str(result),
            })

        if finalize_payload is not None:
            return WizardAgentOutput(
                action=finalize_payload.get("action", "stay"),
                selection_updates=finalize_payload.get("selection_updates") or {},
                reply=finalize_payload.get("reply", ""),
            )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    print(f"[DEBUG][WIZARD_BUILDER] FAIL: hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS} without finalize_turn")
    raise WizardToolLoopError(
        f"Builder LLM did not call finalize_turn within {MAX_TOOL_ITERATIONS} iterations."
    )


def _build_wizard_builder_system_prompt(
    *,
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    chat_history: str = "",
) -> str:
    """Compose the builder-step system prompt: base prompt + current state + chat history."""
    state = session.get("nfcore_wizard") or {}
    pipeline = state.get("pipeline") or "?"
    selection = state.get("selection") or {}
    pinned = state.get("pinned_context") or {}
    base_prompt = config.WIZARD_AGENT_SYSTEM_PROMPT or ""

    state_block = (
        "\n\n# CURRENT BUILDER STATE\n"
        f"pipeline: {pipeline}\n"
        f"selection.uids: {len(selection.get('uids') or [])} UIDs\n"
        f"selection.cohort_criteria: {selection.get('cohort_criteria') or []}\n"
        f"selection.enrichment_fields: {selection.get('enrichment_fields') or []}\n"
        f"pinned_context.source: {pinned.get('source', 'none')}\n"
        f"pinned_context.bundle_id: {pinned.get('bundle_id')}\n"
        "pinned_context.metadata_summary: "
        + (str(pinned.get('metadata_summary')) if pinned.get('metadata_summary') else "(not prefetched yet)")
    )
    parts = [base_prompt, state_block]
    if chat_history:
        parts.append("\n\n" + chat_history)
    return "".join(parts)


def wizard_agent(
    config: ChatConfig,
    *,
    step: str,
    user_text: str,
    wizard_state: dict,
    step_context: dict,
    chat_history: str = "",
    original_query: str = "",
    log_dir: str | None = None,
) -> WizardAgentOutput:
    """Per-turn LLM dispatcher for the nf-core wizard.

    Decides whether the user's reply answers the current step's question, is
    exploratory, or wants cancel/restart. Returns a `WizardAgentOutput` whose
    `extracted` shape depends on the step (see schema docstring).

    Falls back to a deterministic 'stay' answer if the LLM call fails so the
    wizard never crashes a user turn.
    """
    print(f"\n[DEBUG][WIZARD_AGENT] step={step!r} user_text={user_text!r}")

    state_json = json.dumps(
        {k: v for k, v in wizard_state.items() if k not in ("available_pipelines",)},
        default=str,
        indent=2,
    )
    context_json = json.dumps(step_context or {}, default=str, indent=2)
    if len(context_json) > 18000:
        context_json = context_json[:18000] + "\n…[step_context truncated]"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": config.WIZARD_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"CURRENT_STEP: {step}"},
        {"role": "system", "content": f"WIZARD_STATE:\n{state_json}"},
        {"role": "system", "content": f"STEP_CONTEXT:\n{context_json}"},
    ]
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    if original_query:
        messages.append(
            {"role": "system", "content": f"ORIGINAL_USER_QUERY (what triggered the wizard):\n{original_query}"}
        )
    messages.append({"role": "user", "content": user_text})

    client, model_name, budget = config.get_agent_model("wizard_agent")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_text,
            model=WizardAgentOutput,
            system=config.WIZARD_AGENT_SYSTEM_PROMPT,
            messages=messages,
            model_name=model_name,
            temperature=0,
            log_label="wizard_agent",
            usage_label="WIZARD_AGENT",
            thinking_budget=budget,
            client=client,
        )
        print(
            f"[DEBUG][WIZARD_AGENT] action={result.action} "
            f"extracted_keys={list(result.extracted.keys())} notes={result.notes!r}"
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "wizard_agent",
            {
                "step": step,
                "user_text": user_text,
                "wizard_state": wizard_state,
                "step_context_keys": list((step_context or {}).keys()),
                "messages": messages,
                "response": result.model_dump(),
            },
        )
        return result
    except Exception as e:
        print(f"[DEBUG][WIZARD_AGENT] LLM failed: {e!r}; falling back to stay")
        fallback = WizardAgentOutput(
            action="stay",
            extracted={},
            reply=(
                "I hit a snag interpreting that. Could you rephrase your answer "
                "for the current step?"
            ),
            notes=f"llm_error: {e!r}",
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "wizard_agent",
            {
                "step": step,
                "user_text": user_text,
                "error": repr(e),
                "messages": messages,
                "response": fallback.model_dump(),
            },
        )
        return fallback
