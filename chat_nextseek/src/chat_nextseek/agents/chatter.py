from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ..session import SessionState
from ..config import ChatConfig
from ..llm_clients import LLMAPIConnectionError, LLMRateLimitError
from ..helpers import (
    log_prompt,
    log_usage,
)
from ..schemas import (
    PlannerOutput,
)


def chatter_agent_answer(
    config: ChatConfig,
    user_query: str,
    entity_result: dict,
    parser_plan: dict,
    api_plan: dict | None = None,
    api_result_slim: dict | None = None,
    api_result_full: dict | None = None,
    error_context: dict | None = None,
    reporter_summary: dict | None = None,
    graph_plan: dict | None = None,
    graph_result: dict | None = None,
    log_dir: str | None = None,
    session: "SessionState | SessionStateProxy | None" = None,
) -> str:
    """
    Unified chatter agent: produces a narrative answer for search, reporter, and graph results,
    followed by a structured debug JSON block showing key inter-agent data.
    Pass reporter_summary for reporter mode, graph_plan+graph_result for graph mode,
    or API params for search/refine mode.
    Falls back to informative messages when the LLM hits rate or connection limits.
    """
    is_reporter = reporter_summary is not None
    is_graph = graph_plan is not None

    # ---------- Build mode-appropriate debug JSON (UI debug panel only) ----------
    # This JSON is for power-user inspection in the UI — it intentionally
    # keeps plumbing (endpoint, requestBody, Cypher). The LLM prompt built
    # further below does NOT see any of this.
    if is_graph:
        debug_info = {
            "entity": {
                "sampletypes": entity_result.get("sampletypes", []),
                "assays": entity_result.get("assays", []),
                "projects": entity_result.get("projects", []),
            },
            "parser": {
                "mode": parser_plan.get("mode"),
                "intent_summary": parser_plan.get("intent_summary"),
            },
            "graph": {
                "cypher": graph_plan.get("cypher"),
                "explanation": graph_plan.get("explanation"),
                "parameters": graph_plan.get("parameters"),
            },
            "neo4j": {
                "ok": (graph_result or {}).get("ok"),
                "count": (graph_result or {}).get("count"),
                "error": (graph_result or {}).get("error"),
            },
        }
    elif is_reporter:
        debug_info = {
            "entity": {
                "sampletypes": entity_result.get("sampletypes", []),
                "assays": entity_result.get("assays", []),
                "projects": entity_result.get("projects", []),
            },
            "parser": {
                "mode": parser_plan.get("mode"),
                "report_mode": parser_plan.get("report_mode"),
                "report_type": parser_plan.get("report_type"),
            },
        }
    else:
        debug_info = {
            "entity": {
                "sampletypes": entity_result.get("sampletypes", []),
                "assays": entity_result.get("assays", []),
                "projects": entity_result.get("projects", []),
                "filters": parser_plan.get("filters", {}),
            },
            "parser": {
                "mode": parser_plan.get("mode"),
                "target_endpoint": parser_plan.get("target_endpoint"),
                "endpoint_candidates": parser_plan.get("endpoint_candidates", []),
            },
            "api_agent": {
                "requestBody": (api_plan or {}).get("requestBody") or {},
                "queryParameters": (api_plan or {}).get("queryParameters") or {},
            },
        }
    debug_json = json.dumps(debug_info, indent=2)

    # ---------- Build LLM user_content (UNIVERSAL across modes) ----------
    # The LLM never sees endpoint names, Cypher, requestBody, or filter
    # operators. It gets a uniform structure: question + resolved entities
    # + mode-specific data + result stats + brief instructions.

    def _fmt_entities(items: Any) -> str:
        if not items:
            return "(none)"
        parts = []
        for it in items:
            if isinstance(it, dict):
                code = it.get("code", "")
                name = it.get("name", "")
                if code and name and code != name:
                    parts.append(f"{code} ({name})")
                else:
                    parts.append(code or name or "?")
            else:
                parts.append(str(it))
        return ", ".join(parts) if parts else "(none)"

    resolved_sampletypes = _fmt_entities(entity_result.get("sampletypes"))
    resolved_assays = _fmt_entities(entity_result.get("assays"))
    resolved_projects = _fmt_entities(entity_result.get("projects"))
    keywords_list = (entity_result.get("filters") or {}).get("keywords") or []
    keywords_str = ", ".join(keywords_list) if keywords_list else "(none)"

    # Compute total_matches + preview_count for the mode at hand.
    total_matches: Any = None
    preview_count = 0
    if is_graph:
        total_matches = (graph_result or {}).get("count")
        preview_count = len(((graph_result or {}).get("data") or [])[:20])
    elif is_reporter and isinstance(reporter_summary, dict):
        total_matches = (
            reporter_summary.get("total_rows")
            or reporter_summary.get("total")
            or reporter_summary.get("count")
        )
    elif not is_graph and not is_reporter and isinstance(api_result_full, dict):
        api_data_full = api_result_full.get("data")
        if isinstance(api_data_full, dict):
            total_matches = (
                api_data_full.get("total")
                or api_data_full.get("total_samples")
                or api_data_full.get("total_nodes")
            )
            preview_items = (
                api_data_full.get("rows") if isinstance(api_data_full.get("rows"), list)
                else api_data_full.get("nodes") if isinstance(api_data_full.get("nodes"), list)
                else api_data_full.get("data") if isinstance(api_data_full.get("data"), list)
                else []
            )
            preview_count = len(preview_items)

    # Mode-specific data section (the actual answer payload).
    if is_graph:
        records = ((graph_result or {}).get("data") or [])[:20]
        preview_json = json.dumps(records, separators=(",", ":"), default=str)
        ok = (graph_result or {}).get("ok", False)
        error_str = (graph_result or {}).get("error", "")
        data_section = (
            f"Graph result preview (up to 20 records):\n{preview_json}\n"
            f"Query status: {'success' if ok else 'failed'}"
            + (f"\nError: {error_str}" if error_str else "")
        )
        mode_label = "graph_query"
        log_label = "chatter_graph"
    elif is_reporter:
        summary_json = json.dumps(reporter_summary, separators=(",", ":"))
        data_section = f"Aggregated project report:\n{summary_json}"
        mode_label = "reporter"
        log_label = "chatter_report"
    else:
        api_json = json.dumps(api_result_slim, separators=(",", ":"))
        data_section = f"Matching sample records (slimmed):\n{api_json}"
        if error_context:
            error_json = json.dumps(error_context, separators=(",", ":"))
            data_section += f"\n\nError context:\n{error_json}"
        mode_label = "search"
        log_label = "chatter"

    user_content = (
        f"User question:\n{user_query}\n\n"
        "What the user asked for:\n"
        f"- Sample types: {resolved_sampletypes}\n"
        f"- Assays: {resolved_assays}\n"
        f"- Projects: {resolved_projects}\n"
        f"- Keywords: {keywords_str}\n\n"
        f"{data_section}\n\n"
        "Result statistics:\n"
        f"- Total matches: {total_matches if total_matches is not None else 'unknown'}\n"
        f"- Preview rows shown: {preview_count}\n\n"
        f"MODE: {mode_label}\n\n"
        "Instructions for this turn:\n"
        "- Lead with the count or key finding.\n"
        "- Use resolved entity NAMES (e.g. 'Non-Human Primate'), not codes alone, when introducing the result.\n"
        "- Mention 2-3 example identifiers (UIDs, names) from the preview verbatim if available.\n"
        "- Do NOT describe HOW the data was retrieved — no endpoint names, no Cypher, "
        "no API mechanics, no filter operators (AND/OR), no requestBody."
    )

    from ..chat_memory import history_block

    messages: list[dict] = [{"role": "system", "content": config.CHATTER_SYSTEM_PROMPT}]
    chat_history = history_block(session) if session is not None else ""
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    if not is_reporter and not is_graph and error_context:
        messages.append({
            "role": "system",
            "content": (
                "If error_context is present (API failure), summarize the likely cause and request any missing "
                "values explicitly (e.g., project IDs). Offer a short placeholder the user can fill, without inventing "
                "values. Keep the answer concise."
            ),
        })
    messages.append({"role": "user", "content": user_content})

    # ---------- LLM Call ----------
    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    try:
        resp = chatter_client.chat(
            model=chatter_model,
            temperature=0,
            messages=messages,
            thinking_budget=chatter_budget,
        )
        log_usage(resp, "CHATTER")
        answer = resp.content or ""
        print("[DEBUG][CHATTER] Raw answer:", answer)
        log_prompt(
            log_dir or config.LOG_DIR,
            log_label,
            {
                "user_query": user_query,
                "messages": messages,
                "response": answer,
            },
        )

    except LLMAPIConnectionError as e:
        print("[DEBUG][CHATTER] APIConnectionError:", repr(e))
        if is_reporter:
            return "Reporter completed, but had a connection issue summarizing the results."
        if is_graph:
            count = (graph_result or {}).get("count", 0)
            return f"Graph query returned {count} record(s), but had a connection issue summarizing the results."
        data = (api_result_slim or {}).get("data", {})
        total = data.get("total") if isinstance(data, dict) else None
        return (
            "I successfully queried NExtSEEK, but had a connection issue talking to the LLM to "
            "summarize the results.\n\n"
            f"Basic info:\n- endpoint: {parser_plan.get('target_endpoint')}\n"
            f"- intent: {parser_plan.get('intent_summary')}\n"
            f"- total matches: {total if total is not None else 'unknown'}\n\n"
            "You can re-run the query or refine it (e.g. by project or study) to narrow the results."
        )
    except LLMRateLimitError as e:
        print("[DEBUG][CHATTER] RateLimitError:", repr(e))
        if is_reporter:
            return "Reporter completed, but the summarization call hit the model's token/throughput limit."
        if is_graph:
            count = (graph_result or {}).get("count", 0)
            return f"Graph query returned {count} record(s), but hit the rate limit while summarizing. Try again shortly."
        data = (api_result_slim or {}).get("data", {})
        total = data.get("total") if isinstance(data, dict) else None
        return (
            "I pulled the NExtSEEK results, but the summarization call hit the model's token/throughput limit. "
            "Try again with a narrower query or after a short pause.\n\n"
            f"Basic info:\n- endpoint: {parser_plan.get('target_endpoint')}\n"
            f"- intent: {parser_plan.get('intent_summary')}\n"
            f"- total matches: {total if total is not None else 'unknown'}"
        )

    # ---------- Clean answer ----------
    answer_no_links = re.sub(r"https?://\S+", "", answer)
    answer_no_links = re.sub(r"\n{3,}", "\n\n", answer_no_links).strip()

    # ---------- Debug block ----------
    debug_block = (
        "**Debug info**\n\n"
        "```json\n"
        f"{debug_json}\n"
        "```"
    )

    final_answer = answer_no_links + "\n\n" + debug_block
    print("[DEBUG][CHATTER] Final answer (post-processed):", final_answer)
    return final_answer

def chatter_agent_plan(
    config: ChatConfig,
    user_query: str,
    plan: PlannerOutput,
    step_results: dict[int, dict],
    log_dir: str | None = None,
    session: "SessionState | SessionStateProxy | None" = None,
) -> str:
    """
    Chatter variant for the planner pipeline. Receives the full plan + all step results
    and produces a narrative weaving together what was found at each step.
    Appends a debug block with the plan JSON and per-step ok/count summary.
    """
    # Build step summaries for the debug block
    step_summary = []
    for step in plan.steps:
        sr = step_results.get(step.step_id, {})
        step_summary.append({
            "step_id": step.step_id,
            "tool": step.tool,
            "combine_mode": step.combine_mode,
            "ok": sr.get("ok"),
            "count": (sr.get("output") or {}).get("count"),
            "error": sr.get("error"),
        })
    intersection = step_results.get("intersection")
    if intersection:
        step_summary.append({
            "step_id": "intersection",
            "tool": "intersection",
            "combine_mode": "intersect",
            "ok": True,
            "count": (intersection.get("output") or {}).get("count"),
            "error": None,
        })

    debug_info = {
        "planner": {
            "intent_summary": plan.intent_summary,
            "step_count": len(plan.steps),
            "notes": plan.notes,
        },
        "steps": step_summary,
    }
    debug_json = json.dumps(debug_info, indent=2)

    # Build a step-by-step summary for the narrative prompt
    steps_for_prompt = []
    for step in plan.steps:
        sr = step_results.get(step.step_id, {})
        output = sr.get("output") or {}
        count = output.get("count", 0)
        ok = sr.get("ok", False)
        # Build a meaningful preview depending on tool type
        if step.tool == "reporter":
            reporter_summary = output.get("reporter_summary") or {}
            data_preview: Any = reporter_summary if reporter_summary else output.get("reporter_plan", {})
        elif isinstance(output.get("reply"), str) and output.get("reply", "").strip():
            data_preview = {"reply": output.get("reply")}
        else:
            data = output.get("data", [])
            data_preview = data[:10] if isinstance(data, list) else data
        steps_for_prompt.append(
            f"Step {step.step_id} [{step.tool}] combine_mode={step.combine_mode}: ok={ok}, count={count}\n"
            f"  query: {_step_query(step)}\n"
            f"  preview: {json.dumps(data_preview, default=str)[:2000]}"
        )
    if step_results.get("intersection"):
        isr = step_results["intersection"].get("output") or {}
        idata = isr.get("data", [])
        steps_for_prompt.append(
            f"INTERSECTION RESULT: count={isr.get('count', 0)}\n"
            f"  preview: {json.dumps(idata[:5], default=str)[:800]}"
        )

    steps_text = "\n\n".join(steps_for_prompt)

    from ..chat_memory import history_block

    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    messages: list[dict] = [
        {"role": "system", "content": config.CHATTER_SYSTEM_PROMPT},
    ]
    chat_history = history_block(session) if session is not None else ""
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    messages.extend([
        {
            "role": "system",
            "content": (
                "You are summarizing the results of a multi-step plan.\n\n"
                f"PLAN INTENT: {plan.intent_summary}\n\n"
                f"STEP RESULTS:\n{steps_text}"
            ),
        },
        {
            "role": "system",
            "content": f"```json\n{debug_json}\n```",
        },
        {"role": "user", "content": user_query},
    ])

    try:
        resp = chatter_client.chat(
            model=chatter_model,
            temperature=0.3,
            messages=messages,
            thinking_budget=chatter_budget,
        )
        narrative = resp.content or "(no response)"
    except Exception as e:
        print(f"[DEBUG][PLAN_CHATTER] failed: {e!r}")
        narrative = (
            f"I executed a {len(plan.steps)}-step plan for your query: {plan.intent_summary}.\n\n"
            + "\n".join(
                f"Step {s['step_id']} ({s['tool']}): {'✓' if s['ok'] else '✗'} "
                f"— {s['count'] or 0} result(s)"
                for s in step_summary
            )
        )

    log_prompt(
        log_dir or config.LOG_DIR,
        "plan_chatter",
        {"messages": messages, "response": narrative},
    )
    return f"{narrative}\n\n```json\n{debug_json}\n```"

