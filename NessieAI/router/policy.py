"""Routing policy: which engine a chat turn goes to, and the ledger row it leaves.

``_decide_route`` applies the override precedence around the router:
``force_route`` > ``pipeline_agent`` > a turn that refers back goes to CC (labelled
``sticky`` in a chat already on CC, ``followup`` otherwise) > :func:`router.decide`.
``followup.followup_reason`` is the refers-back test and ``_chat_is_sticky_cc`` the
label. ``_fallback_when_cc_unavailable`` hands a turn the policy moved to CC back to
NExtSEEK when the CC runner is down, and ``_record_ledger_row`` writes the per-turn
routing row through :mod:`NessieAI.router.turn_ledger`, best effort.

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

from NessieAI.router import followup as followup_rule
from NessieAI.router import router as cc_router
from NessieAI.router import router_context

logger = logging.getLogger(__name__)


def _turn_route(turn) -> str | None:
    """The route a history turn or a raw ``chat_log`` entry ran on."""
    if isinstance(turn, dict):
        return router_context._derive_router_choice(turn)
    return turn.router_choice


def _turn_status(turn) -> str:
    if isinstance(turn, dict):
        status = turn.get("status")
        return status if status in ("completed", "error") else "completed"
    return turn.status


def _chat_is_sticky_cc(turns) -> bool:
    """True when ANY turn of this chat ran on Container-CC and completed.

    It no longer moves a turn by itself (2026-09-23 ruling): a turn moves to CC only
    when it also refers back to an earlier turn (``followup.followup_reason``), and
    this decides only whether that move is labelled ``sticky`` (the chat is on CC) or
    ``followup``. ``turns`` is the whole ``chat_log`` when the caller has it, so the
    label does not depend on the router's 5-turn window, and a later CC error or a
    forced NS turn does not change it. A chat whose only CC turns errored is not on CC.
    """
    for turn in turns or []:
        if _turn_route(turn) == cc_router.ROUTE_CC and _turn_status(turn) == "completed":
            return True
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


def _decide_route(user, req, *, force_cc: bool, session=None,
                  history: list[router_context.HistoryTurn] | None = None,
                  chat_log: list[dict] | None = None) -> cc_router.RouteDecision:
    """Pick the route for a query, honoring the admin-only ``force_route`` override.

    Precedence: ``force_route`` > ``pipeline_agent`` > a turn that refers back to an
    answered turn goes to CC > the router. ``history`` is the router's 5-turn window;
    ``chat_log``, when the caller has it, is the whole chat, which the refers-back guard
    scans instead of the window.
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
    # Follow-ups and sticky CC (2026-09-23 rulings: "have all follow ups go to
    # container_cc", and once a chat is on container_cc a turn that refers back stays
    # there). One guard, because the two rules are one test: an NS-bound turn that
    # REFERS BACK to an answered turn of this chat goes to CC. `followup.py` defines
    # "refers back" (a back-reference cue: "of those", "which of them", "that chart",
    # "the file", "remind me", ...) and so defines "self-contained" as its complement.
    # A self-contained question is routed normally, even in a chat already on CC: a
    # replay of 90 production turns found whole-chat stickiness pulled 20 extra turns
    # onto CC at ~128 s each against ~29 s on NS.
    #
    # The label says where the chat is: "sticky" when a turn of this chat has completed
    # on CC (the referent may be CC's own result; the literal is read by the harness
    # and the ledger), "followup" when only NS turns came before.
    #
    # ORDER IS LOAD-BEARING, for the same reason spelled out on the pipeline gate
    # above: a guard that short-circuits BEFORE the router captures every following
    # turn without the model ever seeing the query. It runs AFTER cc_router.decide and
    # only ever redirects an NS-bound turn. ROUTE_UNRELATED is deliberately excluded:
    # converting it would spin up an Opus container for an out-of-scope question
    # instead of returning the canned refusal.
    #
    # Delete this block to give follow-ups back to the NExtSEEK engine, whose
    # follow-up code is untouched.
    turns = chat_log if chat_log is not None else history
    try:
        cue = (followup_rule.followup_reason(req.query, turns)
               if decision.route == cc_router.ROUTE_NS else None)
        on_cc = bool(cue) and _chat_is_sticky_cc(turns)
    except Exception:  # noqa: BLE001 - routing must never crash on bad history
        logger.warning("CC router: follow-up/sticky history inspection failed", exc_info=True)
        cue, on_cc = None, False
    if cue:
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_CC, model_class="opus",
            model_id=cc_router._resolve_cc_model_id(),
            reasoning=(f"{'sticky_cc' if on_cc else 'followup_cc'} ({cue}); "
                       f"router said ns ({decision.reasoning})"),
            source="sticky" if on_cc else "followup",
            task_family=decision.task_family,
            family_source=decision.family_source,
            generation_id=decision.generation_id,
            generation_hash=decision.generation_hash,
            attempted_route=decision.route,
            attempted_source=decision.source,
        )
    return decision


#: Sources of a CC decision this module made out of an NS one. Only these fall back.
POLICY_CC_SOURCES = frozenset({"sticky", "followup"})


def _fallback_when_cc_unavailable(decision: cc_router.RouteDecision,
                                  available) -> cc_router.RouteDecision:
    """Hand a policy-made CC turn back to NExtSEEK when the CC runner is down.

    ``available`` is ``cc_engine.cc_runner_available`` (``() -> (ok, detail)``), passed
    in so this module stays free of the engine. Only a turn the router sent to
    NExtSEEK and this policy moved to CC (sticky or follow-up) falls back: the NExtSEEK
    engine can still answer it, with its own follow-up code, where the alternative is a
    "Container-CC route is not available" error on every turn of a chat on a box
    without the agent image. A turn the ROUTER sent to CC, or an admin forced there,
    keeps its error: nothing else can answer it. The fallback lasts one turn; the chat
    stays sticky and the next turn tries CC again.
    """
    if (decision.route != cc_router.ROUTE_CC or decision.source not in POLICY_CC_SOURCES
            or decision.attempted_route != cc_router.ROUTE_NS):
        return decision
    try:
        ok, detail = available()
    except Exception as exc:  # noqa: BLE001 - an unanswerable probe is an unavailable runner
        ok, detail = False, type(exc).__name__
    if ok:
        return decision
    logger.warning("CC router: %s turn falls back to nextseek_query, CC unavailable: %s",
                   decision.source, detail)
    return cc_router.RouteDecision(
        route=cc_router.ROUTE_NS, model_class=None, model_id=None,
        reasoning=f"cc_unavailable ({detail}); was {decision.source}: {decision.reasoning}",
        source="cc_unavailable",
        task_family=decision.task_family,
        family_source=decision.family_source,
        generation_id=decision.generation_id,
        generation_hash=decision.generation_hash,
        attempted_route=cc_router.ROUTE_CC,
        attempted_source=decision.source,
    )
