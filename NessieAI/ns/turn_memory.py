"""A Container-CC turn's shared state in Django (approach 1). This half (plan 03): the op slots.

At most ``MAX_OPS_IN_FLIGHT`` ops of one turn run at once (operator ruling 2026-09-28): the web server runs few
workers and an api-read op calls back into Django, so more could stall the site. ``CCTurn.ops_in_flight`` counts them.
A slot is taken with one conditional UPDATE (never read, then written), so two ops on two workers cannot both take the
last one, and it is given back in ``run_op``'s ``finally``. Only Django writes the row.

A worker killed outright (SIGKILL, the OOM killer) runs no ``finally``: its slot stays taken until the turn ends,
which halves that one turn's op capacity and touches no other turn.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_OPS_IN_FLIGHT = 2


def take_op_slot(turn: Any) -> bool:
    """Take one of the turn's op slots; False when ``MAX_OPS_IN_FLIGHT`` are already taken."""
    from django.db.models import F

    from nextseek_api.assistant.models_db import CCTurn

    taken = CCTurn.objects.filter(pk=turn.pk, ops_in_flight__lt=MAX_OPS_IN_FLIGHT).update(
        ops_in_flight=F("ops_in_flight") + 1)
    return taken == 1


def release_op_slot(turn: Any) -> None:
    """Give back a slot ``take_op_slot`` took. Never below zero. When the worker's connection dropped during a long op,
    one retry on a fresh connection. Never raises: it runs in a ``finally`` and must not hide the op's own error."""
    from django.db import connection
    from django.db.models import F

    from nextseek_api.assistant.models_db import CCTurn

    for attempt in (1, 2):
        try:
            CCTurn.objects.filter(pk=turn.pk, ops_in_flight__gt=0).update(ops_in_flight=F("ops_in_flight") - 1)
            return
        except Exception:  # InterfaceError is not a DatabaseError; nothing may leave the caller's finally
            if attempt == 2:
                logger.warning("cc turn %s: an op slot could not be given back", turn.pk, exc_info=True)
                return
            try:
                connection.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------------------------------------------
# The memory half (plan 04, piece 3): what the ops of one Container-CC turn share. Every read goes to the database,
# never to the caller's row object: the ops run on different workers, each holding its own copy. Only Django writes
# these fields: a conditional update for the vocabulary, a row lock for plans and strikes, F() for spend.
# ---------------------------------------------------------------------------------------------------------------

import hashlib
from decimal import Decimal
from typing import Any


def _rows(turn):
    from nextseek_api.assistant.models_db import CCTurn
    return CCTurn.objects.filter(pk=turn.pk)


def _locked(turn, field: str):
    from nextseek_api.assistant.models_db import CCTurn
    return CCTurn.objects.select_for_update().only("pk", field).get(pk=turn.pk)


def normalize_question(question: Any) -> str:
    """The question with its whitespace collapsed. Only the entity op uses it, to recognise the user's own question;
    a plan is never keyed by it (``plan_key``)."""
    return " ".join(str(question or "").split())


def plan_key(question: Any) -> str:
    """The sha256 hex of the question's exact utf-8 bytes. Nothing is collapsed or folded: whitespace inside a quoted
    literal ("wt  1" vs "wt 1") is part of what the plan matches, so two such questions get two plans."""
    return hashlib.sha256(str(question or "").encode("utf-8")).hexdigest()


def user_question(turn) -> str:
    """The user's own question for this turn: its QueryTask's query."""
    from nextseek_api.assistant.models_db import QueryTask
    return QueryTask.objects.filter(pk=turn.task_id).values_list("query", flat=True).first() or ""


def get_vocabulary(turn) -> dict | None:
    value = _rows(turn).values_list("vocabulary", flat=True).first()
    return value if isinstance(value, dict) else None


def store_vocabulary(turn, out: dict) -> bool:
    """Store the turn's vocabulary only when it has none; True when this call stored it."""
    return _rows(turn).filter(vocabulary__isnull=True).update(vocabulary=out) == 1


def get_plan(turn, question: str) -> dict | None:
    plans = _rows(turn).values_list("plans", flat=True).first()
    plan = plans.get(plan_key(question)) if isinstance(plans, dict) else None
    return plan if isinstance(plan, dict) else None


def store_plan(turn, question: str, plan: dict) -> None:
    """Keep ``plan`` for ``question`` under the row lock; a plan already stored for it stays."""
    from django.db import transaction
    key = plan_key(question)
    with transaction.atomic():
        row = _locked(turn, "plans")
        plans = dict(row.plans or {})
        if key in plans:
            return
        plans[key] = plan
        _rows(turn).update(plans=plans)


def _strike(item: Any) -> list[str] | None:
    if isinstance(item, (list, tuple)) and len(item) >= 3 and all(isinstance(p, str) and p for p in item[:3]):
        return [item[0], item[1], item[2]]
    return None


def load_strikes(turn) -> list[list[str]]:
    value = _rows(turn).values_list("strikes", flat=True).first() or []
    return [s for s in (_strike(item) for item in value) if s is not None]


def merge_strikes(turn, strikes: list[list[str]]) -> None:
    """Add the models ``strikes`` names that the turn does not hold yet (by provider and model; the first reason
    stands), under the row lock. Strikes are only ever added within a turn."""
    from django.db import transaction
    new = [s for s in (_strike(item) for item in strikes or ()) if s is not None]
    if not new:
        return
    with transaction.atomic():
        row = _locked(turn, "strikes")
        have = [s for s in (_strike(item) for item in row.strikes or ()) if s is not None]
        seen = {(s[0], s[1]) for s in have}
        added = [s for s in new if (s[0], s[1]) not in seen and not seen.add((s[0], s[1]))]
        if added:
            _rows(turn).update(strikes=have + added)


def add_spend(turn, usd: float, *, partial: bool = False) -> None:
    """Add ``usd`` to the turn's op spend with F(); ``partial`` marks that some of it was not seen."""
    from django.db.models import F
    amount = Decimal(str(round(max(float(usd or 0.0), 0.0), 6)))
    fields: dict[str, Any] = {}
    if amount:
        fields["ops_cost_usd"] = F("ops_cost_usd") + amount
    if partial:
        fields["ops_cost_partial"] = True
    if fields:
        _rows(turn).update(**fields)


def mark_cost_partial(turn) -> None:
    """Mark the turn's op cost as a floor without adding to it: an op's spend or failed models could not be written
    (``granular._settle_turn``), so the turn must never report a confident complete cost."""
    _rows(turn).update(ops_cost_partial=True)


def count_vocabulary_resolution(turn) -> None:
    """One more time an op resolved the turn's vocabulary itself (F()); read back at the turn's end."""
    from django.db.models import F
    _rows(turn).update(vocabulary_resolutions=F("vocabulary_resolutions") + 1)


def vocabulary_resolutions(turn) -> int:
    """How many times the turn's ops resolved its vocabulary themselves."""
    return int(_rows(turn).values_list("vocabulary_resolutions", flat=True).first() or 0)


def _usd(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    return float(value)


def nested_turn_costs(turn) -> tuple[float, list[str]]:
    """What the NS turns this Container-CC turn started (``QueryTask.parent_cc_turn``) cost, and what was not counted."""
    from nextseek_api.assistant.models_db import QueryTask
    total, missing = 0.0, []
    for status, result in QueryTask.objects.filter(parent_cc_turn=turn).values_list("status", "result"):
        if status not in ("completed", "error"):
            missing.append("a nested NExtSEEK turn was still running")
            continue
        cost = _usd(result.get("total_cost_usd")) if isinstance(result, dict) else None
        if cost is None:
            missing.append("a nested NExtSEEK turn reported no cost")
            continue
        total += cost
        if result.get("cost_partial") is True:
            missing.append("a nested NExtSEEK turn's cost was partial")
    return round(total, 6), missing


def terminal_cost(turn, data: dict, *, router_fields: dict, prerun_settled: Any) -> dict:
    """``data`` for a Container-CC turn's ``query_complete`` or ``query_error`` with the whole turn's cost.

    ``ops_cost_usd``: the ops' and the pre-run's spend plus the nested NS turns'. ``turn_cost_usd``: Claude Code's
    own cost on the repo's price table (``cost_by_price_table_usd``, else ``total_cost_usd``; both kept unchanged in
    ``data``) plus that plus the router's (``router_fields``, empty on a
    forced turn). ``cost_partial`` is false only when every part was counted; ``cost_partial_reason`` says what was
    not. ``prerun_settled`` is the pre-run hand-off's event, or None when there was no pre-run."""
    row = _rows(turn).values("ops_cost_usd", "ops_cost_partial", "ops_in_flight").first() or {}
    nested, reasons = nested_turn_costs(turn)
    if row.get("ops_cost_partial"):
        reasons.append("an op's model calls were not all priced or seen")
    if (row.get("ops_in_flight") or 0) > 0:
        reasons.append("an op or NS query of this turn was still running when the turn ended")
    if prerun_settled is not None and not prerun_settled.is_set():
        reasons.append("the vocabulary pre-run had not finished")
    # One price table for the whole turn: Claude Code's number on the repo's table (translate.py), the same table the
    # ops, the pre-run, the router and the nested NS turns are priced on; its list-price total only as a fallback.
    cc = _usd(data.get("cost_by_price_table_usd"))
    if cc is None:
        cc = _usd(data.get("total_cost_usd"))
    if cc is None:
        reasons.append("Claude Code's own cost was not reported")
    router = 0.0
    if router_fields:
        router_usd = _usd(router_fields.get("router_cost_usd"))
        if router_usd is None:
            reasons.append("the router's cost was not reported")
        else:
            router = router_usd
        if router_fields.get("router_cost_partial"):
            reasons.append("the router's cost was partial")
    ops = round((_usd(row.get("ops_cost_usd")) or 0.0) + nested, 6)
    out = dict(data)
    out["ops_cost_usd"] = ops
    out["turn_cost_usd"] = round((cc or 0.0) + ops + router, 6)
    out["cost_partial"] = bool(reasons)
    if reasons:
        out["cost_partial_reason"] = "; ".join(dict.fromkeys(reasons))
    else:
        out.pop("cost_partial_reason", None)
    return out
