from __future__ import annotations
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.criteria import check_pass  # noqa: E402
from nessie_tests import route_observer as ro


def _last(payload, name):
    data = None
    for ev in payload.get("progress") or []:
        if ev.get("event") == name:
            data = ev.get("data") or {}
    return data


def build_observed_debug(payload: dict) -> dict:
    debug = dict((_last(payload, "query_complete") or {}).get("debug") or {})
    sc = _last(payload, "search_complete") or {}
    if "api_result_meta" not in debug and "api_ok" in sc:
        debug["api_result_meta"] = {"ok": sc.get("api_ok")}
    if "graph_result" not in debug and "neo4j_ok" in sc:
        debug["graph_result"] = {"ok": sc.get("neo4j_ok")}
    return debug


def augment_debug(debug: dict, obs: ro.RouteObservation, bundle_summary: dict | None = None) -> dict:
    debug = dict(debug)
    debug["route"] = obs.route
    debug["engine"] = obs.engine
    debug["route_source"] = obs.source
    if bundle_summary is not None:
        debug["bundle"] = bundle_summary
    return debug


def evaluate_turn(payload, criteria, obs, *, last_reply=None, bundle_summary=None):
    debug = augment_debug(build_observed_debug(payload), obs, bundle_summary)
    return check_pass(debug, criteria, last_reply=last_reply)
