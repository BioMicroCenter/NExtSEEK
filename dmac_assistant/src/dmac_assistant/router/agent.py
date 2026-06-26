"""Bridge-side BAML wrapper for the LLM router."""
from __future__ import annotations

import asyncio
import logging
import time

from dmac_assistant.router.baml_client import b
from dmac_assistant.router.baml_client.types import (
    ModelClass,
    Route,
    RouteCapability,
    RouterDecision,
    RouterInput,
)
from dmac_assistant.router.capabilities import load_capabilities


log = logging.getLogger(__name__)

_FALLBACK_REASONING = "<router_unavailable>"

# `Route.NextseekQuery.name.lower()` -> "nextseekquery", not the BAML
# @alias "nextseek_query"; the WS-facing alias requires an explicit map.
# Duplicated narrowly here (also lives in ws.py) to keep telemetry independent
# of import direction (ws.py imports RouterAgent, not the reverse).
_ROUTE_ALIAS: dict[Route, str] = {
    Route.NextseekQuery: "nextseek_query",
    Route.ContainerCC: "container_cc",
    Route.Unrelated: "unrelated",
}


def _model_class_alias(mc: ModelClass | None) -> str | None:
    if mc is None:
        return None
    return mc.name.lower()


def _fallback_decision() -> RouterDecision:
    return RouterDecision(
        route=Route.ContainerCC,
        model_class=ModelClass.Sonnet,
        reasoning=_FALLBACK_REASONING,
    )


class RouterAgent:
    """Async router wrapper around BAML's RouteQuery function."""

    def __init__(self, capabilities: list[RouteCapability] | None = None) -> None:
        if capabilities is None:
            capabilities = load_capabilities()
        self._capabilities = capabilities

    async def route(self, user_query: str) -> RouterDecision:
        """Return a router decision, falling back if the BAML call fails."""
        request = RouterInput(
            user_query=user_query,
            routes=self._capabilities,
        )
        start = time.monotonic()
        try:
            decision = await b.RouteQuery(input=request)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - broad catch required by router spec
            log.warning(
                "router_fallback",
                extra={
                    "router_fallback": True,
                    "exc_type": type(exc).__name__,
                },
            )
            return _fallback_decision()

        # R-03: never log reasoning text or user_query — only structural
        # facts (route alias, model class alias, latency, reasoning length).
        #
        # Visibility: this record is emitted at INFO level. The bridge runtime
        # must be configured at INFO or below for `dmac_assistant.router.agent`
        # (root logger or this module specifically). uvicorn's `--log-level
        # error` silences this; see docs/bridge/README.md routing section.
        log.info(
            "router_decision",
            extra={
                "route": _ROUTE_ALIAS.get(decision.route, decision.route.name),
                "model_class": _model_class_alias(decision.model_class),
                "decision_latency_ms": (time.monotonic() - start) * 1000.0,
                "reasoning_len": len(decision.reasoning) if decision.reasoning else 0,
            },
        )
        return decision
