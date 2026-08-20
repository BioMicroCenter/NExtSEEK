"""Per-turn route selection for the additive assistant.

Wraps dmac_assistant's BAML classifier and router with Plan 018 V4-6 split:
classification (family only) is separate from routing (destination/model).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

try:
    from .router_context import HistoryTurn
except ImportError:
    from router_context import HistoryTurn

from nextseek_api.cc_assistant import posterior_selector
from nextseek_api.cc_assistant import transport_trace
from nextseek_api.cc_assistant.baml_introspect import declared_family_members, validate_member
from nextseek_api.cc_assistant.family_labels import (
    corpus_snapshot,
    runtime_type_builder,
    type_builder,
)

logger = logging.getLogger(__name__)

ROUTE_NS = "nextseek_query"
ROUTE_CC = "container_cc"
ROUTE_UNRELATED = "unrelated"
_FALLBACK_SENTINEL = "<router_unavailable>"

UNRELATED_CANNED_TEXT = (
    "I'm the NExtSEEK research assistant for the MIT BioMicro Center. I can "
    "help with the lab's samples, projects, studies, sequencing and other "
    "research data, lineage, and related analysis tasks — but that question "
    "is outside that scope, so I can't help with it here. Try asking about "
    "your lab's samples, projects, or data."
)

_CC_PATTERNS = re.compile(
    r"\b(write|refactor|script|code|debug|implement|plot|chart|"
    r"summari[sz]e|read|open|file|/data/|walk me through|generate a (python|shell|sql))\b",
    re.IGNORECASE,
)
_NS_PATTERNS = re.compile(
    r"\b(find|search|how many|list|show me|which|what|samples?|mice|mouse|"
    r"monkey|monkeys|pbmc|patient|patients|treated|study|studies|assay|assays|"
    r"project|projects|lineage|cohort|tissue|specimen)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    model_class: str | None
    model_id: str | None
    reasoning: str
    source: str
    task_family: str | None = None
    family_source: str | None = None
    generation_id: int | None = None
    generation_hash: str = ""
    attempted_route: str | None = None
    attempted_source: str | None = None


def _build_context_dir() -> Path | None:
    try:
        from django.conf import settings

        base = Path(settings.BASE_DIR)
    except Exception:
        base = Path(__file__).resolve().parents[2]
    cand = base / "dmac_assistant" / "build_context"
    return cand if cand.is_dir() else None


def _resolve_model_id(model_class_key: str | None) -> str | None:
    if not model_class_key:
        return None
    ctx = _build_context_dir()
    try:
        from dmac_assistant.router.models import load_model_class_map

        path = (ctx / "router_model_class_map.json") if ctx else None
        mapping = load_model_class_map(path=path)
        return mapping.get(model_class_key.lower())
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: model-id resolution failed (%s)", type(exc).__name__)
        return None


def _resolve_cc_model_id() -> str | None:
    try:
        from dmac_assistant.router.models import resolve_cc_model

        return resolve_cc_model()
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: opus model-id resolution failed (%s)", type(exc).__name__)
        return None


def _heuristic(query: str) -> RouteDecision:
    """Keyword fallback when BAML routing is unavailable.

    Defaults to the NS route (cheap + deterministic) unless the query clearly
    asks for file I/O, code, or open-ended agentic work.
    """
    cc = bool(_CC_PATTERNS.search(query))
    ns = bool(_NS_PATTERNS.search(query))
    if cc and not ns:
        route = ROUTE_CC
    elif ns and not cc:
        route = ROUTE_NS
    elif cc and ns:
        # Mixed signals: prefer CC only if a code/file verb leads.
        route = ROUTE_CC if _CC_PATTERNS.search(query.split()[0] if query.split() else "") else ROUTE_NS
    else:
        route = ROUTE_NS
    return RouteDecision(
        route=route,
        # CC is pinned to Opus (proxy-allowlisted); the heuristic no longer
        # defaults to sonnet (which would 403 at the proxy).
        model_class="opus" if route == ROUTE_CC else None,
        model_id=_resolve_cc_model_id() if route == ROUTE_CC else None,
        reasoning="heuristic (BAML router unavailable)",
        source="heuristic",
    )


def _load_router_deps():
    from dmac_assistant.router.agent import RouterAgent
    from dmac_assistant.router.baml_client import b
    from dmac_assistant.router.baml_client.types import Route
    from dmac_assistant.router.capabilities import load_capabilities

    transport_trace.install_transport_hooks(b)
    return RouterAgent, load_capabilities, Route, b


def _history_to_baml(history: list[HistoryTurn] | None, Route):
    from dmac_assistant.router.baml_client.types import HistoryTurn as BamlHistoryTurn

    out = []
    for turn in history or []:
        router_choice = None
        if turn.router_choice == ROUTE_CC:
            router_choice = Route.ContainerCC
        elif turn.router_choice == ROUTE_NS:
            router_choice = Route.NextseekQuery
        elif turn.router_choice == ROUTE_UNRELATED:
            router_choice = Route.Unrelated
        out.append(
            BamlHistoryTurn(
                position=turn.position,
                user_message=turn.user_message,
                assistant_reply=turn.assistant_reply,
                router_choice=router_choice,
                status=turn.status,
                error=turn.error,
                result_count=turn.result_count,
                sample_uids=list(turn.sample_uids or []),
            )
        )
    return out


def _route_from_baml(decision, Route) -> RouteDecision | None:
    if decision.reasoning == _FALLBACK_SENTINEL:
        return None
    if decision.route == Route.ContainerCC:
        route = ROUTE_CC
    elif decision.route == Route.Unrelated:
        route = ROUTE_UNRELATED
    else:
        route = ROUTE_NS
    return RouteDecision(
        route=route,
        model_class="opus" if route == ROUTE_CC else None,
        model_id=_resolve_cc_model_id() if route == ROUTE_CC else None,
        reasoning=decision.reasoning or "baml",
        source="baml",
    )


def _classify_query(query: str, history: list[HistoryTurn] | None = None) -> tuple[str | None, str | None, str]:
    """Return (task_family, family_source, reasoning). None family on failure/unrelated."""
    try:
        _, _, Route, b = _load_router_deps()
        from dmac_assistant.router.baml_client.types import ClassificationInput

        snap = corpus_snapshot()
        allowed = declared_family_members(type_builder(snap))
        builder = runtime_type_builder(snap)
        request = ClassificationInput(user_query=query, history=_history_to_baml(history, Route))
        decision = asyncio.run(b.ClassifyQuery(input=request, baml_options={"tb": builder}))
        label = getattr(decision.task_family, "value", decision.task_family) if decision.task_family else None
        if label is None:
            return None, None, decision.reasoning or "unrelated"
        if not validate_member(str(label), allowed):
            return None, None, f"invalid family label {label!r}"
        return str(label), "baml", decision.reasoning or "baml"
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: classification failed (%s)", type(exc).__name__)
        return None, None, str(exc)


def _route_query(query: str, history: list[HistoryTurn] | None = None) -> RouteDecision | None:
    try:
        RouterAgent, load_capabilities, Route, b = _load_router_deps()
        from dmac_assistant.router.baml_client.types import RouterInput

        ctx = _build_context_dir()
        caps = load_capabilities(path=(ctx / "route_capabilities.json") if ctx else None)
        agent = RouterAgent(capabilities=caps)
        # RouterAgent.route uses internal RouteQuery; use traced b directly for observation.
        request = RouterInput(
            user_query=query,
            routes=caps,
            history=_history_to_baml(history, Route),
        )
        decision = asyncio.run(b.RouteQuery(input=request))
        return _route_from_baml(decision, Route)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: RouteQuery failed (%s)", type(exc).__name__)
        return None


def _legacy_decide(query: str, history: list[HistoryTurn] | None = None) -> RouteDecision:
    routed = _route_query(query, history)
    if routed is not None:
        return routed
    return _heuristic(query)


def _posterior_enabled_decide(query: str, history: list[HistoryTurn] | None = None) -> RouteDecision:
    try:
        snap = corpus_snapshot()
        # Validate the source-derived recipe before any provider transport.
        # The generated runtime builder is constructed in _classify_query and
        # passed to that exact BAML call.
        type_builder(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: corpus/typebuilder invalid pre-transport (%s)", type(exc).__name__)
        routed = _route_query(query, history)
        decision = routed if routed is not None else _heuristic(query)
        return replace(
            decision,
            task_family=None,
            family_source=None,
            reasoning=f"pre-transport invalid: {exc}; {decision.reasoning}",
        )

    task_family, family_source, classify_reason = _classify_query(query, history)
    if task_family is None and "unrelated" in (classify_reason or "").lower():
        return RouteDecision(
            route=ROUTE_UNRELATED,
            model_class=None,
            model_id=None,
            reasoning=classify_reason,
            source="baml",
            task_family=None,
            family_source=None,
        )

    if task_family is None:
        routed = _route_query(query, history)
        decision = routed if routed is not None else _heuristic(query)
        return replace(
            decision,
            task_family=None,
            family_source=None,
            reasoning=f"classification failed: {classify_reason}; {decision.reasoning}",
        )

    try:
        selected = posterior_selector.select_route(task_family)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: posterior selection failed (%s)", type(exc).__name__)
        selected = None
    if selected is not None:
        return RouteDecision(
            route=selected.route,
            model_class="opus" if selected.route == ROUTE_CC else None,
            model_id=_resolve_cc_model_id() if selected.route == ROUTE_CC else None,
            reasoning=selected.reasoning,
            source="posterior",
            task_family=task_family,
            family_source=family_source,
            generation_id=selected.generation_id,
            generation_hash=selected.generation_hash,
        )

    routed = _route_query(query, history)
    decision = routed if routed is not None else _heuristic(query)
    return replace(
        decision,
        task_family=task_family,
        family_source=family_source,
        reasoning=f"posterior fallback: {decision.reasoning}",
    )


def decide(query: str, history: list[HistoryTurn] | None = None) -> RouteDecision:
    """Return the route decision for a user query."""
    if posterior_selector.posterior_routing_enabled():
        return _posterior_enabled_decide(query, history)
    return _legacy_decide(query, history)
