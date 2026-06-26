"""Per-turn route selection for the additive assistant.

Wraps dmac_assistant's BAML ``RouterAgent`` (the intelligent toggle between the
deterministic NExtSEEK pipeline and the sandboxed Container-Claude-Code path)
and adapts it to NExtSEEK's synchronous daemon-worker context.

Two robustness layers on top of dmac's router:

1. dmac's ``RouterAgent`` already falls back to ``ContainerCC/Sonnet`` on any
   BAML failure (sentinel reasoning ``<router_unavailable>``). But "everything
   to CC" is wrong for plain lookups (e.g. "Find me all mice treated with
   NDMA"), so when we detect that sentinel — or any import/runtime failure — we
   apply a keyword **heuristic** instead.
2. All dmac imports are lazy and guarded, so a vendoring / uv-sync hiccup can
   never stop Django from booting; the route just falls back to the heuristic.

Routes returned: ``"nextseek_query"`` (NS) or ``"container_cc"`` (CC).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

ROUTE_NS = "nextseek_query"
ROUTE_CC = "container_cc"
_FALLBACK_SENTINEL = "<router_unavailable>"

# Keyword heuristic used only when BAML routing is unavailable.
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
    route: str                 # ROUTE_NS | ROUTE_CC
    model_class: str | None    # "opus" | "sonnet" | "haiku" | None
    model_id: str | None       # Bedrock-qualified id for the CC route
    reasoning: str
    source: str                # "baml" | "heuristic"


def _build_context_dir() -> Path | None:
    """Locate the vendored router config dir (BASE_DIR/dmac_assistant/build_context)."""
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
    """OI-5 / audit B1: the CC route ALWAYS runs the single auto-mode Opus tier —
    the ONLY model the Bedrock auth-proxy allowlists. Pin it via resolve_cc_model()
    so a router model_class of sonnet/haiku (or the heuristic's old sonnet default)
    can never reach the proxy and get a 403."""
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


def _baml_decision(query: str) -> RouteDecision | None:
    """Try dmac's BAML router. Return None to signal 'use the heuristic'."""
    try:
        from dmac_assistant.router.agent import RouterAgent
        from dmac_assistant.router.capabilities import load_capabilities
        from dmac_assistant.router.baml_client.types import Route
    except Exception as exc:  # noqa: BLE001
        logger.info("CC router: dmac BAML router unavailable (%s); using heuristic", type(exc).__name__)
        return None

    ctx = _build_context_dir()
    try:
        caps = load_capabilities(path=(ctx / "route_capabilities.json") if ctx else None)
        agent = RouterAgent(capabilities=caps)
        decision = asyncio.run(agent.route(query))
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC router: BAML route() failed (%s); using heuristic", type(exc).__name__)
        return None

    if decision.reasoning == _FALLBACK_SENTINEL:
        # dmac's own fallback fired; prefer our heuristic over its CC default.
        return None

    # 3-route world (post-OI-3): NextseekQuery -> NS; ContainerCC -> CC.
    # Unrelated -> NS (cost-safe: deterministic pipeline, no paid agent spawn for
    # off-topic queries). The router's classification is still honored/recorded.
    if decision.route == Route.ContainerCC:
        route = ROUTE_CC
    else:
        route = ROUTE_NS
    return RouteDecision(
        route=route,
        # CC always Opus (proxy-allowlisted); the BAML model_class is not used to
        # pick the CC model id (OI-5).
        model_class="opus" if route == ROUTE_CC else None,
        model_id=_resolve_cc_model_id() if route == ROUTE_CC else None,
        reasoning="baml",
        source="baml",
    )


def decide(query: str) -> RouteDecision:
    """Return the route decision for a user query (BAML-first, heuristic fallback)."""
    decision = _baml_decision(query)
    if decision is not None:
        return decision
    return _heuristic(query)
