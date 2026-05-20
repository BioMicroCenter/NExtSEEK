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

    # ---------- Build mode-appropriate debug JSON ----------
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
        search_total = None
        search_preview_count = 0
        if isinstance(api_result_full, dict):
            api_data_full = api_result_full.get("data")
            if isinstance(api_data_full, dict):
                search_total = (
                    api_data_full.get("total")
                    or api_data_full.get("total_samples")
                    or api_data_full.get("total_nodes")
                )
                preview_items = (
                    api_data_full.get("rows")
                    if isinstance(api_data_full.get("rows"), list)
                    else api_data_full.get("nodes")
                    if isinstance(api_data_full.get("nodes"), list)
                    else api_data_full.get("data")
                    if isinstance(api_data_full.get("data"), list)
                    else []
                )
                search_preview_count = len(preview_items)

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

    # ---------- Build messages ----------
    if is_graph:
        graph_plan_json = json.dumps(graph_plan, separators=(",", ":"))
        records = ((graph_result or {}).get("data") or [])[:20]
        preview_json = json.dumps(records, separators=(",", ":"), default=str)
        count = (graph_result or {}).get("count", 0)
        ok = (graph_result or {}).get("ok", False)
        error_str = (graph_result or {}).get("error", "")
        user_content = (
            "User question:\n"
            f"{user_query}\n\n"
            "Graph query plan (Cypher + explanation + parameters):\n"
            f"{graph_plan_json}\n\n"
            f"Query status: {'success' if ok else 'failed'}\n"
            f"Records returned: {count}\n"
            f"{'Error: ' + error_str if error_str else ''}\n\n"
            "Result preview (up to 20 records):\n"
            f"{preview_json}\n\n"
            "MODE: graph_query — Summarize what the graph query found."
        )
        log_label = "chatter_graph"
    elif is_reporter:
        summary_json = json.dumps(reporter_summary, separators=(",", ":"))
        user_content = (
            "User question:\n"
            f"{user_query}\n\n"
            "Reporter summary JSON:\n"
            f"{summary_json}\n\n"
            "MODE: reporter — Write a concise narrative summary using ONLY the reporter summary JSON."
        )
        log_label = "chatter_report"
    else:
        plan_json = json.dumps(parser_plan, separators=(",", ":"))
        api_plan_json = json.dumps(api_plan, separators=(",", ":"))
        api_json = json.dumps(api_result_slim, separators=(",", ":"))
        error_json = json.dumps(error_context, separators=(",", ":")) if error_context else ""
        user_content = (
            "User question:\n"
            f"{user_query}\n\n"
            "Parser plan JSON:\n"
            f"{plan_json}\n\n"
            "API plan JSON:\n"
            f"{api_plan_json}\n\n"
            "NExtSEEK API result JSON (slimmed):\n"
            f"{api_json}\n\n"
            "Deterministic result stats:\n"
            f"total_matches={search_total}\n"
            f"preview_items={search_preview_count}\n\n"
            f"Error context (if any):\n{error_json}\n\n"
            "MODE: search — Answer the question using ONLY the information from the API result.\n"
            "Treat the deterministic result stats above as authoritative for whether results exist."
        )
        log_label = "chatter"

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

