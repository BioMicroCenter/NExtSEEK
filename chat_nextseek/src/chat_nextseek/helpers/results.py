"""API result shaping/normalization helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
from pathlib import Path

from .text import strip_html


def slim_api_result_for_llm(api_result: dict, max_rows: int = 5, max_chars: int = 5000) -> dict:
    """
    Trim API results to keep LLM prompts small while preserving key totals and a few example rows.
    Caps row count and total serialized size, substituting a preview with truncation metadata when oversized.
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
