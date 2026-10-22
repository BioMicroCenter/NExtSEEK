"""Full-agentic nf-core pipeline agent.

One BedrockClient.chat_with_tools conversation per session. The LLM picks the
pipeline, judges data-type fit, builds cohorts, and asks the user when unsure.
Six tools (select_pipeline, resolve_samples, write_samplesheet, configure_run,
submit_to_luria, conclude) do the deterministic I/O; select_pipeline judges the
pipeline from the cohort's own evidence, write_samplesheet builds the CSV
(rejecting refs the agent didn't resolve), configure_run builds params.yml +
launch.yml.

Public surface (unchanged contract with the orchestrator):
- is_active(session) -> bool
- start(session, config, *, user_query, parser_plan, reporter_plan, log_dir=None, send_event=None) -> {action, reply, params}
- handle_turn(session, config, user_text, *, log_dir=None, send_event=None) -> {action, reply, params}
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

from .agent_tools import build_pipeline_tool_schemas, dispatch_pipeline_tool_call, format_luria_followup
from ..tool_loop import call_tools
from ..chat_memory import bundle_rows, history_block, named_bundle
from ..helpers import summarize_pinned_bundle, uids_from_last_search
from ..helpers.results import picked_uids, pinned_bundle
from ..helpers.uid_check import uids_in
from ..seqera.catalog import catalog_for_prompt

PIPELINE_AGENT_KEY = "pipeline_agent"
# 12 covered the five-tool loop. select_pipeline consumes one more before
# resolve_samples ever runs, and a 'fork' verdict spends a further round-trip
# waiting for the user, so the old budget would run out mid-build.
MAX_ITER = 13
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
        "selection": state.get("selection") or {},
        "source_bundle_id": state.get("source_bundle_id"),
        "cohort_count": len(artifacts.get("cohorts") or []),
        "message_count": len(state.get("messages") or []),
    }


def _wants_cancel(text: str) -> bool:
    norm = (text or "").strip().lower()
    return norm in CANCEL_TOKENS or norm.startswith("/cancel")


def _text_of(content: list) -> str:
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()


#: The most carried UIDs the seed lists; the rest are counted, so a 351-sample set does not flood the build.
CARRIED_UIDS_MAX = 50


def _carried_uids(parser_plan: Any, user_query: str) -> list[str]:
    """The UIDs in ``parser_plan.filters.uids`` that ``user_query`` does not already name, upper-cased, once each."""
    plan = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else parser_plan
    uids = ((plan or {}).get("filters") or {}).get("uids") if isinstance(plan, dict) else None
    named = set(uids_in(user_query or ""))
    return [u for u in uids_in("", [u for u in uids or [] if isinstance(u, str)]) if u not in named]


def _plan_uids(parser_plan: Any) -> list:
    """``parser_plan.filters.uids`` as the parser wrote them, from a model or a dict; [] for the CC bridge."""
    plan = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else parser_plan
    uids = ((plan or {}).get("filters") or {}).get("uids") if isinstance(plan, dict) else None
    return uids if isinstance(uids, list) else []


def _target_id(parser_plan: Any) -> Any:
    """The result the parser says the user means (``target_result_id``), from a model or a dict; None for the CC bridge."""
    plan = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else parser_plan
    return plan.get("target_result_id") if isinstance(plan, dict) else None


def _no_samples_reply(target: dict, offered: dict | None) -> str:
    """The deterministic ask when the result the user means holds no sample list: a count or a breakdown, or a
    search that found nothing. It offers only what the build can do: the offered set, explicit UIDs, or a new
    search."""
    what = "is a count or a breakdown" if bundle_rows(target) else "found no samples"
    reply = (f"The result you mean ({target.get('user_query') or 'that result'!r}) {what}, "
             "so it has no list of samples to build a pipeline on. ")
    if offered is None:
        return reply + "Run a search for the samples first, or give me their UIDs."
    n = len(uids_from_last_search({"results_history": [offered]}, offered.get("id")))
    return (reply + f"The newest result with samples is {offered.get('user_query') or 'an earlier search'!r} "
            f"({n} samples). Say yes to use those, give me their UIDs, or ask a new search.")


def start(session, config: "ChatConfig", *, user_query: str, parser_plan: Any = None,
          reporter_plan: Any = None, log_dir: str | None = None,
          send_event=None) -> dict[str, Any]:
    """Launch a fresh pipeline conversation.

    The build is on one results bundle, ``source_bundle_id``, which the tools read too: the one the parser
    named in ``target_result_id`` ("the mice from my first search"), else the newest with sample UIDs. A named
    result with no sample list (a count) is not built on: the agent asks and offers the newest set, and a "yes"
    builds on exactly that one. With no named result the wizard also seeds from the sample UIDs ``parser_plan``
    carried that the text does not name: a follow-up such as "yes, build it" names no samples, and the parser
    copies the earlier turn's into ``filters.uids`` (dev chat 0ddfba7a). ``reporter_plan`` is unused. The CC
    bridge (run_pipeline_launch) calls this with neither.
    """
    seed = user_query
    history = session.get("results_history") or []
    target = named_bundle(history, _target_id(parser_plan))
    messages: list[dict] = [{"role": "user", "content": seed}]
    if target is not None:
        uids = uids_from_last_search(session, target.get("id"))
        if uids:
            source_id = target.get("id")
            summary = summarize_pinned_bundle(session, source_id).removeprefix("last search: ")
            seed += f"\n\n[context] the result you mean: id {source_id}, {summary}, {len(uids)} sample UIDs"
            picked = picked_uids(uids, _plan_uids(parser_plan))
            if picked:
                more = len(picked) - CARRIED_UIDS_MAX
                seed += (f"\n\n[context] the user picked {len(picked)} samples of it: "
                         + ", ".join(picked[:CARRIED_UIDS_MAX]) + (f", and {more} more" if more > 0 else "")
                         + ". Build on exactly these.")
            earlier = history_block(session)
            if earlier:
                seed += f"\n\n[context] earlier turns of this chat:\n{earlier}"
            messages = [{"role": "user", "content": seed}]
        else:
            offered, _ = pinned_bundle(session)
            reply = _no_samples_reply(target, offered)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
            _save(session, {"active": True, "messages": messages, "resolved": {"uids": [], "accessions": []},
                            "artifacts": {}, "pipeline_key": None,
                            "source_bundle_id": offered.get("id") if offered else None})
            return {"action": "ask", "reply": reply, "params": None}
    else:
        pinned = summarize_pinned_bundle(session)
        if pinned:
            seed += f"\n\n[context] {pinned}"
        carried = _carried_uids(parser_plan, user_query)
        if carried:
            more = len(carried) - CARRIED_UIDS_MAX
            seed += ("\n\n[context] samples from the earlier turn: " + ", ".join(carried[:CARRIED_UIDS_MAX])
                     + (f", and {more} more" if more > 0 else ""))
        messages = [{"role": "user", "content": seed}]
        source_id = (pinned_bundle(session)[0] or {}).get("id")
    state = {
        "active": True,
        "messages": messages,
        "resolved": {"uids": [], "accessions": []},
        "artifacts": {},
        "pipeline_key": None,
        "source_bundle_id": source_id,
    }
    _save(session, state)
    return _run_loop(session, config, log_dir=log_dir, send_event=send_event)


def handle_turn(session, config: "ChatConfig", user_text: str, *, log_dir: str | None = None,
                send_event=None) -> dict[str, Any]:
    state = _state(session)
    if not state.get("active"):
        return {"action": "passthrough", "reply": "", "params": None}
    if _wants_cancel(user_text):
        clear(session)
        return {"action": "cancel", "reply": "Cancelled. Ask me a fresh question whenever you're ready.", "params": None}
    state.setdefault("messages", []).append({"role": "user", "content": user_text})
    _save(session, state)
    return _run_loop(session, config, log_dir=log_dir, send_event=send_event)


def _run_loop(session, config: "ChatConfig", *, log_dir: str | None, send_event=None) -> dict[str, Any]:
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
        # Through call_tools, not chat_with_tools directly: a 503 anywhere in a build
        # used to end it outright, the tokens a 12-iteration loop spent were invisible
        # to the ledger, and the stable head (tools + system prompt) was re-sent
        # uncached on every iteration.
        resp = call_tools(
            config,
            messages=messages,
            tools=build_pipeline_tool_schemas(config),
            system=system_prompt,
            model_name=model_name,
            client=client,
            agent_label=PIPELINE_AGENT_KEY,
        )
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
                                                     name=name, tool_input=tool_input,
                                                     log_dir=log_resolved_dir,
                                                     send_event=send_event)
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
        followup = format_luria_followup(artifacts.get("luria_runs"),
                                         artifacts.get("luria_ssh_target"))
        return {"action": "execute", "reply": (message + "\n" + followup) if followup else message,
                "params": {"artifacts": artifacts}}
    if outcome == "cancelled":
        return {"action": "cancel", "reply": message or "Cancelled.", "params": None}
    return {"action": "ask", "reply": message, "params": None}
