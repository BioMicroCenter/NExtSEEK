from __future__ import annotations

THIN_KEYS = {"id", "uuid", "sample_type", "sample_type_description"}


def _samples(bundle: dict) -> list[dict]:
    for path in (("memory_payload", "data", "samples"), ("api_result_full", "data", "samples")):
        node = bundle
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, list):
            return node
    gr = (bundle.get("graph_result") or {}).get("data")
    return gr if isinstance(gr, list) else []


def richness_summary(bundle: dict) -> dict:
    samples = _samples(bundle)
    extra = sorted({k for s in samples if isinstance(s, dict) for k in s} - THIN_KEYS)
    return {
        "row_count": len(samples),
        "has_json_metadata": any(bool(s.get("json_metadata")) for s in samples if isinstance(s, dict)),
        "sample_extra_keys": extra,
        "has_extra_keys": bool(extra),
        "memory_payload_null": bundle.get("memory_payload") is None,
    }


def read_results_history(session_id) -> list[dict]:
    from nextseek_api.assistant.models_db import ChatSession  # lazy: Django only at call time
    return ChatSession.objects.get(session_id=session_id).results_history or []


def summary_for_session(session_id) -> dict | None:
    hist = read_results_history(session_id)
    return richness_summary(hist[-1]) if hist else None
