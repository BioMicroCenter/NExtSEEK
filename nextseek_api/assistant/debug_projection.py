"""Rebuild the Search Details panel for NExtSEEK-engine turns on rehydrate.

The live path fills the panel from SSE progress events (``agent_started``,
``route_decided``, ...) accumulated in the frontend and attached on
``query_complete``. Those events are ephemeral: nothing persists them in that
shape, so a page refresh left every NExtSEEK-engine turn with an empty panel
while Container-CC turns kept theirs, because CC traces *are* mirrored onto the
``chat_log`` entry and projected as ``Turn.cc_traces``.

The bundle already stores the substance of what those events summarised
(``parser_plan``, ``graph_plan``, ``graph_result``, ``api_plan``,
``api_result_slim``), so the panel is reconstructed from the bundle rather than
replayed. Reconstructing has one decisive advantage over persisting entries at
write time: it repairs every session that already exists, not only future ones.

Entries are ``{agent, summary}`` only. The frontend supplies ``timestamp`` from
the turn, since the per-event times were never recorded.
"""

from __future__ import annotations

from typing import Any

#: Filter keys worth showing; empty values are dropped so the panel stays terse.
_FILTER_ORDER = ("sampletype_code", "assay_codes", "keywords", "uids", "lab_codes")

_SEP = "  ·  "


def _as_dict(value: Any) -> dict:
    """Return ``value`` if it is a dict, else an empty dict.

    Bundles are user-visible JSON written across many releases; a field that is
    a string or an int in an older row must degrade to "no entry", never raise.
    """
    return value if isinstance(value, dict) else {}


def _fmt_filters(filters: Any) -> str:
    """Render non-empty filters as ``k=v``, lists joined with commas."""
    filters = _as_dict(filters)
    parts = []
    for key in _FILTER_ORDER:
        val = filters.get(key)
        if val in (None, "", [], {}):
            continue
        if isinstance(val, (list, tuple)):
            val = ",".join(str(v) for v in val if v not in (None, ""))
            if not val:
                continue
        parts.append(f"{key}={val}")
    return _SEP.join(parts)


def _parser_entry(bundle: dict) -> dict | None:
    plan = _as_dict(bundle.get("parser_plan"))
    if not plan:
        return None
    head = [p for p in (plan.get("mode"), plan.get("target_endpoint")) if p]
    lines = [_SEP.join(str(h) for h in head)] if head else []
    intent = plan.get("intent_summary")
    if intent:
        lines.append(str(intent))
    filters = _fmt_filters(plan.get("filters"))
    if filters:
        lines.append(filters)
    summary = "\n".join(line for line in lines if line)
    return {"agent": "parser", "summary": summary} if summary else None


def _graph_entry(bundle: dict) -> dict | None:
    plan = _as_dict(bundle.get("graph_plan"))
    cypher = plan.get("cypher")
    if not cypher:
        return None
    lines = [str(cypher)]
    params = _as_dict(plan.get("parameters"))
    if params:
        lines.append(_SEP.join(f"{k}={v}" for k, v in params.items()))
    explanation = plan.get("explanation")
    if explanation:
        lines.append(str(explanation))
    return {"agent": "graph", "summary": "\n".join(lines)}


def _graph_result_entry(bundle: dict) -> dict | None:
    result = _as_dict(bundle.get("graph_result"))
    if not result:
        return None
    count = result.get("count")
    if count is None:
        return None
    parts = [f"{count:,} rows" if isinstance(count, int) else f"{count} rows"]
    total = result.get("total")
    if isinstance(total, int):
        parts.append(f"total {total:,}")
    # Always state truncation: a silently capped result reading as complete is
    # the failure this panel exists to make visible.
    parts.append(f"truncated={bool(result.get('truncated'))}")
    limit = result.get("limit")
    if isinstance(limit, int):
        parts.append(f"limit {limit:,}")
    return {"agent": "neo4j", "summary": _SEP.join(parts)}


def _api_entry(bundle: dict) -> dict | None:
    plan = _as_dict(bundle.get("api_plan"))
    endpoint = plan.get("endpoint") or bundle.get("endpoint")
    if not endpoint or endpoint == "neo4j":
        return None
    method = plan.get("method") or "GET"
    parts = [f"{method} {endpoint}"]
    result = _as_dict(bundle.get("api_result_slim")) or _as_dict(bundle.get("api_result_full"))
    rows = result.get("row_count")
    total = result.get("total")
    if isinstance(rows, int):
        parts.append(f"{rows:,} rows")
    if isinstance(total, int) and total != rows:
        parts.append(f"total {total:,}")
    elif isinstance(total, int) and not isinstance(rows, int):
        parts.append(f"{total:,} rows")
    return {"agent": "api", "summary": _SEP.join(parts)}


def bundle_debug_entries(bundle: Any) -> list[dict[str, str]]:
    """Project one ``results_history`` bundle into Search Details entries.

    Returns ``[]`` for anything that is not a bundle carrying at least one plan,
    so wizard and Container-CC turns (which write no plans) do not grow an empty
    panel. Never raises: a malformed bundle yields no entries.
    """
    bundle = _as_dict(bundle)
    if not bundle:
        return []
    builders = (_parser_entry, _graph_entry, _graph_result_entry, _api_entry)
    entries: list[dict[str, str]] = []
    for build in builders:
        try:
            entry = build(bundle)
        except Exception:  # pragma: no cover - defensive, see module docstring
            entry = None
        if entry:
            entries.append(entry)
    return entries
