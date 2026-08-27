from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from nessie_tests.limits import GRAPH_LIMIT_SENTINELS
from nessie_tests.outage import OUTAGE_REASON, is_provider_outage


@dataclass
class GroupResult:
    id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    # True when at least one member query came back on an exhausted provider
    # chain. The group is NOT a product failure in that case, and the runner
    # records it as an exempt `error` rather than a `failed`.
    outage: bool = False


def _last_query_complete(payload: dict) -> dict:
    data = None
    for ev in payload.get("progress") or []:
        if ev.get("event") == "query_complete":
            data = ev.get("data") or {}
    return data or {}


def get_last_reply(payload: dict) -> str | None:
    """The turn's final reply text.

    A group's ``drive_fn`` used to return only ``{route, count}``, so the reply
    never reached ``run_group`` and an outage inside a group was undetectable —
    which is exactly how ``cons.nhp_sequencing_engine`` reported "count could not
    be resolved" during a Bedrock outage and was filed as drift.
    """
    reply = _last_query_complete(payload).get("reply")
    return reply if isinstance(reply, str) else None


def get_result_count(payload: dict) -> int | None:
    """Resolve a turn's result count from whichever field the engine wrote.

    ``api_result_meta.count`` was tried first and does not exist: the orchestrator
    writes ``row_count`` on the new_search path (orchestrator.py:1027) and no count at
    all on the recall path (line 594). Tasks 838/839 confirm it — ``row_count: 139``
    and no ``count`` key. So this used to fall through to
    ``api_result_full.data.total`` or return None, and a group where BOTH counts were
    None passed every assertion while evaluating nothing.
    """
    debug = _last_query_complete(payload).get("debug") or {}
    for path in (("api_result_meta", "row_count"),
                 ("api_result_meta", "count"),
                 ("api_result_full", "data", "total"),
                 ("graph_result", "total"),
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

    # PROVIDER OUTAGE FIRST, before any assertion message is composed.
    #
    # A group replaces its members' replies with its own summary, so an outage
    # inside a group is invisible downstream. In the 2026-08-03 seed-6 run BOTH
    # of cons.nhp_sequencing_engine's turns came back on an exhausted Bedrock
    # fallback chain; the group reported "count could not be resolved for 2 of 2
    # queries ...; the count assertions evaluated nothing" and a careful reviewer
    # read that and filed the case as drift. It was the tenth case of one outage.
    #
    # Returning here rather than appending a reason is deliberate: every check
    # below describes a product defect, and none of them is true of a turn that
    # never reached the product.
    outaged = [o["query"] for o in obs if is_provider_outage(o.get("reply"))]
    if outaged:
        return GroupResult(
            group["id"], False,
            [f"{OUTAGE_REASON}. Hit {len(outaged)} of {len(obs)} queries "
             f"({outaged}); the group's assertions were not evaluated"],
            obs, outage=True)

    a = group.get("assert", {})
    reasons: list[str] = []
    routes = {o.get("route") for o in obs}
    counts = [o.get("count") for o in obs]

    # A count-based assertion over unresolved counts asserts nothing. Fail loudly
    # instead of passing vacuously — that silence is what made this group's green
    # untrustworthy.
    if (a.get("same_count") or "count_not" in a or a.get("count_not_limit")):
        unresolved = [o["query"] for o in obs if o.get("count") is None]
        if unresolved:
            reasons.append(
                f"count could not be resolved for {len(unresolved)} of {len(obs)} "
                f"queries ({unresolved}); the count assertions evaluated nothing"
            )

    resolved = {c for c in counts if c is not None}
    if a.get("same_route") and len(routes) > 1:
        reasons.append(f"routes differ: {sorted(str(r) for r in routes)}")
    if a.get("same_count") and len(resolved) > 1:
        reasons.append(f"counts differ: {sorted(resolved)}")
    if "count_not" in a and a["count_not"] in resolved:
        reasons.append(f"count equals forbidden {a['count_not']} (likely a LIMIT cap)")
    if a.get("count_not_limit"):
        # Replaces a hardcoded `count_not: 250`, which went dead the moment the graph
        # limit moved to 5000. Checks every limit the corpus has ever run under.
        hit = sorted(resolved & set(GRAPH_LIMIT_SENTINELS))
        if hit:
            reasons.append(f"count sits exactly on a graph LIMIT {hit} (likely capped)")

    return GroupResult(group["id"], not reasons, reasons, obs)
