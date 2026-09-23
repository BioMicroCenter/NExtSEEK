from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ..session import SessionState
from ..config import ChatConfig
from ..llm_clients import LLMAPIConnectionError, LLMFatalError, LLMRateLimitError, LLMTimeoutError
from ..schemas.schema_helper import call_llm_text
from ..helpers import (
    log_prompt,
)
from ..helpers.query_scope import describe_query_scope, render_query_scope
from ..uid_links import link_sample_uids
from ..schemas import (
    PlannerOutput,
)
from .parser import _step_query

#: Keys on the REST result envelope that describe HOW the request was made rather
#: than WHAT came back. `slim_api_result_for_llm` copies them straight through from
#: `tool_nextseek_api_request`, so until D1 the prompt carried the full URL, the HTTP
#: verb, `page_size` and every requestBody field name. They stay on
#: `debug_payload["api_result_slim"]` and in the stored bundle, where a power user
#: inspects them; they are only removed from what the reply writer reads.
#: `page_size` joins them: it is the NAME of an API query parameter, and the three
#: disclosure flags the prompt actually has rules for (`rows_returned`,
#: `result_capped`, `total_matching`) already carry everything a reply needs about
#: the cap it describes.
_RESULT_PLUMBING_KEYS = frozenset({"url", "method", "query", "body", "endpoint", "page_size"})

#: The same cut on `error_context`. `status_code`, `error`, `response_preview` and
#: `schema_required_paths` are kept: they are the cause and the names of the values
#: the user has to supply, which the error-handling system message asks for.
_ERROR_PLUMBING_KEYS = frozenset({"url", "method", "request_body", "request_query"})


def _scrub_plumbing(payload: dict | None, keys: frozenset[str]) -> dict:
    """A shallow copy without the transport keys. Non-dicts pass through as {}."""
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k not in keys}


#: A graph row is a sample record when it carries a sample identity key; any other row is an
#: aggregate (a value and its count, a type and its count). Pilot A v2 (2026-09-18): the
#: Scientist-duplicates query returned all 216 stored names with counts and the writer was
#: handed 20 of them, so it could not see a single duplicate pair.
_PREVIEW_ROWS = 20
_AGGREGATE_ROWS_MAX = 500
_AGGREGATE_CHARS_MAX = 24_000


def _is_sample_key(key: Any) -> bool:
    k = str(key).lower()
    return k in {"id", "uuid", "uid"} or k.endswith(("_id", "_uuid", "_uid"))


def _is_count_only(rows: Any) -> bool:
    """One row whose every value is a number: an aggregate with no evidence beside it.

    The same single-row shape as ``matched_nothing`` (``helpers/tools/neo4j.py``) without
    requiring the numbers to be zero. A row of real data that happens to hold a count keeps
    its non-numeric values, and several rows are a breakdown the writer can reason over; a
    lone ``RETURN count(s) AS n`` leaves it holding nothing it can name.
    """
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or not rows[0]:
        return False
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rows[0].values())


def _graph_rows_for_writer(rows: list) -> list:
    """The graph rows the writer is shown: an aggregate whole, up to a size cap; a list of
    sample records as its first 20."""
    rows = list(rows or [])
    aggregate = bool(rows) and all(
        isinstance(r, dict) and not any(_is_sample_key(k) for k in r) for r in rows
    )
    if not aggregate:
        return rows[:_PREVIEW_ROWS]
    shown: list = []
    size = 2
    for row in rows[:_AGGREGATE_ROWS_MAX]:
        size += len(json.dumps(row, separators=(",", ":"), default=str)) + 1
        if size > _AGGREGATE_CHARS_MAX:
            break
        shown.append(row)
    return shown or rows[:_PREVIEW_ROWS]


def _breakdown_sum(rows: list) -> tuple[str, int | float] | None:
    """``(column, sum)`` when every row carries exactly the same one numeric column, over two
    or more rows: a breakdown whose total the question may ask for (Lung / lung / LUNG)."""
    if len(rows) < 2 or not all(isinstance(r, dict) for r in rows):
        return None
    numeric = [
        {k for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} for r in rows
    ]
    if any(len(cols) != 1 for cols in numeric) or len(set.union(*numeric)) != 1:
        return None
    col = next(iter(numeric[0]))
    return col, sum(r[col] for r in rows)


def _type_histogram_block(all_rows: list, shown: int) -> str:
    """How the sample types are distributed across the WHOLE result, not the preview.

    B8 (wesselr 437): a query returned a heterogeneous result and the writer was shown its
    first twenty rows, which happened to be one type. It named the whole result after that
    type. The rows are all in memory, so the distribution costs a pass over a list.

    Only emitted when it adds something the preview cannot show: the preview is short of
    the full set, and the full set holds more than one type.
    """
    if shown >= len(all_rows):
        return ""
    counts: dict[str, int] = {}
    for row in all_rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            k = str(key).lower()
            if (k == "type" or k.endswith("_type")) and isinstance(value, str) and value:
                counts[value] = counts.get(value, 0) + 1
                break
    if len(counts) < 2:
        return ""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    listed = ", ".join(f"{code} {n:,}" for code, n in ranked[:12])
    more = f", and {len(ranked) - 12} more" if len(ranked) > 12 else ""
    return (
        f"Sample types across ALL {len(all_rows):,} rows, not just the preview: {listed}{more}. "
        "The preview is the head of the result and is not representative: describe the result "
        "by this distribution, and never name it after the type that happens to appear first.\n"
    )


def _type_names_block(config: Any, rows: list) -> str:
    """Catalog names for the sample type codes in the rows, so the writer does not invent them
    (a Scientist-by-type question, Pilot A v2: D.MSP was called "Mass Spectrometry Peptide")."""
    catalog = getattr(config, "MIN_SAMPLETYPES", None)
    if not isinstance(catalog, list):
        return ""
    names = {
        str(item.get("SampleType")): str(item.get("Name"))
        for item in catalog
        if isinstance(item, dict) and item.get("SampleType") and item.get("Name")
    }
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            k = str(key).lower()
            if (k == "type" or k.endswith("_type")) and isinstance(value, str) and value in names:
                if value not in seen:
                    seen.append(value)
    if not seen:
        return ""
    return ("Sample type names for the codes in these rows (use these names; never invent one):\n"
            + "\n".join(f"- {code} = {names[code]}" for code in seen) + "\n")


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
    query_notes: list[str] | None = None,
) -> str:
    """
    Unified chatter agent: produces a narrative answer for search, reporter, and graph results,
    followed by a structured debug JSON block showing key inter-agent data.
    Pass reporter_summary for reporter mode, graph_plan+graph_result for graph mode,
    or API params for search/refine mode.
    Falls back to informative messages when the LLM hits rate or connection limits.

    ``query_notes`` are caveats the caller knows and the plans do not carry — the
    graph turn's ``graph_retry_changed_answer`` is the first one — each of which is
    disclosed to the writer verbatim.
    """
    is_reporter = reporter_summary is not None
    is_graph = graph_plan is not None

    # ---------- Build mode-appropriate debug JSON (UI debug panel only) ----------
    # This JSON is for power-user inspection in the UI — it intentionally
    # keeps plumbing (endpoint, requestBody, Cypher). The LLM prompt built further
    # below is assembled separately and none of these fields is passed into it; the
    # guard is tests/chat_nextseek/test_chatter_prompt.py, not this comment, which
    # said the same thing while the REST envelope carried the plumbing in anyway.
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
    # D1. This block used to claim "the LLM never sees endpoint names, Cypher,
    # requestBody, or filter operators", and on the REST path that was never true.
    # `tool_nextseek_api_request` returns {ok, url, status_code, method, query, body,
    # data} and `slim_api_result_for_llm` copies everything but `data` through
    # verbatim, so the prompt carried the full URL, the verb, `page_size` and the
    # requestBody field names — while the instruction block told the model not to
    # mention any of it. The mechanics were in the prompt and the useful form was not.
    #
    # Both halves are fixed here. `_scrub_plumbing` takes the raw envelope out, which
    # is what this comment always claimed; `describe_query_scope` puts in a structured,
    # user-facing account of what the executed query CONSTRAINED and what the user
    # asked for that it did not. That gap is what production issues B7, B8 and B13 all
    # needed and none of them had: a reply cannot avoid misreporting the question if
    # the writer has no way to know what ran. See helpers/query_scope.py.
    scope = describe_query_scope(
        entity_result=entity_result,
        parser_plan=parser_plan,
        api_plan=api_plan if not (is_graph or is_reporter) else None,
        graph_plan=graph_plan,
        extra_notes=query_notes,
        user_query=user_query,
    )

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
    # `keywords` is a TOP-LEVEL field on EntityAgentOutput (schemas/entity.py); there
    # has never been a `filters` key on it, so reading entity_result["filters"]
    # ["keywords"] rendered "(none)" in every turn, in every mode, since the line was
    # written. The system prompt's "state ... what the key filters were (sample type,
    # assay, keywords)" was unsatisfiable for keywords the whole time.
    keywords_list = [str(k) for k in (entity_result.get("keywords") or []) if str(k).strip()]
    keywords_str = ", ".join(keywords_list) if keywords_list else "(none)"

    # Compute total_matches + preview_count for the mode at hand, AND
    # pre-extract a few example identifiers so the LLM doesn't have to dig.
    total_matches: Any = None
    preview_count = 0
    example_ids: list[str] = []

    def _harvest_ids(items: Any, limit: int = 3) -> list[str]:
        """Pull UIDs/UUIDs/names from a list of row dicts. Order: uuid > uid > id > name."""
        out: list[str] = []
        for row in (items or [])[:limit * 2]:
            if not isinstance(row, dict):
                continue
            for key in ("uuid", "uid", "UID", "UUID", "id", "name", "title"):
                val = row.get(key)
                if isinstance(val, str) and val:
                    out.append(val)
                    break
            if len(out) >= limit:
                break
        return out

    graph_truncated = False
    graph_limit = None
    if is_graph:
        graph_data = ((graph_result or {}).get("data") or [])
        # `count` is len(records), so a query that hit its LIMIT used to report the
        # limit as the answer — graph.tissue_cell_impact's real total is 10,688 and
        # it reported 5000. Prefer the probed total when the result was truncated.
        graph_truncated = bool((graph_result or {}).get("truncated"))
        graph_limit = (graph_result or {}).get("limit")
        probed_total = (graph_result or {}).get("total")
        total_matches = probed_total if probed_total is not None else (graph_result or {}).get("count")
        graph_rows_shown = _graph_rows_for_writer(graph_data)
        preview_count = len(graph_rows_shown)
        example_ids = _harvest_ids(graph_data)
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
            # The API result uses different keys for the list of records
            # depending on endpoint: "samples" (sample search), "rows"
                                                            # (generic), "nodes" (graph-shaped),
            # "data" (catch-all). Check all of them.
            preview_items = (
                api_data_full.get("rows") if isinstance(api_data_full.get("rows"), list)
                else api_data_full.get("nodes") if isinstance(api_data_full.get("nodes"), list)
                else api_data_full.get("samples") if isinstance(api_data_full.get("samples"), list)
                else api_data_full.get("data") if isinstance(api_data_full.get("data"), list)
                else []
            )
            preview_count = len(preview_items)
            example_ids = _harvest_ids(preview_items)

    # Mode-specific data section (the actual answer payload).
    if is_graph:
        all_rows = (graph_result or {}).get("data") or []
        records = _graph_rows_for_writer(all_rows)
        preview_json = json.dumps(records, separators=(",", ":"), default=str)
        ok = (graph_result or {}).get("ok", False)
        error_str = (graph_result or {}).get("error", "")
        row_total = total_matches if isinstance(total_matches, int) else len(all_rows)
        complete = bool(records) and len(records) == len(all_rows) == row_total and not graph_truncated
        if complete:
            heading = f"Graph result (all {len(records)} rows):"
        else:
            heading = f"Graph result preview (first {len(records)} of {row_total} rows):"
        breakdown = _breakdown_sum(records) if complete else None
        data_section = (
            f"{heading}\n{preview_json}\n"
            + (f"Sum of {breakdown[0]} across all {len(records)} rows: {breakdown[1]:,}. When the question "
               "asks how many, give this total first, then the breakdown.\n" if breakdown else "")
            + ("Every row of this result is attached to the turn as a table and a downloadable "
               "file, so say the full list is available rather than offering to re-run the "
               "query or telling the user to narrow it.\n" if len(records) < len(all_rows) else "")
            + _type_histogram_block(all_rows, len(records))
            + _type_names_block(config, all_rows if len(all_rows) <= _AGGREGATE_ROWS_MAX else records)
            + f"Query status: {'success' if ok else 'failed'}"
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
        api_json = json.dumps(
            _scrub_plumbing(api_result_slim, _RESULT_PLUMBING_KEYS),
            separators=(",", ":"), default=str,
        )
        data_section = f"Matching sample records (slimmed):\n{api_json}"
        if error_context:
            error_json = json.dumps(
                _scrub_plumbing(error_context, _ERROR_PLUMBING_KEYS),
                separators=(",", ":"), default=str,
            )
            data_section += f"\n\nError context:\n{error_json}"
        mode_label = "search"
        log_label = "chatter"

    # The 2026-09-21 re-run: 34 of the 43 replies recited the shape of the search,
    # because the permission to state it was unconditional while every disclosure that
    # behaves (NOT APPLIED, TRUNCATED, substitution) fires only when it changes the
    # reading. Grant it on the same footing.
    slim_flags = api_result_slim if isinstance(api_result_slim, dict) else {}
    # `query_notes`, not `scope.notes`: the scope's notes also carry the graph agent's own
    # `explanation` on every graph turn (helpers/query_scope.py), so using them qualified
    # every turn and turn 1151 answered "There are 57,441 samples in the SRP project. This
    # count was determined by a graph query over the sample network, ..." on an image that
    # carried this fix. A qualification is something the CALLER knows and the answer needs.
    disclosure_qualifies = bool(
        scope.not_applied
        or query_notes
        or graph_truncated
        or slim_flags.get("search_text_substituted")
        or slim_flags.get("result_capped")
        or (isinstance(total_matches, int) and total_matches == 0)
    )
    # 13 of that run's 34 graph turns answered from one count row and named no identifier
    # at all (12 of the 13 named nothing), because every rule about naming them is written
    # for rows ("from the preview", "when you were given all the rows") and none can fire.
    count_only = is_graph and _is_count_only((graph_result or {}).get("data") or [])

    examples_block = ""
    if example_ids:
        examples_block = (
            "Example identifiers from the result (MENTION these verbatim in your answer):\n"
            + "\n".join(f"- {eid}" for eid in example_ids)
            + "\n\n"
        )

    user_content = (
        f"User question:\n{user_query}\n\n"
        "What the user asked for:\n"
        f"- Sample types: {resolved_sampletypes}\n"
        f"- Assays: {resolved_assays}\n"
        f"- Projects: {resolved_projects}\n"
        f"- Keywords: {keywords_str}\n\n"
        f"{render_query_scope(scope)}\n\n"
        f"{data_section}\n\n"
        "Result statistics:\n"
        f"- Total matches: {total_matches if total_matches is not None else 'unknown'}\n"
        f"- Preview rows shown: {preview_count}\n"
        + (
            f"- TRUNCATED: the query hit its LIMIT of {graph_limit}. "
            f"{'Total matches above is the TRUE total, obtained by a separate count.' if total_matches is not None else 'The true total could not be determined.'} "
            "The rows you were given are a capped sample, not the whole result.\n"
            if graph_truncated else ""
        )
        + "\n"
        f"{examples_block}"
        f"MODE: {mode_label}\n\n"
        "Instructions for this turn:\n"
        "- Lead with the count or key finding: the first sentence is the answer, not an account of how it "
        "was found.\n"
        "- Name a sample type, assay or project ONCE, by its name or its code, not both: '140 RNA samples', "
        "never '140 RNA samples (RNA Sample)'. Use the form the user used.\n"
        + (
            "- MUST mention all example identifiers listed above verbatim — they are pre-extracted for you.\n"
            if example_ids else
            "- This result is a single number: no rows, so no identifiers, no spellings and no examples. Give "
            "the number and what it counts, never write as though you had seen the records, and when naming "
            "them would answer the question better than the number does, offer that as the one next step.\n"
            if count_only else
            "- Mention 2-3 example identifiers (UIDs, names) from the preview verbatim if available.\n"
        )
        + (
            "- The query did NOT constrain on everything the user asked for. Say which "
            "constraint is missing in your FIRST sentence, and do not describe the result "
            "as though it were restricted to it.\n"
            if scope.not_applied else ""
        )
        + "- If you name a sample type, assay code or keyword, take it from 'Constrained by', never from "
        "'What the user asked for' — those are what was requested, not what was searched.\n"
        + (
            "- You may name WHAT was searched using the 'Searched' phrase above, once and after the answer, "
            "because something about this result needs qualifying (a dropped constraint, a substituted or "
            "capped search, a zero, or a note from whoever built the query). Never name an endpoint, a URL, "
            "an HTTP method, Cypher, a query operator (AND/OR) or a request field: the user cannot act on "
            "any of it. Never narrate the retry path either: no 'an initial search returned no matches', no "
            "'another search was run instead'. Qualify what the result covers, not how it was reached.\n"
            if disclosure_qualifies else
            "- Do not say how the answer was found. Nothing about this result needs qualifying, so the "
            "search is not part of the reply: no mention of a query, of what it was constrained by, or of "
            "how the number was determined.\n"
        )
        + "- Skip filler phrases like 'diverse set', 'I have truncated the list', 'feel free to refine'. "
        "Be informative and brief."
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
    # Through call_llm_text, not client.chat: the chatter is the last step of a turn
    # whose query has already run, so a provider blip here throws away a finished
    # answer. It now gets the same 503 -> provider-fallback, timeout-recycle and 429
    # backoff ladder as every structured agent, plus a ledger entry it never had.
    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    try:
        answer = call_llm_text(
            config,
            messages=messages,
            model_name=chatter_model,
            client=chatter_client,
            agent_label="chatter",
            temperature=0,
            thinking_budget=chatter_budget,
            usage_label="CHATTER",
        )
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
            f"Basic info:\n- searched: {scope.searched}\n"
            f"- your question: {parser_plan.get('intent_summary')}\n"
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
            f"Basic info:\n- searched: {scope.searched}\n"
            f"- your question: {parser_plan.get('intent_summary')}\n"
            f"- total matches: {total if total is not None else 'unknown'}"
        )
    except (LLMFatalError, LLMTimeoutError) as e:
        # Every provider in the chain refused, or the provider the call moved to after
        # a timeout timed out too (that surfaces as LLMTimeoutError, which the parser
        # needs to see as such). The query itself already succeeded, so
        # report what it found and name the real cause. Previously this exception left
        # the chatter uncaught, escaped run_query's bare `except Exception` and was
        # rewritten by ns/turn.py into "Internal pipeline error" — production turns
        # 463/464, where a finished graph result (total=0) was thrown away and the user
        # was told nothing except that something had broken.
        print("[DEBUG][CHATTER] Fatal LLM error:", repr(e))
        busy = (
            "The model that writes the reply is busy, so this answer is unformatted. "
            "The query itself ran. Ask again in a moment for the written version."
        )
        if is_reporter:
            return f"{busy}\n\nThe report step completed."
        if is_graph:
            count = (graph_result or {}).get("count", 0)
            return f"{busy}\n\nThe graph query returned {count} record(s)."
        data = (api_result_slim or {}).get("data", {})
        total = data.get("total") if isinstance(data, dict) else None
        return (
            f"{busy}\n\n"
            f"Basic info:\n- searched: {scope.searched}\n"
            f"- your question: {parser_plan.get('intent_summary')}\n"
            f"- total matches: {total if total is not None else 'unknown'}"
        )

    # ---------- Clean answer ----------
    answer_no_links = re.sub(r"https?://\S+", "", answer)
    answer_no_links = re.sub(r"\n{3,}", "\n\n", answer_no_links).strip()
    # Every sample UID the reply names links to its sample page. Before the debug
    # block is appended, so that block is never a candidate.
    answer_no_links = link_sample_uids(answer_no_links)

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
        # Same path as the single-turn chatter: retries, provider fallback and a ledger
        # entry, none of which a bare client.chat had.
        narrative = call_llm_text(
            config,
            messages=messages,
            model_name=chatter_model,
            client=chatter_client,
            agent_label="chatter",
            temperature=0.3,
            thinking_budget=chatter_budget,
            usage_label="PLAN_CHATTER",
        ) or "(no response)"
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
    return f"{link_sample_uids(narrative)}\n\n```json\n{debug_json}\n```"

