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
    # The live turn carries api_result_meta/graph_result on query_complete.debug;
    # that primary path is authoritative. (A former search_complete api_ok/neo4j_ok
    # backfill was dead — the live search_complete event emits {source, ok, count},
    # not api_ok/neo4j_ok — so it is intentionally omitted.)
    return dict((_last(payload, "query_complete") or {}).get("debug") or {})


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
