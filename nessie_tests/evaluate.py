from __future__ import annotations

import csv
from pathlib import Path, PurePosixPath
from typing import Any

from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.criteria import check_pass, resolve_field  # noqa: E402
from nessie_tests import route_observer as ro

# Criteria in the base catalog address produced files as `api_artifact.<name>`.
# e2e resolves those against a `run_root` on disk, but nessie drives the async
# HTTP endpoint, which reuses ONE run root per gunicorn process rather than
# creating one per turn — so there is no per-turn root to hand it, and every
# such criterion resolved to None and could never pass. We resolve them from the
# turn's own emitted artifact paths instead, which is both correct per-turn and
# independent of where the server happens to write.
ARTIFACT_PREFIX = "api_artifact."
ARTIFACT_INDEX_KEY = "nessie_artifact_index"

# Row counts that land EXACTLY on a cypher LIMIT are almost certainly truncated.
# A capped result is indistinguishable from a complete one to every other
# criterion — `graph.what_mice_are_in_the_impact_st` passed every assertion in
# the 2026-07-24 run while returning exactly 250 rows. Both the historical cap
# and the current one are listed so an old deployment is still caught.
# Re-exported for back-compat; the definition moved to nessie_tests.limits so
# consistency.py can use it without importing e2e.criteria and openpyxl.
from nessie_tests.limits import GRAPH_LIMIT_SENTINELS  # noqa: E402,F401


def _last(payload, name):
    data = None
    for ev in payload.get("progress") or []:
        if ev.get("event") == name:
            data = ev.get("data") or {}
    return data


def build_observed_debug(payload: dict) -> dict:
    # The live turn carries api_result_meta/graph_result on query_complete.debug;
    # that primary path is authoritative. (A former search_complete api_ok/neo4j_ok
    # backfill was dead — the live search_complete event emits {source, ok, count},
    # not api_ok/neo4j_ok — so it is intentionally omitted.)
    return dict((_last(payload, "query_complete") or {}).get("debug") or {})


def _collect_paths(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_paths(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_paths(item, out)


def build_artifact_index(debug: dict, payload: dict) -> dict[str, str]:
    """Map produced-artifact basename -> absolute path for this turn.

    Sources: ``report_saved_files`` in the debug payload (reporter/pipeline
    writes) and any ``files`` carried on the query_complete event.
    """
    paths: list[str] = []
    _collect_paths(debug.get("report_saved_files") or {}, paths)
    qc = _last(payload, "query_complete") or {}
    for entry in qc.get("files") or []:
        if isinstance(entry, dict):
            candidate = entry.get("path") or entry.get("name")
            if candidate:
                paths.append(str(candidate))
        elif isinstance(entry, str):
            paths.append(entry)
    return {PurePosixPath(p).name: p for p in paths if p}


def _count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix == ".csv":
        with open(path, newline="", encoding="utf-8") as fh:
            return max(0, len(list(csv.reader(fh))) - 1)  # exclude header
    if path.suffix in (".xlsx", ".xls"):
        from openpyxl import load_workbook  # noqa: PLC0415
        wb = load_workbook(path, read_only=True)
        ws = wb.active
        count = sum(1 for _ in ws.iter_rows(min_row=2)) if ws else 0
        wb.close()
        return count
    return sum(1 for line in path.read_text().splitlines() if line.strip())


def resolve_artifact(index: dict[str, str], field: str) -> Any:
    """Resolve one `api_artifact.<name>[.rows_gte]` field from the turn's index."""
    sub = field[len(ARTIFACT_PREFIX):]
    if sub.endswith(".rows_gte"):
        name = sub[: -len(".rows_gte")]
        path = index.get(name)
        return 0 if path is None else _count_rows(Path(path))
    return sub in index


def augment_debug(debug: dict, obs: ro.RouteObservation, bundle_summary: dict | None = None,
                  artifact_index: dict[str, str] | None = None) -> dict:
    debug = dict(debug)
    debug["route"] = obs.route
    debug["engine"] = obs.engine
    debug["route_source"] = obs.source
    if bundle_summary is not None:
        debug["bundle"] = bundle_summary
    if artifact_index is not None:
        debug[ARTIFACT_INDEX_KEY] = artifact_index
    debug["graph_not_truncated"] = _graph_not_truncated(debug)
    return debug


def _graph_not_truncated(debug: dict) -> bool:
    """True when the graph result is not a capped sample.

    Prefers the real `truncated` flag, which the Neo4j tool now sets by comparing the
    row count against the query's own trailing LIMIT. The sentinel comparison below is
    only a fallback for results produced before that flag existed — it can never be
    more than a guess, and it went stale the moment the limit moved from 250 to 5000.

    True for non-graph turns too, so the criterion is only meaningful where a graph
    query actually ran.
    """
    graph = debug.get("graph_result") or {}
    if "truncated" in graph:
        return not bool(graph.get("truncated"))
    count = graph.get("count")
    return not (isinstance(count, int) and count in GRAPH_LIMIT_SENTINELS)


def _criterion_parts(crit) -> tuple[str | None, str | None, Any]:
    if isinstance(crit, dict):
        return crit.get("field"), crit.get("op"), crit.get("value")
    return getattr(crit, "field", None), getattr(crit, "op", None), getattr(crit, "value", None)


def observe_values(debug: dict, criteria: list, *, last_reply: str | None = None) -> dict[str, Any]:
    """Resolve every criterion field to its OBSERVED value.

    check_pass reports only pass/fail plus a prose reason, so a manifest built
    from it cannot answer "what did it actually return?". Resolving alongside it
    keeps the run self-triaging without touching the vendored e2e DSL.
    """
    index = debug.get(ARTIFACT_INDEX_KEY) or {}
    observed: dict[str, Any] = {}
    for crit in criteria:
        field, _op, _value = _criterion_parts(crit)
        if not field:
            continue
        try:
            if field.startswith(ARTIFACT_PREFIX):
                observed[field] = resolve_artifact(index, field)
            else:
                observed[field] = resolve_field(debug, field, last_reply=last_reply)
        except Exception as exc:  # a resolver blowing up must not fail the run
            observed[field] = f"<unresolved: {type(exc).__name__}>"
    return observed


def _split_local_criteria(criteria: list) -> tuple[list, list]:
    """Partition into (locally evaluated criteria, delegated criteria)."""
    local, rest = [], []
    for crit in criteria:
        field, _op, _value = _criterion_parts(crit)
        (local if (field or "").startswith(ARTIFACT_PREFIX) else rest).append(crit)
    return local, rest


def evaluate_turn(payload, criteria, obs, *, last_reply=None, bundle_summary=None):
    raw_debug = build_observed_debug(payload)
    debug = augment_debug(raw_debug, obs, bundle_summary,
                          artifact_index=build_artifact_index(raw_debug, payload))
    local_criteria, delegated = _split_local_criteria(criteria)
    passed, results = check_pass(debug, delegated, last_reply=last_reply)

    index = debug.get(ARTIFACT_INDEX_KEY) or {}
    for crit in local_criteria:
        field, op, expected = _criterion_parts(crit)
        is_artifact = field.startswith(ARTIFACT_PREFIX)
        actual = (resolve_artifact(index, field) if is_artifact
                  else resolve_field(debug, field, last_reply=last_reply))
        if op == "neq":
            ok = actual != expected
            reason = (f"is {actual!r} (must not be {expected!r})" if not ok
                      else f"{actual!r} != {expected!r}")
        elif op == "true":
            ok = actual is True
            reason = "produced" if ok else f"not produced (have: {sorted(index) or 'nothing'})"
        elif op == "gte":
            try:
                ok = int(actual) >= int(expected)
            except (TypeError, ValueError):
                ok = False
            reason = f"{actual} (needed >= {expected})"
        else:
            ok, reason = False, f"unsupported op {op!r} for a locally-evaluated criterion"
        results.append({"field": field, "op": op, "value": expected,
                        "passed": ok, "reason": f"{field}: {reason}"})
        passed = passed and ok

    return passed, results, observe_values(debug, criteria, last_reply=last_reply)
