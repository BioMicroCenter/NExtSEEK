"""Routing policy: which engine a chat turn goes to, and the ledger row it leaves.

``_decide_route`` applies the override precedence around the router:
``force_route`` > ``pipeline_agent`` > sticky CC > :func:`router.decide`.
``_prev_route_was_cc`` is the sticky-CC history scan it uses, and
``_record_ledger_row`` writes the per-turn routing row through
:mod:`NessieAI.router.turn_ledger`, best effort.

Moved verbatim from ``nextseek_api/services/cc_assistant.py`` (Phase B of the
NessieAI consolidation). The CC turn, :mod:`NessieAI.cc.turn`, imports
``_decide_route`` and ``_record_ledger_row`` and calls them; nothing here
imports ``nextseek_api.services``. ``ChatSession`` appears only in an
annotation, which ``from __future__ import annotations`` keeps a string, so
this module adds no ORM edge of its own (the ledger write goes through
``turn_ledger``, lazily).
"""
from __future__ import annotations

import logging

from chat_nextseek.pipeline import agent as pipeline_agent

from NessieAI.router import router as cc_router
from NessieAI.router import router_context

logger = logging.getLogger(__name__)


def _prev_route_was_cc(history: list[router_context.HistoryTurn] | None) -> bool:
    """True when the most recent ENGINE-RUNNING turn in this chat ran CC and completed.

    ``unrelated`` turns are transparent. They never reach an engine, never produce
    a bundle and carry no routing state worth inheriting, so scanning past them is
    what keeps one off-topic aside from silently ending stickiness: with a plain
    ``history[-1]`` test, "cluster these samples" (CC) -> "what's the weather"
    (unrelated) -> "now group those by genotype" dropped back to NS and then failed
    for want of an NS bundle to refine, which is the exact breakage sticky CC
    exists to prevent.

    The scan stops at the FIRST engine-running turn it finds, whatever its status.
    That is deliberate: a failed CC turn must NOT trap the chat on a route that
    just broke, so we must not keep looking past it for an older healthy CC turn.

    Bounded by ``router_context.MAX_HISTORY_TURNS`` (5), so three consecutive
    ``unrelated`` turns push the CC turn out of the window and stickiness is lost
    regardless. Accepted: the window is the router's own context limit.
    """
    for turn in reversed(history or []):
        if turn.router_choice == cc_router.ROUTE_UNRELATED:
            continue
        return (turn.router_choice == cc_router.ROUTE_CC
                and turn.status == "completed")
    return False


def _record_ledger_row(chat_session: ChatSession, decision: cc_router.RouteDecision,
                       query_task=None) -> None:
    """Best-effort ledger write; must not fail the user turn.

    ``len(chat_log) + 1`` is only the floor of the turn number: a turn that died before
    its chat-log append, a chat past the chat-log cap, or a second turn of the chat in
    flight all read the same length again, and ``record_next_turn`` moves past a number
    already taken instead of dropping the row. ``query_task`` ties the row to the
    ``QueryTask`` the turn ran as.
    """
    from NessieAI.router.turn_ledger import ALLOCATION_ATTEMPTS, LedgerCollision, record_next_turn

    chat_log = (chat_session.extra_state or {}).get("chat_log") or []
    turn_number = len(chat_log) + 1
    try:
        record_next_turn(
            str(chat_session.session_id),
            turn_number,
            decision.route,
            decision.source,
            decision.task_family,
            decision.family_source,
            query_task=query_task,
            pinned_generation_id=decision.generation_id,
            pinned_generation_hash=decision.generation_hash or "",
            attempted_route=decision.attempted_route,
            attempted_source=decision.attempted_source,
        )
    except LedgerCollision:
        logger.error(
            "ledger collision for session=%s from turn=%s: all %d numbers tried were taken "
            "first, so this turn has no ledger row",
            chat_session.session_id,
            turn_number,
            ALLOCATION_ATTEMPTS,
        )
    except Exception:
        logger.exception(
            "ledger write failed for session=%s turn=%s",
            chat_session.session_id,
            turn_number,
        )


def _decide_route(user, req, *, force_cc: bool, session=None, history: list[router_context.HistoryTurn] | None = None) -> cc_router.RouteDecision:
    """Pick the route for a query, honoring the admin-only ``force_route`` override.

    Precedence: ``force_route`` > ``pipeline_agent`` > sticky CC > the router.
    An explicit force (``force_cc`` or an admin's ``force_route``) wins first,
    THEN the BAML router (:func:`cc_router.decide`) decides, and two guards may
    still redirect an NS-bound turn: an active ``pipeline_agent`` wizard keeps
    it on NExtSEEK, and a chat whose previous turn completed on CC keeps it on
    CC (A1 "sticky CC"). A non-admin's
    ``force_route`` is ignored and falls back to the router (mirrors
    ``use_prod``'s server-side admin gate). Forced decisions are
    ``ROUTE_NS``/``ROUTE_CC`` (never ``ROUTE_UNRELATED``), so a forced query
    always runs on the chosen path instead of hitting the out-of-scope canned
    reply.
    """
    forced = getattr(req, "force_route", None)
    if forced in ("ns", "cc"):
        # is_superuser ALONE. dmac/views.py:80,97 sets is_staff = 1 on every SEEK
    # user at registration and at every login, so `or is_staff` admitted every
    # authenticated account. Same predicate as seek/views.py verifySuperUser and
    # AdminSampleViewSet (#74).
        is_admin = bool(getattr(user, "is_superuser", False))
        if not is_admin:
            forced = None  # non-admins can never force a route

    if force_cc or forced == "cc":
        # CC always runs Opus (the only proxy-allowlisted model); hardcoding
        # sonnet here would 403 at the Bedrock proxy.
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_CC, model_class="opus",
            model_id=cc_router._resolve_cc_model_id(),
            reasoning="forced", source="forced",
        )
    if forced == "ns":
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_NS, model_class=None,
            model_id=None, reasoning="forced", source="forced",
        )
    decision = cc_router.decide(req.query, history=history)
    if (
        session is not None
        and pipeline_agent.is_active(session)
        and decision.route == cc_router.ROUTE_NS
    ):
        # A pipeline wizard is mid-flow: keep confirm/tweak/launch turns on the NS
        # route so they reach pipeline_agent.handle_turn.
        #
        # This used to short-circuit BEFORE consulting the router, which let an
        # open build capture every following turn — a plain sample search got
        # answered by the samplesheet builder ("searching the database isn't
        # something I can do") because the model never saw the query. Now the
        # router decides first and only an NS-bound turn is handed to the wizard;
        # the wizard itself calls `handoff` when the turn is not about the build,
        # which the orchestrator turns into a passthrough.
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_NS, model_class=None, model_id=None,
            reasoning=f"pipeline_active; router said ns ({decision.reasoning})",
            source="pipeline",
        )
    # A1 (sticky CC): the router classifies each turn independently, so a
    # conversation that starts on CC gets yanked back to NS mid-thread and the
    # follow-up fails for want of an NS bundle to refine ("Find samples from a
    # 4 week study." -> CC, "Just the 4 week ones." -> NS -> broken). Once a CC
    # turn completes, keep the chat on CC.
    #
    # ORDER IS LOAD-BEARING, for the same reason spelled out on the pipeline
    # gate above: a guard that short-circuits BEFORE the router captures every
    # following turn without the model ever seeing the query. Both guards run
    # AFTER cc_router.decide and only ever redirect an NS-bound turn. Do not
    # "simplify" this to the top of the function.
    #
    # ROUTE_UNRELATED is deliberately excluded -- converting it would spin up an
    # Opus container for an out-of-scope question instead of returning the
    # canned refusal. Only NS -> CC.
    try:
        sticky = decision.route == cc_router.ROUTE_NS and _prev_route_was_cc(history)
    except Exception:  # noqa: BLE001 - routing must never crash on bad history
        logger.warning("CC router: sticky-CC history inspection failed", exc_info=True)
        sticky = False
    if sticky:
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_CC, model_class="opus",
            model_id=cc_router._resolve_cc_model_id(),
            reasoning=f"sticky_cc; router said ns ({decision.reasoning})",
            source="sticky",
            task_family=decision.task_family,
            family_source=decision.family_source,
            generation_id=decision.generation_id,
            generation_hash=decision.generation_hash,
            attempted_route=decision.route,
            attempted_source=decision.source,
        )
    return decision
