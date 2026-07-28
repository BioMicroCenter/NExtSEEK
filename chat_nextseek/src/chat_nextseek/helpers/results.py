"""API result shaping/normalization helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .text import strip_html

# tool_nextseek_api_request hard-defaults this. A search matching more rows than this
# silently returns one page, so it must be disclosed rather than left implicit.
DEFAULT_API_PAGE_SIZE = 1000


def api_row_count(api_result: dict | None) -> int | None:
    """How many rows an API result actually returned, or None if not countable.

    ``api_result_meta`` used to carry only ok/status_code/url, so nothing
    downstream — the debug panel, the chat log, or the test harness — could tell
    a search that returned 195 rows from one that returned 0. Both looked like
    ``ok: true``. Counting here makes the OUTCOME of a search observable rather
    than just its plumbing.
    """
    if not isinstance(api_result, dict):
        return None
    data = api_result.get("data")
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return None
    # advanced_search/new_search pin rows under 'samples'; graph under 'nodes'.
    for key in ("samples", "rows", "nodes", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    # A totals-only payload (e.g. a count query) still reports a number.
    for key in ("total", "total_samples", "total_nodes", "count"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


def _result_total(api_result: dict | None) -> int | None:
    """The API's reported grand total, independent of how many rows came back."""
    if not isinstance(api_result, dict):
        return None
    data = api_result.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("total", "total_samples", "total_nodes"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


def _rows_actually_returned(api_result: dict | None) -> int | None:
    """Length of the returned row list, or None when the payload carries no list."""
    if not isinstance(api_result, dict):
        return None
    data = api_result.get("data")
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return None
    for key in ("samples", "rows", "nodes", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def build_result_disclosure(
    api_result: dict | None,
    api_plan: dict | None = None,
    preview_rows: int | None = None,
) -> dict:
    """
    Flags describing how the rows the chatter sees differ from what the user asked for.

    Three silences this removes.

    1. ``tool_nextseek_api_request`` hard-defaults ``page_size: 1000``, so a search
       matching 2,057 records returns 1,000 rows and reports total 2,057. The chatter
       faithfully said "2,057 records" while row_count was 1000, and nothing said the
       rows were a page.
    2. The LLM-context slim keeps 5 rows. ``/nextseek_api/assays/`` is a SEEK JSON:API
       passthrough whose body carries no total at all, so after slimming the chatter
       held 5 rows and no other number — and answered "You have access to 5 assays"
       for a 324-row result (task 833). Reporting the preview length as the answer was
       the only thing it could do with what it was given.
    3. When the retry ladder substitutes a different search text, the rows are not the
       user's terms at all.

    None of these is a hallucination — the defect is that nothing told the chatter.
    """
    disclosure: dict = {}

    total = _result_total(api_result)
    rows = _rows_actually_returned(api_result)
    if rows is not None:
        disclosure["rows_returned"] = rows
    if isinstance(api_plan, dict):
        page_size = (api_plan.get("queryParameters") or {}).get("page_size")
        if page_size is None and rows is not None:
            page_size = DEFAULT_API_PAGE_SIZE
        if page_size is not None:
            disclosure["page_size"] = page_size

    # Capped by the API's page size: more matched than came back.
    if total is not None and rows is not None and total > rows:
        disclosure["result_capped"] = True
        disclosure["total_matching"] = total

    # Capped by the LLM-context slim: more came back than the chatter can see.
    if preview_rows is not None and rows is not None and preview_rows < rows:
        disclosure["preview_rows"] = preview_rows
        disclosure["result_capped"] = True
        disclosure.setdefault("total_matching", total if total is not None else rows)

    if isinstance(api_plan, dict) and api_plan.get("retry_substituted_search"):
        disclosure["search_text_substituted"] = api_plan["retry_substituted_search"]

    return disclosure


def slim_api_result_for_llm(
    api_result: dict,
    max_rows: int = 5,
    max_chars: int = 5000,
    api_plan: dict | None = None,
) -> dict:
    """
    Trim API results to keep LLM prompts small while preserving key totals and a few example rows.
    Caps row count and total serialized size, substituting a preview with truncation metadata when oversized.

    ``api_plan`` is optional and only used to disclose capping and search-text
    substitution; omitting it preserves the previous behaviour exactly.
    """
    data = api_result.get("data", {})
    new_data = data
    preview_items = None
    preview_key = None

    if isinstance(data, dict):
        if isinstance(data.get("rows"), list):
            preview_key = "rows"
        elif isinstance(data.get("nodes"), list):
            preview_key = "nodes"
        elif isinstance(data.get("data"), list):
            preview_key = "data"

        if preview_key:
            preview_items = data[preview_key][:max_rows]
            new_data = {**data, preview_key: preview_items}

    slimmed = {**api_result, "data": new_data}

    text = json.dumps(slimmed)
    if len(text) > max_chars:
        total = None
        total_key = "total"
        if isinstance(new_data, dict):
            for key in ("total", "total_samples", "total_nodes"):
                if new_data.get(key) is not None:
                    total = new_data.get(key)
                    total_key = key
                    break
        # Keep a small preview even when truncating for size
        preview_items = preview_items if preview_items is not None else []
        preview_label = f"{preview_key}_preview" if preview_key else "items_preview"
        slimmed = {
            **{k: v for k, v in api_result.items() if k != "data"},
            "data": {
                total_key: total,
                preview_label: preview_items,
                f"{preview_key or 'items'}_truncated": True,
                "note": f"Result truncated for LLM context (>{max_chars} chars).",
            },
        }

    # Row counts come from the ORIGINAL result; `preview_rows` is what actually
    # survived into the payload the chatter reads. Passing both is what lets the
    # chatter say "324, showing 5" instead of "5".
    slimmed.update(
        build_result_disclosure(api_result, api_plan, preview_rows=_rows_actually_returned(slimmed))
    )
    return slimmed


def collect_bundle_files(bundle: dict) -> list[tuple[str, str]]:
    """Return `(label, path)` pairs for saved bundle artifacts that still exist on disk."""
    paths: list[tuple[str, str]] = []

    manifest_files = bundle.get("files") or []
    for entry in manifest_files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        label = entry.get("label") or entry.get("key") or "artifact"
        if isinstance(path, str) and path and Path(path).exists():
            paths.append((str(label), path))

    if paths:
        return paths

    raw = bundle.get("raw_result_path")
    if raw and Path(raw).exists():
        paths.append(("API result (full JSON)", raw))

    graph = bundle.get("graph_debug_path")
    if graph and Path(graph).exists():
        paths.append(("Graph query debug JSON", graph))

    plan_debug = bundle.get("plan_debug_path")
    if plan_debug and Path(plan_debug).exists():
        paths.append(("Plan debug JSON", plan_debug))

    for step_id, raw_path in (bundle.get("raw_result_paths") or {}).items():
        if raw_path and Path(raw_path).exists():
            paths.append((f"API result (step {step_id})", raw_path))

    for step_id, graph_path in (bundle.get("graph_debug_paths") or {}).items():
        if graph_path and Path(graph_path).exists():
            paths.append((f"Graph debug (step {step_id})", graph_path))

    saved = bundle.get("report_saved_files") or {}
    for key, path in saved.items():
        if isinstance(path, str) and path and Path(path).exists():
            paths.append((key, path))
        elif isinstance(path, list):
            for i, p in enumerate(path):
                if isinstance(p, str) and p and Path(p).exists():
                    paths.append((f"{key}[{i}]", p))

    return paths


def normalize_api_result_for_memory(api_result: dict, min_rows_for_norm: int = 1) -> dict:
    """
    Normalize raw API rows into a compact structure the memory agent can consume reliably.
    Extracts uid/sample_type/assays/json_metadata, falling back to raw data when structure is unexpected or too sparse.
    """
    data = api_result.get("data")
    if not isinstance(data, dict):
        print("[DEBUG][MEMORY_NORM] data is not a dict; falling back to raw data.")
        return {"fallback": True, "raw_data": data}

    rows = data.get("rows")
    if not isinstance(rows, list):
        print("[DEBUG][MEMORY_NORM] No 'rows' list in data; falling back to raw data for this endpoint.")
        return {"fallback": True, "raw_data": data}

    normalized_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue

        uid = r.get("uuid")
        if not uid:
            uid = strip_html(r.get("uid"))

        sample_type = r.get("sample_type") or r.get("sampletype")
        assays = r.get("assays")
        source_id = r.get("id")

        meta = r.get("json_metadata")
        if not isinstance(meta, dict):
            meta = {}

        normalized_rows.append(
            {
                "uid": uid,
                "sample_type": sample_type,
                "assays": assays,
                "source_id": source_id,
                "metadata": meta,
            }
        )

    if len(normalized_rows) < min_rows_for_norm and len(rows) > 0:
        print(
            f"[DEBUG][MEMORY_NORM] Normalization produced only {len(normalized_rows)} rows "
            f"from {len(rows)} raw rows; falling back to raw data."
        )
        return {"fallback": True, "raw_data": data}

    print(
        f"[DEBUG][MEMORY_NORM] Normalized {len(normalized_rows)} rows "
        f"from {len(rows)} raw rows for memory agent."
    )

    return {
        "total": data.get("total"),
        "rows": normalized_rows,
        "note": "Normalized for memory agent; metadata flattened from json_metadata; HTML removed.",
    }


def summarize_pinned_bundle(session) -> str:
    """One-line summary of the user's most recent results bundle. '' when none.

    Moved from pipeline/agent.py — generic results-history knowledge.
    """
    history = session.get("results_history") or []
    if not isinstance(history, list) or not history:
        return ""
    last = history[-1]
    if not isinstance(last, dict):
        return ""
    user_q = last.get("user_query") or ""
    data = (last.get("api_result_full") or {}).get("data") or {}
    # advanced_search/new_search pin rows under 'samples'; graph under 'nodes'; others 'rows'.
    rows = data.get("rows") or data.get("samples") or data.get("nodes") or []
    return f"last search: query={user_q!r}, ~{len(rows) if isinstance(rows, list) else 0} rows"


def uids_from_last_search(session) -> list[str]:
    """Extract sample UIDs from the most recent results bundle that has any.

    Walks results_history in reverse; reads api_result_full.data rows
    (keys rows/nodes/data), strips HTML, falls back to reporter_plan.uids
    then parser_plan.filters.uids. Moved from pipeline/agent.py.
    """
    history = session.get("results_history") or []
    if not isinstance(history, list):
        return []
    for bundle in reversed(history):
        if not isinstance(bundle, dict):
            continue
        api_full = bundle.get("api_result_full") or {}
        data = api_full.get("data") if isinstance(api_full, dict) else None
        rows = []
        if isinstance(data, dict):
            # advanced_search/new_search pin rows under 'samples'; graph under 'nodes'.
            for key in ("rows", "nodes", "data", "samples"):
                val = data.get(key)
                if isinstance(val, list):
                    rows = val
                    break
        elif isinstance(data, list):
            rows = data
        out: list[str] = []
        for row in rows or []:
            if isinstance(row, dict):
                uid = row.get("uid") or row.get("UID") or row.get("uuid") or row.get("sample_uid")
                if uid:
                    out.append(re.sub(r"<[^>]+>", "", str(uid)).strip())
            elif isinstance(row, str):
                out.append(re.sub(r"<[^>]+>", "", row).strip())
        if not out:
            rp = bundle.get("reporter_plan")
            plan_uids = rp.get("uids") if isinstance(rp, dict) else None
            if not (isinstance(plan_uids, list) and plan_uids):
                pp = bundle.get("parser_plan")
                filt = pp.get("filters") if isinstance(pp, dict) else None
                plan_uids = filt.get("uids") if isinstance(filt, dict) else None
            for uid in plan_uids or []:
                if isinstance(uid, str) and uid.strip():
                    out.append(re.sub(r"<[^>]+>", "", uid).strip())
        if out:
            return out
    return []
