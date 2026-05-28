"""NExtSEEK REST API tool and retry/sanitize helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import requests

from ...config import ChatConfig
from ...session import SessionState


def tool_nextseek_api_request(config: ChatConfig, endpoint, method, requestBody=None, queryParameters=None):
    """
    Send an HTTP request to the NExtSEEK API with optional basic auth and schema validation.
    Logs request/response previews, parses JSON when possible, and returns a structured dict with ok/status details.
    """
    requestBody = requestBody or {}
    queryParameters = {"page_size": 1000, **(queryParameters or {})}

    base = config.NEXTSEEK_BASE_URL
    if not base:
        msg = "NEXTSEEK_BASE_URL is not set."
        print(f"[DEBUG][API] {msg}")
        return {
            "ok": False,
            "error": msg,
            "endpoint": endpoint,
            "method": method,
        }

    is_valid, error_payload = config.validate_request_body(endpoint, requestBody, method)
    if not is_valid:
        return error_payload

    url = f"{base}/{endpoint.lstrip('/')}"
    auth = (config.API_USER, config.API_PASS) if config.API_USER and config.API_PASS else None
    request_timeout = 90
    if endpoint == "/nextseek_api/samples/advanced_search/":
        request_timeout = 120

    print("[DEBUG][API] Request:")
    print(f"  METHOD: {method}")
    print(f"  URL:    {url}")
    print(f"  PARAMS: {queryParameters}")
    print(f"  BODY:   {requestBody}")
    print(f"  AUTH:   {'Basic' if auth else 'None'}")
    print(f"  TIMEOUT:{request_timeout}s")

    try:
        resp = requests.request(
            method=method,
            url=url,
            auth=auth,
            params=queryParameters,
            json=requestBody if requestBody else None,
            timeout=request_timeout,
        )

        print("[DEBUG][API] Response:")
        print(f"  STATUS: {resp.status_code}")
        preview = resp.text[:300].replace("\n", " ")
        print(f"  PREVIEW: {preview!r}")

        try:
            data = resp.json()
        except Exception:
            data = {"_raw": resp.text[:1000]}

        return {
            "ok": resp.ok,
            "url": url,
            "status_code": resp.status_code,
            "method": method,
            "query": queryParameters,
            "body": requestBody,
            "data": _sanitize_api_row_strings(data),
        }

    except Exception as e:
        print(f"[DEBUG][API] Exception: {repr(e)}")
        return {
            "ok": False,
            "error": repr(e),
            "endpoint": endpoint,
            "method": method,
        }


_API_HTML_PATTERN = re.compile(r"<[^>]+>")
_API_UID_LIKE_FIELDS = {"uid", "uuid", "title", "idlink", "idurl"}


def _sanitize_api_row_strings(payload: Any) -> Any:
    """In-place-ish: walk an API response and strip HTML wrappers from UID-bearing
    string fields. The NExtSEEK API wraps UIDs as `<a href=...>UID</a>` for the
    legacy web UI; raw text breaks downstream string filtering (memory_coder,
    samplesheet emission, chat_log previews).

    Only sanitizes whitelisted fields to avoid mangling legitimate HTML in other
    free-text fields. Returns the payload (mutated when dict/list).
    """
    if isinstance(payload, dict):
        for k, v in list(payload.items()):
            if isinstance(k, str) and k in _API_UID_LIKE_FIELDS and isinstance(v, str):
                if "<" in v and ">" in v:
                    payload[k] = _API_HTML_PATTERN.sub("", v).strip()
            elif isinstance(v, (dict, list)):
                _sanitize_api_row_strings(v)
    elif isinstance(payload, list):
        for item in payload:
            _sanitize_api_row_strings(item)
    return payload


def log_api_call(
    session,
    user_query: str,
    parser_plan: dict,
    api_plan: dict,
    api_result_full: dict,
    bundle_id: int,
):
    """
    Append a JSONL record of an API call to the session-scoped api_log_path.
    Captures query text, plans, and normalized results so console logs remain slim.
    """
    record = {
        "timestamp": datetime.now().isoformat(),
        "bundle_id": bundle_id,
        "user_query": user_query,
        "parser_plan": parser_plan,
        "api_plan": api_plan,
        "api_result_meta": {
            "ok": api_result_full.get("ok"),
            "status_code": api_result_full.get("status_code"),
            "url": api_result_full.get("url"),
        },
        "api_result_data": api_result_full.get("data"),
    }
    log_path = session.get("api_log_path") if hasattr(session, "get") else None
    if not log_path:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print("[DEBUG][API_LOG] Failed to write API log:", repr(e))


def fix_sample_endpoint(plan: dict) -> dict:
    """
    Auto-correct admin retrieve endpoint selections when no UIDs are provided.
    Rewrites to advanced_search and annotates notes to avoid invalid admin calls while keeping other fields intact.
    """
    endpoint = plan.get("target_endpoint")
    mode = plan.get("mode")
    filters = plan.get("filters", {})
    uids = filters.get("uids") or []

    if (
        mode in ("new_search", "refine_last_search")
        and endpoint == "/nextseek_api/admin/samples/retrieve/"
        and not uids
    ):
        print("[DEBUG][PARSER_FIX] Rewriting endpoint from admin retrieve to samples/advanced_search")
        plan["target_endpoint"] = "/nextseek_api/samples/advanced_search/"
        notes = plan.get("notes", "")
        plan["notes"] = (notes + " | endpoint auto-corrected to /samples/advanced_search/").strip(" |")

    return plan


def build_recent_results_summary(session: SessionState, max_results: int = 8) -> str:
    """
    Build a short summary of recent result bundles for prompt conditioning.
    Includes bundle IDs, user queries, endpoints, and totals to guide refinement or follow-up questions.

    Now defaults to 8 bundles (up from 3) — long sessions hit a recall cliff if older
    bundles fall out of view. Parser can then pick `target_result_id` for any bundle
    in the visible window when the user uses "first", "originally", "earlier", etc.
    """
    history = session.get("results_history", [])
    if not history:
        return "No prior results in this session."

    visible = history[-max_results:]
    lines = [
        f"Recent results (most recent first; {len(visible)} of {len(history)} bundles shown):"
    ]
    for bundle in reversed(visible):
        total = None
        data = bundle.get("api_result_slim", {})
        if isinstance(data, dict):
            total = data.get("total")
            if total is None and isinstance(data.get("data"), dict):
                total = (
                    data["data"].get("total")
                    or data["data"].get("total_samples")
                    or data["data"].get("total_nodes")
                )
        lines.append(
            f"- id={bundle.get('id')}, "
            f"mode={bundle.get('mode')}, "
            f"query={bundle.get('user_query')!r}, "
            f"endpoint={bundle.get('endpoint')}, "
            f"total={total}"
        )
    if len(history) > max_results:
        lines.append(
            f"(NOTE: {len(history) - max_results} older bundle(s) exist but are not shown. "
            "If the user references something not visible here, infer from CHAT_HISTORY narrative "
            "and set target_result_id explicitly.)"
        )
    return "\n".join(lines)


def _extract_total_and_rows(api_result_full: dict) -> tuple[int | None, int]:
    """
    Extract (total, row_count) from a NExtSEEK response, handling both wrapped and raw result shapes.
    Falls back to (None, 0) when structure is unexpected so retry heuristics remain safe.
    """
    data = api_result_full.get("data")
    if isinstance(data, dict):
        total = (
            data.get("total")
            or data.get("total_samples")
            or data.get("total_nodes")
        )
        rows = (
            data.get("rows")
            if isinstance(data.get("rows"), list)
            else data.get("nodes")
            if isinstance(data.get("nodes"), list)
            else data.get("data")
            if isinstance(data.get("data"), list)
            else None
        )
        if isinstance(rows, list):
            return total, len(rows)
        return total, 0

    # If tool returns raw dict already shaped like {"total":..., "rows":[...]}
    if isinstance(api_result_full, dict):
        total = (
            api_result_full.get("total")
            or api_result_full.get("total_samples")
            or api_result_full.get("total_nodes")
        )
        rows = (
            api_result_full.get("rows")
            if isinstance(api_result_full.get("rows"), list)
            else api_result_full.get("nodes")
            if isinstance(api_result_full.get("nodes"), list)
            else api_result_full.get("data")
            if isinstance(api_result_full.get("data"), list)
            else None
        )
        if isinstance(rows, list):
            return total, len(rows)

    return None, 0


def _should_retry_advanced_search(plan: dict, api_plan: dict, api_result_full: dict) -> bool:
    """
    Determine whether an advanced_search POST should be retried after an empty or failed result.
    Verifies endpoint/method, requires keywords, and checks for zero results or API errors.
    """
    if api_plan.get("endpoint") != "/nextseek_api/samples/advanced_search/":
        return False

    keywords = (plan.get("filters") or {}).get("keywords") or []
    if not isinstance(keywords, list) or len(keywords) < 2:
        # Still retry on errors (timeout etc.) even with fewer keywords
        if isinstance(api_result_full, dict) and api_result_full.get("ok") is False:
            return bool(keywords)
        if not keywords or not _has_expandable_keyword(keywords):
            return False

    total, row_count = _extract_total_and_rows(api_result_full)
    # Retry on empty results or API errors (timeout, connection issues)
    if isinstance(api_result_full, dict) and api_result_full.get("ok") is False:
        return True
    return (total == 0) or (row_count == 0)


def _split_retry_keyword(keyword: str) -> list[str]:
    """Split compact search phrases into useful retry terms while preserving the original elsewhere."""
    terms = [part for part in re.split(r"[\s_\-/]+", keyword.strip()) if part]
    return list(dict.fromkeys(terms))


def _has_expandable_keyword(keywords: list[str]) -> bool:
    return any(len(_split_retry_keyword(k)) > 1 for k in keywords if isinstance(k, str))


def _advanced_search_retry_attempts(keywords: list[str]) -> list[tuple[str, str]]:
    """
    Generate labeled keyword variants for retrying advanced_search.
    Produces OR-joined and single-keyword attempts so the API gets multiple matching chances.
    Also handles single-keyword retries (e.g. after a timeout on the first attempt).
    """
    kws = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    if not kws:
        return []

    attempts: list[tuple[str, str]] = []
    if len(kws) >= 2:
        attempts.append(("OR", " OR ".join(kws)))
    elif len(kws) == 1:
        split_terms = _split_retry_keyword(kws[0])
        if len(split_terms) >= 2:
            attempts.append(("OR", " OR ".join(split_terms)))
            for term in split_terms:
                attempts.append(("SINGLE", term))
    for k in kws:
        attempts.append(("SINGLE", k))
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, text in attempts:
        if text in seen:
            continue
        seen.add(text)
        deduped.append((label, text))
    return deduped


def _retry_advanced_search_if_empty(config: ChatConfig, plan: dict, api_plan: dict, api_result_full: dict) -> tuple[dict, dict]:
    """
    If advanced_search returns empty and multiple keywords exist, retry with OR then SINGLE.
    Returns (final_api_plan, final_api_result_full). If no retry needed, returns originals.
    """
    if not _should_retry_advanced_search(plan, api_plan, api_result_full):
        return api_plan, api_result_full

    keywords = (plan.get("filters") or {}).get("keywords") or []
    base_body = dict(api_plan.get("requestBody") or {})

    for label, search_text in _advanced_search_retry_attempts(keywords):
        retry_body = dict(base_body)
        retry_body["filter_searchText"] = search_text

        retry_api_plan = dict(api_plan)
        retry_api_plan["requestBody"] = retry_body
        retry_api_plan["notes"] = (retry_api_plan.get("notes") or "") + f" [retry={label}]"

        retry_result = tool_nextseek_api_request(
            config,
            endpoint=retry_api_plan["endpoint"],
            method=retry_api_plan["method"],
            requestBody=retry_api_plan.get("requestBody") or {},
            queryParameters=retry_api_plan.get("queryParameters") or {},
        )

        total, row_count = _extract_total_and_rows(retry_result)
        if (total and total > 0) or (row_count and row_count > 0):
            print(f"[DEBUG][API][RETRY] Success with {label}: filter_searchText={search_text!r} total={total} rows={row_count}")
            return retry_api_plan, retry_result

        print(f"[DEBUG][API][RETRY] No results with {label}: filter_searchText={search_text!r} total={total} rows={row_count}")

    # All retries failed -> keep original (so logs match original attempt)
    return api_plan, api_result_full
