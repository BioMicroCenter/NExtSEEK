from __future__ import annotations
from dataclasses import dataclass

ROUTE_NS = "nextseek_query"
ROUTE_CC = "container_cc"
ROUTE_UNRELATED = "unrelated"


@dataclass
class RouteObservation:
    route: str | None
    model_class: str | None
    source: str | None
    reasoning: str | None
    parser_mode: str | None
    engine: str | None


def _events(payload: dict) -> list[dict]:
    return payload.get("progress") or []


def _first(payload, name):
    for ev in _events(payload):
        if ev.get("event") == name:
            return ev.get("data") or {}
    return None


def _last(payload, name):
    data = None
    for ev in _events(payload):
        if ev.get("event") == name:
            data = ev.get("data") or {}
    return data


def has_route_decided(payload: dict) -> bool:
    return _first(payload, "route_decided") is not None


def _parser_mode(payload, debug):
    pp = debug.get("parser_plan") or {}
    if pp.get("mode"):
        return pp["mode"]
    for ev in _events(payload):
        d = ev.get("data") or {}
        if ev.get("event") == "agent_complete" and d.get("agent") == "parser":
            return (d.get("summary") or {}).get("mode")
    return None


def _engine(route, parser_mode, debug, model_class):
    if route == ROUTE_CC:
        return f"container_cc:{model_class}" if model_class else "container_cc"
    if route == ROUTE_UNRELATED:
        return "unrelated"
    if route == ROUTE_NS:
        if parser_mode == "graph_query":
            return "graph_query"
        endpoint = (debug.get("api_plan") or {}).get("endpoint") \
            or (debug.get("parser_plan") or {}).get("target_endpoint")
        return endpoint or parser_mode
    return None


def observe(payload: dict) -> RouteObservation:
    rd = _first(payload, "route_decided") or {}
    debug = (_last(payload, "query_complete") or {}).get("debug") or {}
    mode = _parser_mode(payload, debug)
    return RouteObservation(
        route=rd.get("route"), model_class=rd.get("model_class"),
        source=rd.get("source"), reasoning=rd.get("reasoning"),
        parser_mode=mode, engine=_engine(rd.get("route"), mode, debug, rd.get("model_class")),
    )
