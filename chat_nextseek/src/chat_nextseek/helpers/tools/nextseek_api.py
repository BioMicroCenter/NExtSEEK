"""NExtSEEK REST API tool and retry/sanitize helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import requests

from ...config import ChatConfig
from ...session import SessionState
from ..results import DEFAULT_API_PAGE_SIZE


# --------------------------------------------------------------------------
# Write boundary: this tool is READ-ONLY.
#
# `method` arrives straight from the api_agent's plan, so a single mis-parsed turn
# could otherwise issue DELETE against a live sample record. Mutation belongs on the
# container_cc path, where `nextseek-api-write` exits WRITE_BLOCKED without an
# explicit `--confirmed-write` and the skill demands plain-text confirmation first.
# On this path there is no confirmation step, so there is no write.
#
# This mirrors the Neo4j tool, which has always been hard-blocked before the driver
# opens (`helpers/tools/neo4j.py`, `_WRITE_KEYWORDS`). The asymmetry was the bug.
#
# The rule is (method, path) PAIRS, not method alone: this API uses POST for both
# search and create, so a method-only allowlist would either block the three search
# POSTs or permit sample creation.
# --------------------------------------------------------------------------

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The only POSTs that are reads. Everything else that POSTs — notably
# `/nextseek_api/samples/`, which CREATES a sample and is one path segment away from
# `/nextseek_api/samples/advanced_search/` — is denied.
_READ_POST_PATHS = frozenset({
    "/nextseek_api/admin/samples/retrieve/",
    "/nextseek_api/sample_types/get_parents/parents_by_child_types/",
    "/nextseek_api/samples/advanced_search/",
})


def _normalize_endpoint_path(endpoint: str) -> str:
    """Canonicalise to `/a/b/` form.

    Callers are inconsistent about the leading slash (the URL build does
    `endpoint.lstrip('/')`) and about the trailing one, so normalise both rather than
    let a stray character decide an allow.
    """
    path = str(endpoint or "").split("?", 1)[0].strip().strip("/")
    return f"/{path}/" if path else "/"


def _canonical_method(method: str | None) -> str:
    """Canonicalise the verb: strip surrounding whitespace and control characters,
    upper-case. This is the form both the decision AND the outgoing request use."""
    return str(method or "").strip().upper()


def _is_read_only_pair(verb: str, path: str) -> bool:
    """The policy itself, over ALREADY-CANONICAL inputs.

    DEFAULT-DENY. Reads are allowed unconditionally; POST is allowed only for the
    three known search endpoints; every other verb — PATCH, PUT, DELETE, or anything
    unrecognised — is denied whatever the path. That last clause is the property that
    survives the catalog changing: a mutating endpoint added later is denied because
    nothing allowed it, not permitted because nothing forbade it.
    """
    if verb in _READ_METHODS:
        return True
    if verb == "POST":
        return path in _READ_POST_PATHS
    return False


def _is_read_only_request(endpoint: str, method: str | None) -> bool:
    """True when this (method, path) pair may leave the process. Canonicalises first."""
    return _is_read_only_pair(_canonical_method(method), _normalize_endpoint_path(endpoint))


def tool_nextseek_api_request(config: ChatConfig, endpoint, method, requestBody=None, queryParameters=None):
    """
    Send an HTTP request to the NExtSEEK API with optional basic auth and schema validation.
    Logs request/response previews, parses JSON when possible, and returns a structured dict with ok/status details.

    Read-only: mutating (method, path) pairs are refused here, before any network work.
    """
    # Canonicalise ONCE, then decide, validate, build the URL and dispatch on the same
    # two strings. Approving one form and sending another is the classic parser
    # differential; `verb` and `path` below are what was approved and what goes out.
    verb = _canonical_method(method)
    path = _normalize_endpoint_path(endpoint)

    # First decision in the function on purpose — the refusal must not depend on
    # config state, request-body validation, or anything else that could be absent.
    if not _is_read_only_pair(verb, path):
        msg = (
            f"Write operations are not permitted on the NExtSEEK REST path: "
            f"{method} {endpoint} was blocked. This tool is read-only; sample "
            f"creation, modification and deletion must go through the confirmed-write "
            f"path, not the assistant's REST corridor."
        )
        print(f"[DEBUG][API] Blocked write request: {method} {endpoint!r}")
        return {
            "ok": False,
            "error": msg,
            "endpoint": endpoint,
            "method": method,
        }

    requestBody = requestBody or {}
    queryParameters = {"page_size": DEFAULT_API_PAGE_SIZE, **(queryParameters or {})}

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

    is_valid, error_payload = config.validate_request_body(path, requestBody, verb)
    if not is_valid:
        return error_payload

    url = f"{base}/{path.lstrip('/')}"
    auth = (config.API_USER, config.API_PASS) if config.API_USER and config.API_PASS else None
    request_timeout = 90
    if path == "/nextseek_api/samples/advanced_search/":
        request_timeout = 120

    print("[DEBUG][API] Request:")
    print(f"  METHOD: {verb}")
    print(f"  URL:    {url}")
    print(f"  PARAMS: {queryParameters}")
    print(f"  BODY:   {requestBody}")
    print(f"  AUTH:   {'Basic' if auth else 'None'}")
    print(f"  TIMEOUT:{request_timeout}s")

    try:
        resp = requests.request(
            method=verb,
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
            # `verb`/`url` so the record matches the request that was actually sent.
            "method": verb,
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


def _retry_terms(plan: dict, api_plan: dict) -> list[str]:
    """Terms to try in isolation when advanced_search returns empty: the tokens of
    the search that was actually SENT, plus filters.keywords and filters.lab_codes,
    deduped in order. Sourcing from the sent filter_searchText (and lab_codes) — not
    filters.keywords alone — lets a lab-scoped search whose 3-letter code the api_agent
    fused with other terms (e.g. "KAM MetNet") fall back to the code alone ("KAM")."""
    filters = plan.get("filters") or {}
    sent = ((api_plan.get("requestBody") or {}).get("filter_searchText") or "")
    candidates = list(_split_retry_keyword(sent)) if sent else []
    candidates += [k for k in (filters.get("keywords") or []) if isinstance(k, str)]
    candidates += [c for c in (filters.get("lab_codes") or []) if isinstance(c, str)]
    out: list[str] = []
    for term in candidates:
        term = term.strip()
        if term and term not in out:
            out.append(term)
    return out


def _should_retry_advanced_search(plan: dict, api_plan: dict, api_result_full: dict) -> bool:
    """
    Determine whether an advanced_search POST should be retried after an empty or failed result.
    Verifies endpoint/method, requires multiple fallback terms (from the sent search +
    keywords + lab_codes), and checks for zero results or API errors.
    """
    if api_plan.get("endpoint") != "/nextseek_api/samples/advanced_search/":
        return False

    terms = _retry_terms(plan, api_plan)
    if len(terms) < 2 and not _has_expandable_keyword(terms):
        # Still retry on errors (timeout etc.) when there is anything to re-send.
        if isinstance(api_result_full, dict) and api_result_full.get("ok") is False:
            return bool(terms)
        return False

    total, row_count = _extract_total_and_rows(api_result_full)
    # Retry on empty results or API errors (timeout, connection issues)
    if isinstance(api_result_full, dict) and api_result_full.get("ok") is False:
        return True
    return (total == 0) or (row_count == 0)


# A SINGLE-term retry that matches more than this is not answering the question that
# was asked; it is the ladder falling off the bottom. Task 797's ladder "succeeded"
# on the term "1" with 2,057 rows for a two-UID question.
RETRY_SINGLE_TOTAL_CEILING = 200

# Tokens that carry no search meaning on their own. A UID like NHP-220524FLY-1-PUB
# splits into ['NHP','220524FLY','1','PUB']; the increment and the publication suffix
# match essentially everything.
_USELESS_RETRY_TOKEN_RE = re.compile(r"^(?:\d+|PUB\d*)$", re.IGNORECASE)


def _is_useful_retry_token(term: str) -> bool:
    """A retry term must be at least 3 characters and not a bare increment or PUB suffix."""
    return len(term) >= 3 and not _USELESS_RETRY_TOKEN_RE.match(term)


def _split_retry_keyword(keyword: str) -> list[str]:
    """Split compact search phrases into useful retry terms while preserving the original elsewhere.

    Tokens shorter than 3 characters, purely numeric tokens and PUB suffixes are
    dropped: they are UID structure, not search terms, and searching them alone
    returns an arbitrary slice of the database.
    """
    terms = [part for part in re.split(r"[\s_\-/]+", keyword.strip()) if part]
    return list(dict.fromkeys(t for t in terms if _is_useful_retry_token(t)))


def _original_search_was_unfiltered(api_plan: dict) -> bool:
    """True when the original request carried no filter at all (e.g. "how many samples
    are there"), in which case a large total is the honest answer and the SINGLE
    ceiling must not apply."""
    body = api_plan.get("requestBody") or {}
    if (body.get("filter_searchText") or "").strip():
        return False
    return not any(
        key.startswith("filter_") and value not in (None, "", [], {})
        for key, value in body.items()
    )


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

    terms = _retry_terms(plan, api_plan)
    base_body = dict(api_plan.get("requestBody") or {})
    original_search = (base_body.get("filter_searchText") or "").strip()
    unfiltered = _original_search_was_unfiltered(api_plan)

    for label, search_text in _advanced_search_retry_attempts(terms):
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
            # A single leftover token that matches a large slice of the database is
            # the ladder falling off the bottom, not an answer. Task 797 "succeeded"
            # on the term "1" with 2,057 rows for a two-UID question.
            if label == "SINGLE" and not unfiltered and (total or 0) > RETRY_SINGLE_TOTAL_CEILING:
                print(
                    f"[DEBUG][API][RETRY] Rejecting {label}: filter_searchText={search_text!r} "
                    f"total={total} exceeds ceiling {RETRY_SINGLE_TOTAL_CEILING}; "
                    "a single leftover term is not an answer to a filtered question"
                )
                continue

            print(f"[DEBUG][API][RETRY] Success with {label}: filter_searchText={search_text!r} total={total} rows={row_count}")
            if search_text != original_search:
                # Recorded so the reply can disclose that these rows are not the
                # user's terms. Undisclosed substitution is the actual defect.
                retry_api_plan["retry_substituted_search"] = {
                    "original": original_search,
                    "used": search_text,
                    "label": label,
                }
            return retry_api_plan, retry_result

        print(f"[DEBUG][API][RETRY] No results with {label}: filter_searchText={search_text!r} total={total} rows={row_count}")

    # All retries failed -> keep original (so logs match original attempt)
    return api_plan, api_result_full
