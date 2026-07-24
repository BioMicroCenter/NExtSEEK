from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class GroupResult:
    id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)


def get_result_count(payload: dict) -> int | None:
    data = None
    for ev in payload.get("progress") or []:
        if ev.get("event") == "query_complete":
            data = ev.get("data") or {}
    debug = (data or {}).get("debug") or {}
    # NOTE: the executor confirms the exact count key against a live NS debug
    # (outputs/*/console.txt); these fallbacks cover the observed shapes.
    for path in (("api_result_meta", "count"), ("api_result_full", "data", "total"),
                 ("graph_result", "count")):
        node = debug
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, int):
            return node
    gr = (debug.get("graph_result") or {}).get("data")
    return len(gr) if isinstance(gr, list) else None


def run_group(group: dict, drive_fn: Callable[[str], dict]) -> GroupResult:
    obs = [{"query": q, **drive_fn(q)} for q in group["queries"]]
    a = group.get("assert", {})
    reasons: list[str] = []
    routes = {o.get("route") for o in obs}
    counts = {o.get("count") for o in obs}
    if a.get("same_route") and len(routes) > 1:
        reasons.append(f"routes differ: {sorted(str(r) for r in routes)}")
    if a.get("same_count") and len({c for c in counts if c is not None}) > 1:
        reasons.append(f"counts differ: {sorted(c for c in counts if c is not None)}")
    if "count_not" in a and a["count_not"] in counts:
        reasons.append(f"count equals forbidden {a['count_not']} (likely a LIMIT cap)")
    return GroupResult(group["id"], not reasons, reasons, obs)
