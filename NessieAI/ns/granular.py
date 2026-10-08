"""Native dispatch for the granular assistant ops.

Port of the dmac sidecar's ``sidecar/app/ops.py``: each op calls the same
chat_nextseek portable function, with the same argument order, so behavior is
preserved and the dmac sidecar can be rewired to call these endpoints
mechanically. chat_nextseek imports are lazy (deferred to call time) so the
viewset module stays import-light and unit tests can patch the agents.

The single intentional **superset** of dmac behavior is ``graph``: per the design
decision for this work it ALSO executes the Cypher plan via Neo4j and returns the
rows alongside the plan (dmac returns the plan only). The Neo4j tool holds that
statement to the caller's project scope, which the view puts on the config. A
statement refused for its scope is answered through graph_search, the
project-scoped sample search, exactly as the NS orchestrator falls back
(``_fall_back_to_graph_search``): the parser plan retargeted to graph_search, built
by the API agent, gated as a read and run, returned under ``fallback``. That chain is
``run_graph_question``, which the ``aggregate`` op (``aggregate.py``) runs once per part.

Error taxonomy (nextseek_api/assistant/op_errors.py):
* :class:`OpValidationError` -> VALIDATION, naming the field
* :class:`~NessieAI.ns.write_gate.WriteBlockedError` -> WRITE_BLOCKED
* :class:`OpBusyError` -> BUSY
* :class:`OpDeadlineError` -> AGENT_FAILED, reason ``deadline``
Any other exception raised by an agent maps to AGENT_FAILED at the viewset layer, with a closed reason.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from NessieAI.ns.write_gate import WriteBlockedError  # noqa: F401 (re-exported)

logger = logging.getLogger(__name__)

#: One line per op, on the 'dmac' logger tree, which dmac/settings.py writes to /app/logs/nextseek.log.
_TIMING_LOG = logging.getLogger("dmac.op_timing")


def _log_timing(op: str, turn: Any, limit: float, marks: list) -> None:
    """Seconds from the first mark (the view taking the request) to each named step: where an op's time went.
    Never raises: a logging fault must not cost the op's answer."""
    try:
        if not marks:
            return
        ordered = sorted(marks, key=lambda m: m[1])
        t0 = ordered[0][1]
        steps = " ".join(f"{name}={t - t0:.2f}" for name, t in ordered)
        _TIMING_LOG.info("op_timing op=%s turn=%s limit=%.1f %s", op, getattr(turn, "pk", None), limit, steps)
    except Exception:  # noqa: BLE001
        pass


class OpValidationError(ValueError):
    """Bad or missing op arguments. Maps to the VALIDATION error code.

    ``field`` and ``error_type`` are what the reply says (nextseek_api/assistant/op_errors.py): the field's name and
    what was wrong with it, never the value. The message may quote the value; it goes to the server log only.
    """

    def __init__(self, message: str, *, field: str = "args", error_type: str = "invalid") -> None:
        super().__init__(message)
        self.field = field
        self.error_type = error_type

    def field_error(self) -> dict:
        return {"field": self.field, "type": self.error_type}


class OpBusyError(RuntimeError):
    """The turn already has ``turn_memory.MAX_OPS_IN_FLIGHT`` ops running. Maps to the BUSY error code."""


class OpDeadlineError(RuntimeError):
    """The op ran out of its time (before a call could start, or while it ran). AGENT_FAILED, reason ``deadline``."""


@dataclass(frozen=True)
class LatePart:
    """Work an op started that was still running when the op answered (an aggregate part, or its vocabulary step),
    with the cost collector it records into (its own, never the op's)."""
    future: Any
    spend: Any


#: The late parts of the op running in this context: run_op sets a fresh list around its handler.
_LATE_PARTS: contextvars.ContextVar[list | None] = contextvars.ContextVar("nextseek_op_late_parts", default=None)


def hand_over_late(future: Any, spend: Any) -> bool:
    """Called by a handler, in the op's own thread, for work still running when it answers. True when run_op takes it
    over: under a turn it settles the part's spend and failed models into the turn when the part finishes, exactly
    once, and keeps the op's slot until the last such part is in. False outside run_op (nothing settles it)."""
    late = _LATE_PARTS.get()
    if late is None:
        return False
    late.append(LatePart(future, spend))
    return True


def _dump(obj: Any) -> Any:
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _load_parser_plan(args: dict) -> Any:
    """Parse ``args['parser_plan']`` as JSON; malformed input -> OpValidationError
    (mirrors the dmac runner's VALIDATION/exit-3 parity for a bad --parser-plan)."""
    try:
        return json.loads(args["parser_plan"])
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise OpValidationError(f"parser_plan is not valid JSON: {exc}", field="parser_plan", error_type="invalid_json") from exc


#: Caps on what an agent-supplied plan may hold (T1): a plan is data, never Cypher, scope or an endpoint.
PLAN_MAX_ITEMS = 20
PLAN_MAX_ITEM_CHARS = 200
PLAN_MAX_TEXT_CHARS = 1000


_Short = Annotated[str, StringConstraints(max_length=PLAN_MAX_ITEM_CHARS)]


def _items() -> Any:
    return Field(default_factory=list, max_length=PLAN_MAX_ITEMS)


class AgentFilters(BaseModel):
    sampletype_code: Optional[_Short] = None
    assay_codes: list[_Short] = _items()
    keywords: list[_Short] = _items()
    uids: list[_Short] = _items()
    lab_codes: list[_Short] = _items()
    model_config = ConfigDict(extra="forbid")


class AgentPlan(BaseModel):
    """What the CC agent may send as ``--plan``. Anything else (mode, resolved, cypher, projects, target_endpoint,
    ...) is refused; Django sets mode and resolved."""
    intent_summary: str = Field("", max_length=PLAN_MAX_TEXT_CHARS)
    filters: AgentFilters = Field(default_factory=AgentFilters)
    notes: str = Field("", max_length=PLAN_MAX_TEXT_CHARS)
    model_config = ConfigDict(extra="forbid")


def _validate_agent(model, data: Any, field: str):
    """``model`` of ``data``; a failure is an OpValidationError naming the first bad field, never repaired by a parse."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join([field, *(str(part) for part in err["loc"])])
        raise OpValidationError(f"{loc}: {err['msg']}", field=loc, error_type=err["type"]) from exc


def parse_agent_plan(raw: Any, field: str = "plan"):
    """The agent's ``--plan`` as an ``AgentPlan``, or None when it sent none. Bad JSON, an unknown field, a wrong
    type or an over-cap value raises OpValidationError (VALIDATION) naming the field; no model is called."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise OpValidationError("plan must be JSON text", field=field, error_type="must_be_json_text")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise OpValidationError(f"plan is not valid JSON: {exc}", field=field, error_type="invalid_json") from exc
    return _validate_agent(AgentPlan, data, field)


def parse_agent_filters(data: Any, field: str):
    """A part's own ``filters`` object (aggregate ``--parts``), validated like the plan's."""
    return _validate_agent(AgentFilters, data, field)


def _vocabulary_model(entity_out: Any):
    from chat_nextseek.schemas import EntityAgentOutput
    if isinstance(entity_out, EntityAgentOutput):
        return entity_out
    return EntityAgentOutput.model_validate(_dump(entity_out))


def plan_filters(filters: Any, resolved: Any, field: str):
    """``filters`` as the parser's filters. A lab code the vocabulary did not match is refused, never dropped."""
    from chat_nextseek.schemas import ParserFilters
    known = set(resolved.lab_codes)
    bad = [code for code in filters.lab_codes if code not in known]
    if bad:
        raise OpValidationError(f"lab code {bad[0]!r} is not in the turn's vocabulary", field=f"{field}.lab_codes",
                                error_type="unknown_lab_code")
    return ParserFilters(**filters.model_dump())


def parser_plan_from(agent_plan: Any, entity_out: Any):
    """The ParserPlan an agent plan stands for: Django sets ``mode`` and ``resolved`` (the turn's vocabulary); the
    agent's ``intent_summary``, ``filters`` and ``notes`` are data."""
    from chat_nextseek.schemas import ParserPlan
    resolved = _vocabulary_model(entity_out)
    return ParserPlan(mode="graph_query", intent_summary=agent_plan.intent_summary, notes=agent_plan.notes,
                      filters=plan_filters(agent_plan.filters, resolved, "plan.filters"), resolved=resolved)


def op_plan(turn: Any, agent_plan: Any, entity_out: Any, parse: Callable[[], Any]) -> tuple[Any, str]:
    """``(the plan this op runs, its source)``. The agent's plan when it sent one, else the turn's, else one fallback
    parse (``parse()``). Under a turn the plan an op ran becomes the turn's, except a turn plan reused as it is."""
    from chat_nextseek.schemas import ParserPlan
    from NessieAI.ns import turn_memory
    if agent_plan is not None:
        plan, source = parser_plan_from(agent_plan, entity_out), "agent"
    else:
        plan, source = None, "turn"
        stored = turn_memory.get_turn_plan(turn) if turn is not None else None
        if stored is not None:
            try:
                plan = ParserPlan.model_validate(stored)
            except Exception:  # noqa: BLE001 - parse again
                logger.warning("the turn's stored plan did not load; parsing again", exc_info=True)
        if plan is None:
            plan, source = parse(), "parser"
    if turn is not None and source != "turn":
        dumped = _plan_json(plan)
        if isinstance(dumped, dict):
            turn_memory.set_turn_plan(turn, dumped)
    return plan, source


def run_op(
    op: str,
    args: dict,
    *,
    config: Any,
    session: Any,
    write_gate: Callable,
    neo4j_exec: Callable | None = None,
    outputs_dir: str | None = None,
    limit_s: float | None = None,
    turn: Any = None,
    timing: list | None = None,
) -> dict:
    """Dispatch a granular op to its handler and return its result dict.

    ``timing``: the view's own marks, (name, monotonic seconds); the op logs them with its own (dmac.op_timing).

    The op runs inside its own ``call_scope`` (chat_nextseek) with ``limit_s``, the op's limit for this request
    (``NessieAI/ns/op_limits.py``; the view passes it, None means the table's value). A model that failed in one of the
    op's agent calls is not asked again by the next one, and every model call, Neo4j statement and REST call inside the
    op is cut to fit the limit (operator rulings 2026-09-28). ``turn`` is the request's ``CCTurn`` under a turn pass,
    else None; the handlers get both. Under a turn pass the op takes one of the turn's two slots first
    (turn_memory.py) and gives it back in a finally; with both taken it raises OpBusyError and the handler never runs.

    Plan 04: the op runs in its own cost collector (turn_spend). Under a turn the scope starts with the turn's failed
    models, the op's new ones and its spend are put back into the turn (_settle_turn) before the slot is given back,
    and the vocabulary and parser plans are the turn's (_vocabulary_of, _turn_plan). Work the handler hands over as
    still running (hand_over_late) settles into the turn as each piece finishes, and the last one gives the slot
    back (_settle_late); with nothing late the slot is given back here, as before. A late part's wait is inside the
    op's call scope, so it ends by the op's limit; a wait outside the scope (none known) would hold the slot longer.
    """
    handler = _HANDLERS.get(op)
    if handler is None:
        raise OpValidationError(f"not a sidecar op: {op!r}", field="op", error_type="unknown_op")
    from chat_nextseek import call_scope, turn_spend
    from NessieAI.ns.op_limits import op_limit_s
    limit = op_limit_s(op, None, time.time()) if limit_s is None else float(limit_s)
    marks = list(timing or [])
    slot = False
    if turn is not None:
        from NessieAI.ns.turn_memory import take_op_slot
        if not take_op_slot(turn):
            raise OpBusyError("this turn already has two ops running")
        slot = True
        marks.append(("slot", time.monotonic()))
    late: list[LatePart] = []
    late_token = _LATE_PARTS.set(late)
    try:
        with turn_spend.collecting() as spend, call_scope.scope(deadline_s=limit, op=True) as scope:
            scope.marks.extend(marks)
            scope.mark("scope")
            if turn is not None:
                from NessieAI.ns import turn_memory
                scope.seed(turn_memory.load_strikes(turn))
            try:
                return handler(args, config, session, write_gate, neo4j_exec, outputs_dir, limit_s=limit, turn=turn)
            finally:
                scope.mark("end")
                _log_timing(op, turn, limit, scope.marks)
                if turn is not None:
                    _settle_turn(turn, scope, spend)
                    # Parts the op answered without are still running (aggregate at its deadline): the slot stays
                    # taken until the last of them has settled its own spend and failed models.
                    if late and _settle_late(turn, scope, late):
                        slot = False
    finally:
        _LATE_PARTS.reset(late_token)
        if slot:
            from NessieAI.ns.turn_memory import release_op_slot
            release_op_slot(turn)


def _settle_turn(turn: Any, scope: Any, spend: Any) -> None:
    """Put what this op (or one late part of it) learned into its turn: the models that failed, and what its model
    calls cost. Two independent writes: one failing never stops the other, and either failing marks the turn's cost
    partial, so a lost write never leaves a confident complete cost. Never raises: the op's answer goes back either
    way."""
    from NessieAI.ns import turn_memory
    lost = False
    try:
        turn_memory.merge_strikes(turn, scope.strikes())
    except Exception:  # noqa: BLE001
        lost = True
        logger.warning("could not record an op's failed models in its turn", exc_info=True)
    try:
        record = spend.summary()
        total = record.get("total_cost_usd")
        turn_memory.add_spend(turn, float(total or 0.0),
                              partial=lost or bool(record.get("cost_partial")) or total is None,
                              estimated=bool(record.get("cost_estimated")))
    except Exception:  # noqa: BLE001
        logger.warning("could not record an op's spend in its turn; marking its cost partial", exc_info=True)
        try:
            turn_memory.mark_cost_partial(turn)
        except Exception:  # noqa: BLE001
            logger.error("could not mark the turn's cost partial after a lost write", exc_info=True)


class _LateSettlement:
    """An op's late parts: each settles its own spend and the op scope's failed models into the turn when it finishes
    (a merge is a union, so the scope's other strikes change nothing), and the last one gives the op's slot back."""

    def __init__(self, turn: Any, scope: Any, count: int) -> None:
        self._turn, self._scope = turn, scope
        self._left = count
        self._lock = threading.Lock()
        self._owner = threading.get_ident()

    def part_done(self, part: LatePart) -> None:
        from django.db import connection
        from NessieAI.ns.turn_memory import release_op_slot
        try:
            _settle_turn(self._turn, self._scope, part.spend)
        finally:
            with self._lock:
                self._left -= 1
                last = self._left == 0
            if last:
                release_op_slot(self._turn)
            if threading.get_ident() != self._owner:
                connection.close()  # the part's pool thread opened a connection of its own for these writes


def _settle_late(turn: Any, scope: Any, late: list[LatePart]) -> bool:
    """Settle each late part when it finishes (a done-callback, called exactly once per future; at once, in this
    thread, for one that finished meanwhile). True: the slot is now given back by the last part, not by run_op."""
    settlement = _LateSettlement(turn, scope, len(late))
    for part in late:
        part.future.add_done_callback(lambda _future, part=part: settlement.part_done(part))
    return True


def _plan_json(plan: Any) -> Any:
    return plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan


def _store_turn_vocabulary(turn: Any, out: Any) -> Any:
    """Store ``out`` as the turn's vocabulary when it has none; when another op stored one first, use that one, so
    every op of the turn reads the same terms."""
    from chat_nextseek.schemas import EntityAgentOutput
    from NessieAI.ns import turn_memory
    if turn_memory.store_vocabulary(turn, out.model_dump(mode="json")):
        return out
    stored = turn_memory.get_vocabulary(turn)
    return EntityAgentOutput.model_validate(stored) if stored is not None else out


def _vocabulary_of(turn: Any, config: Any) -> Any:
    """The turn's vocabulary: the stored one, else resolved now on the user's own question and stored."""
    from chat_nextseek.schemas import EntityAgentOutput
    from chat_nextseek.vocabulary import resolve_vocabulary
    from NessieAI.ns import turn_memory
    stored = turn_memory.get_vocabulary(turn)
    if stored is not None:
        return EntityAgentOutput.model_validate(stored)
    turn_memory.count_vocabulary_resolution(turn)  # the turn's end reports resolutions after the first as duplicates
    return _store_turn_vocabulary(turn, resolve_vocabulary(None, config, turn_memory.user_question(turn)))


def _turn_plan(turn: Any, question: str, make: Callable[[], Any]) -> Any:
    """The parser plan for ``question`` in this turn: one an earlier op stored, else ``make()``'s, stored for the
    next op. A reused plan runs through the same write gate and scope as a new one."""
    from chat_nextseek.schemas import ParserPlan
    from NessieAI.ns import turn_memory
    stored = turn_memory.get_plan(turn, question)
    if stored is not None:
        try:
            return ParserPlan.model_validate(stored)
        except Exception:  # noqa: BLE001 - make it again
            logger.warning("a stored parser plan did not load; parsing again", exc_info=True)
    plan = make()
    dumped = _plan_json(plan)
    if isinstance(dumped, dict):
        turn_memory.store_plan(turn, question, dumped)
    return plan


def _entity(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    from chat_nextseek.portable import entity_agent
    if turn is not None:
        from NessieAI.ns import turn_memory
        if turn_memory.normalize_question(args["query"]) == turn_memory.normalize_question(
                turn_memory.user_question(turn)):
            return _dump(_vocabulary_of(turn, config))
    return _dump(entity_agent(config, args["query"]))


def _parse(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    from chat_nextseek.portable import entity_agent, parser_agent
    if turn is None:
        entity_out = entity_agent(config, args["query"])
        return _dump(parser_agent(session, config, args["query"], entity_out))
    entity_out = _vocabulary_of(turn, config)
    return _dump(_turn_plan(turn, args["query"], lambda: parser_agent(session, config, args["query"], entity_out)))


#: The project-scoped sample search a refused graph question is answered through.
GRAPH_SEARCH_ENDPOINT = "/nextseek_api/samples/graph_search/"

#: Added to the error of a ``graph`` op result refused for its project scope, by whether its fallback answered.
GRAPH_SCOPE_FALLBACK_HINT = (
    f"The op asked {GRAPH_SEARCH_ENDPOINT} instead, which applies the caller's project scope on the server; "
    "its answer is under fallback."
)
GRAPH_SCOPE_FALLBACK_RETRY_HINT = (
    f"The op's {GRAPH_SEARCH_ENDPOINT} fallback did not answer (fallback.error); nextseek-api-read with "
    "fallback.parser_plan asks it again."
)

#: What an inline graph_search fallback needs of the op's limit: an API-agent model call and a graph_search request.
#: It runs inline only when the refusal came at least this long before the op's limit (60 s into a 90 s graph op, 25 s
#: into a 55 s one on the sidecar road); later, the op hands back the retargeted plan and the agent runs it with
#: nextseek-api-read.
GRAPH_FALLBACK_RESERVE_S = 30.0
_monotonic = time.monotonic


def fallback_start_budget_s(limit_s: float | None) -> float:
    """Seconds into a graph op by which a scope-refused question may still run its fallback inline."""
    from NessieAI.ns.op_limits import OP_LIMITS_S
    limit = OP_LIMITS_S["graph"] if limit_s is None else limit_s
    return max(0.0, limit - GRAPH_FALLBACK_RESERVE_S)

#: What the CC agent must tell the user when it answers from ``fallback`` (the NS chatter gets the same note).
GRAPH_SCOPE_FALLBACK_NOTE = (
    "The graph query written for this question could not be confirmed to stay within the user's projects, so it "
    "was not run. This answer comes from the project-scoped sample search instead. Say so, and say which "
    "conditions of the question that search could not apply."
)


def _graph_search_fallback(config, parser_plan, refused: dict, write_gate, elapsed_s: float, *,
                           budget_s: float | None = None) -> dict:
    """Answer a scope-refused graph question through graph_search, as the NS orchestrator does.

    Never raises: a fallback that cannot run reports why, and the refusal it answers stays in the op's ``result``.
    ``parser_plan`` is always the parser's plan retargeted to graph_search, as JSON, so the agent can ask it
    through nextseek-api-read when the fallback did not answer here. It runs here only when the op is still inside
    ``budget_s`` (by default ``fallback_start_budget_s`` of the table's graph limit; the ops pass their own). ``ok`` is
    graph_search's own answer: an error status is a failed fallback.
    Only graph_search is ever called here, and only through the read gate.
    """
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request

    scope = refused.get("scope") if isinstance(refused.get("scope"), dict) else {}
    retarget = {"mode": "new_search", "target_endpoint": GRAPH_SEARCH_ENDPOINT}
    if hasattr(parser_plan, "model_copy"):
        plan = parser_plan.model_copy(update=retarget)
    elif isinstance(parser_plan, dict):
        plan = {**parser_plan, **retarget}
    else:
        plan = retarget
    plan_json = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else json.loads(json.dumps(plan, default=str))
    out: dict[str, Any] = {
        "ok": False, "ran": False, "endpoint": GRAPH_SEARCH_ENDPOINT, "note": GRAPH_SCOPE_FALLBACK_NOTE,
        "codes": list(scope.get("codes") or ()), "reasons": list(scope.get("reasons") or ()),
        "parser_plan": plan_json,
    }
    if elapsed_s > (fallback_start_budget_s(None) if budget_s is None else budget_s):
        out["error"] = (
            f"not run here: the op had already used {elapsed_s:.0f} s of its time; run nextseek-api-read with "
            "fallback.parser_plan to ask graph_search"
        )
        return out
    out["ran"] = True
    try:
        api_plan = api_agent_build_request(config, plan)
        endpoint, method = api_plan.endpoint, (api_plan.method or "").upper()
        out["api_plan"] = _dump(api_plan)
        if endpoint != GRAPH_SEARCH_ENDPOINT:
            out["error"] = f"the API agent built a request for {endpoint!r}, not {GRAPH_SEARCH_ENDPOINT}; nothing ran"
            return out
        write_gate("api-read", endpoint, method, False)
        response = helpers.tool_nextseek_api_request(
            config, endpoint, method, requestBody=api_plan.requestBody, queryParameters=api_plan.queryParameters,
        )
    except Exception as exc:  # the refusal is still the op's answer; say why its fallback did not run
        out["error"] = f"graph_search fallback failed: {type(exc).__name__}: {exc}"
        return out
    response = response if isinstance(response, dict) else {}
    out.update(method=method, status_code=response.get("status_code"))
    if response.get("ok"):
        out.update(ok=True, data=response.get("data"))
    else:
        detail = response.get("error") or response.get("data")
        out["error"] = f"graph_search answered {response.get('status_code')}: {str(detail)[:500]}"
    return out


@dataclass
class GraphAnswer:
    """What one pass of the graph op's chain produced.

    ``plan`` is the dumped graph agent plan whose statement ``result`` answers, ``cypher`` the statement that was
    submitted for it (after ``prepare_cypher``), ``fallback`` the graph_search answer to a scope refusal (None when
    there was none), ``attempts`` one record per statement run, and ``retry_changed_answer`` whether a zero-row
    retry replaced a first query that matched nothing.
    """

    plan: Any
    result: dict
    fallback: dict | None
    parser_plan: Any
    cypher: str | None
    attempts: list = field(default_factory=list)
    retry_changed_answer: bool = False
    plan_source: str = "parser"


def _attempt(reason: str, result: dict) -> dict:
    """One statement's record: why it ran, whether it answered, and how many rows or which refusal codes."""
    out: dict[str, Any] = {"reason": reason, "ok": bool(result.get("ok"))}
    if result.get("ok"):
        out["rows"] = result.get("count", len(result.get("data") or []))
    scope = result.get("scope")
    if isinstance(scope, dict) and scope.get("codes"):
        out["codes"] = list(scope["codes"])
    return out


def _run_plan(plan, exec_fn, config, prepare_cypher) -> tuple[Any, str | None, str | None, dict]:
    """``(plan dump, the agent's cypher, the submitted cypher, result)`` for one graph agent plan."""
    plan_dump = _dump(plan)
    cypher = plan_dump.get("cypher") if isinstance(plan_dump, dict) else getattr(plan, "cypher", None)
    params = (
        plan_dump.get("parameters") if isinstance(plan_dump, dict) else getattr(plan, "parameters", {})
    ) or {}
    if not cypher:
        return plan_dump, cypher, cypher, {"ok": False, "error": "graph agent produced no cypher", "data": []}
    submitted = prepare_cypher(cypher, params) if prepare_cypher is not None else cypher
    return plan_dump, cypher, submitted, exec_fn(config, submitted, params)


def run_graph_question(
    query: str,
    *,
    config: Any,
    session: Any,
    write_gate: Callable,
    neo4j_exec: Callable | None = None,
    entity_out: Any = None,
    refine_context: str | None = None,
    prepare_cypher: Callable[[str, dict], str] | None = None,
    retry: Callable[[dict, str], "tuple[str, str] | None"] | None = None,
    started: float | None = None,
    clock: Callable[[], float] | None = None,
    fallback_budget_s: float | None = None,
    parser_plan: Any = None,
    turn: Any = None,
    uid_checks: list | None = None,
    agent_plan: Any = None,
    plan_source: str | None = None,
) -> GraphAnswer:
    """The graph op's chain: parser, graph agent, the Neo4j tool, and graph_search on a scope refusal.

    ``_graph`` passes its UID check (round 6): the note as ``refine_context`` and the checks as ``uid_checks``, which
    write the parser plan's filters.uids as the graph stores them, as the NS path does. The aggregate op calls it
    once per part and passes: ``entity_out`` (resolved once for the whole question), ``refine_context`` (its brief,
    handed to every graph agent call), ``prepare_cypher`` (its row cap, applied to every statement before the tool sees
    it), ``retry`` (given the first result and the agent's own statement, it returns ``(reason, retry_context)``
    for at most one more statement, or None), and its own clock, start and fallback budget.

    Every statement, a retry's too, runs through ``neo4j_exec`` (the scoped ``tool_neo4j_query`` when None) on the
    ``config`` it was handed: nothing here builds or changes a scope. A retry is kept only when it answered and,
    after a first query that matched nothing, found something. Whatever answers last and was refused for its scope
    goes to ``_graph_search_fallback``.

    ``turn`` (a Container-CC turn) supplies the vocabulary and the turn's plan. The plan is, in order: ``parser_plan``
    when handed one (the aggregate op makes one for all its parts; ``plan_source`` says where it came from), else the
    agent's own (``agent_plan``, an ``AgentPlan``), else the turn's, else one parser call (``op_plan``).
    """
    from chat_nextseek import call_scope
    from chat_nextseek.portable import entity_agent, graph_agent, parser_agent
    now = clock or _monotonic
    if started is None:
        started = now()
    if entity_out is None:
        entity_out = _vocabulary_of(turn, config) if turn is not None else entity_agent(config, query)
    # Run the parser and pass its plan to graph_agent, mirroring the NS
    # orchestrator (orchestrator.py:869 graph_agent(config, query, entity, plan)).
    # Without the parser_plan the graph agent gets no PARSER PLAN block and emits
    # unbounded, pathological Cypher that overruns the 60s proxy timeout (#20).
    if parser_plan is None:
        parser_plan, plan_source = op_plan(turn, agent_plan, entity_out,
                                           lambda: parser_agent(session, config, query, entity_out))
    if uid_checks:
        from chat_nextseek.helpers.uid_check import plan_with_stored_uids
        parser_plan = plan_with_stored_uids(parser_plan, uid_checks)  # R4, as the NS path: filters.uids as stored
    call_scope.mark("parser_end")
    brief = {"refine_context": refine_context} if refine_context else {}
    call_scope.mark("graph_start")
    plan = graph_agent(config, query, entity_out, parser_plan, **brief)
    exec_fn = neo4j_exec
    if exec_fn is None:
        from chat_nextseek.helpers import tool_neo4j_query
        exec_fn = tool_neo4j_query
    plan_dump, own_cypher, cypher, result = _run_plan(plan, exec_fn, config, prepare_cypher)
    attempts = [_attempt("initial", result)]
    changed = False
    spec = retry(result, own_cypher) if retry is not None and own_cypher else None
    if spec:
        from chat_nextseek.helpers.tools.neo4j import matched_nothing
        reason, retry_context = spec
        again = graph_agent(config, query, entity_out, parser_plan, retry_context=retry_context, **brief)
        again_dump, _, again_cypher, again_result = _run_plan(again, exec_fn, config, prepare_cypher)
        keep = bool(again_result.get("ok")) and not (reason == "zero_rows" and matched_nothing(again_result))
        attempts.append({**_attempt(reason, again_result), "kept": keep})
        if keep:
            changed = matched_nothing(result)
            plan_dump, cypher, result = again_dump, again_cypher, again_result
    from chat_nextseek.helpers.tools.neo4j import is_scope_refusal
    if is_scope_refusal(result):
        fallback = _graph_search_fallback(config, parser_plan, result, write_gate, now() - started,
                                          budget_s=fallback_budget_s)
        hint = GRAPH_SCOPE_FALLBACK_HINT if fallback["ok"] else GRAPH_SCOPE_FALLBACK_RETRY_HINT
        result = {**result, "error": f"{result.get('error') or ''} {hint}".strip()}
        return GraphAnswer(plan_dump, result, fallback, parser_plan, cypher, attempts, changed, plan_source or "parser")
    return GraphAnswer(plan_dump, result, None, parser_plan, cypher, attempts, changed, plan_source or "parser")


def _uid_check(config, query, neo4j_exec):
    """``(note for the graph agent, notes for the reply, the checks)``: the check the aggregate runs in its prelude.
    Nothing when the question names no UID or the check fails (a failed check claims nothing either way)."""
    from chat_nextseek.helpers.uid_check import check_uids, uid_notes, uids_in
    try:
        uids = uids_in(query)
        if not uids:
            return None, [], None
        exec_fn = neo4j_exec
        if exec_fn is None:
            from chat_nextseek.helpers import tool_neo4j_query as exec_fn
        checks = check_uids(config, uids, run=exec_fn)
        return (*uid_notes(checks), checks)
    except Exception:
        return None, [], None


def _graph(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    # Round 6: a UID typed without (or with) -PUB reaches the graph agent under the spelling the graph stores, in
    # the note and in the parser plan's filters, and the reply says so (``notes``).
    agent_plan = parse_agent_plan(args.get("plan"))  # before any call: a bad plan is VALIDATION, never repaired
    agent_note, reply_notes, checks = _uid_check(config, args["query"], neo4j_exec)
    answer = run_graph_question(args["query"], config=config, session=session, write_gate=write_gate,
                                neo4j_exec=neo4j_exec, refine_context=agent_note, uid_checks=checks,
                                fallback_budget_s=fallback_start_budget_s(limit_s), turn=turn, agent_plan=agent_plan)
    # parser_plan: the plan this answer ran, as JSON, so nextseek-api-read can ask the same question of the REST API.
    out = {"plan": answer.plan, "result": answer.result, "parser_plan": _plan_json(answer.parser_plan),
           "plan_source": answer.plan_source}
    if answer.fallback is not None:
        out["fallback"] = answer.fallback
    if reply_notes:
        out["notes"] = reply_notes
    return out


def _aggregate(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Counts and breakdowns in one call, one to four parts run in parallel on the server (``aggregate.py``)."""
    from NessieAI.ns.aggregate import run_aggregate
    return run_aggregate(args, config=config, session=session, write_gate=write_gate, neo4j_exec=neo4j_exec,
                         limit_s=limit_s, turn=turn)


def _graph_schema(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """The deployed graph's schema, read live, with no model call and no Cypher.

    The op that replaces the graph snapshot the cc-agent image used to bake: the agent
    asks NExtSEEK, which reads the live catalog through ``graph_catalog``, so a catalog
    change no longer needs an image rebuild. ``types`` arrives as a comma-separated
    string over the wire (one shim flag) and is split here; ``query`` only gates the
    vocabulary blocks.
    """
    from chat_nextseek.portable import graph_schema_snapshot
    types = [code.strip() for code in str(args.get("types") or "").split(",") if code.strip()]
    return graph_schema_snapshot(config, types=types, question=args.get("query") or "")


def _api_read(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request
    plan = api_agent_build_request(config, _load_parser_plan(args))
    endpoint, method = plan.endpoint, (plan.method or "").upper()
    write_gate("api-read", endpoint, method, False)  # raises WriteBlocked if not read-safe
    result = helpers.tool_nextseek_api_request(
        config, endpoint, method, requestBody=plan.requestBody, queryParameters=plan.queryParameters
    )
    return {"endpoint": endpoint, "method": method, "api_plan": _dump(plan), "response": result}


def _api_write(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request
    confirmed = args.get("confirmed_write", False)
    write_gate("api-write", None, None, confirmed)  # raises WriteBlocked unless confirmed is True
    plan = api_agent_build_request(config, _load_parser_plan(args))
    result = helpers.tool_nextseek_api_request(
        config, plan.endpoint, plan.method, requestBody=plan.requestBody,
        queryParameters=plan.queryParameters,
    )
    return {
        "endpoint": plan.endpoint, "method": (plan.method or "").upper(),
        "api_plan": _dump(plan), "response": result,
    }


def _report(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    from chat_nextseek import helpers
    from chat_nextseek.schemas.chat import ReporterPlan
    mode = args["mode"]
    summary_mode = "RPPR" if mode == "rppr" else mode
    rp = ReporterPlan(project=args["project"], reporter_mode="summary", summary_mode=summary_mode)
    log_dir = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    result, saved, summary = helpers.run_reporter_summary(config, rp, log_dir)
    return {"summary": summary, "saved_files": saved, "rows": result}


def _generate_submission(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    # Route through the SAME orchestration the NS run_query report_generation
    # path uses (generate_report_outputs), rather than calling the leaf
    # report_writer_agent directly. That gives the op, for every report type:
    #   * the type-specific template (load_report_template) -> bounded output
    #     (a template-less call free-forms and overruns the writer's output-token
    #     cap, truncating the JSON -> AGENT_FAILED);
    #   * the full reporter_context (metadata hydration, protocols, plans);
    #   * the emitters that persist the REAL submission workbooks under
    #     saved_files (geo_seq_workbooks / sra_* / pride_* / nfcore_* / ...),
    #     which the bundle/download + CC staging then serve.
    # See GitHub issue #21 (reporter port defect / drift).
    from chat_nextseek.portable import generate_report_outputs, report_writer_agent
    from chat_nextseek.schemas.chat import ReporterPlan

    uids = [u.strip() for u in args["uids"].split(",") if u.strip()]
    report_type = args["type"]
    # A non-empty user query is required: some providers (Bedrock/Opus Converse)
    # reject a blank message content block. Fall back to a type-aware default when
    # the caller supplies no query, so the op is robust to query=None / "".
    user_query = (args.get("query") or "").strip() or (
        f"Generate a {report_type} submission report for the provided sample UIDs."
    )
    reporter_plan = ReporterPlan(
        report_type=report_type,
        uids=uids,
        reporter_mode="report_generation",
        reporter_context={"per_sample_reports": False},
    )
    log_dir = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    _reporter_result, report_writer_output, saved_files, _reply = generate_report_outputs(
        config=config,
        user_query=user_query,
        parser_plan={"report_type": report_type},
        reporter_plan=reporter_plan,
        uids=uids,
        log_dir=log_dir,
        report_writer_fn=report_writer_agent,
        per_sample_reports=False,
    )
    # Combined mode wraps the writer output as {"all_samples": <writer output>}.
    # Unwrap to the flat writer dict to preserve the op's existing result shape,
    # and attach the real saved_files so the download bundle + CC staging serve
    # the actual generated report file.
    flat = report_writer_output
    if isinstance(report_writer_output, dict) and "all_samples" in report_writer_output:
        flat = report_writer_output["all_samples"]
    result = dict(flat) if isinstance(flat, dict) else {"report_type": report_type, "report": flat}
    result["saved_files"] = saved_files or {}
    return result



_RUN_LS_CAP = 2_000_000  # bytes of `ls -laR` returned to CC before truncation (well under the 16 MiB WS cap)


def _run_ls(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Read-only recursive listing of a finished Luria run dir (reingest input).

    Validates ``run_dir`` is under ``<LURIA working_path>/runs`` (no traversal),
    then SSHes Luria and runs ``ls -laR``. Returns the tree text (capped). Never
    writes to Luria.
    """
    import shlex
    luria_env = getattr(config, "LURIA_ENV", None) or {}
    working_path = str(luria_env.get("working_path") or "").rstrip("/")
    if not working_path or not luria_env.get("key"):
        # NExtSEEK's own configuration, not the caller's argument: AGENT_FAILED internal (operator ruling 2026-10-02).
        raise RuntimeError("Luria is not configured (LURIA_ENV incomplete)")
    runs_root = working_path + "/runs"
    run_dir = os.path.normpath(str(args["run_dir"]))
    if run_dir != runs_root and not run_dir.startswith(runs_root + "/"):
        raise OpValidationError(f"run_dir must be under {runs_root}", field="run_dir", error_type="outside_the_runs_root")
    from chat_nextseek import call_scope
    from chat_nextseek.luria.ssh import SshTimeout, prepare_key, ssh_run
    from NessieAI.ns.op_limits import OP_LIMITS_S
    left = call_scope.time_left_for(OP_LIMITS_S["run-ls"] if limit_s is None else float(limit_s))
    if left is None:
        raise OpDeadlineError("the op ran out of time before the Luria listing could start")
    key_path = prepare_key(luria_env["key"])
    try:
        out = ssh_run(luria_env, f"ls -laR {shlex.quote(run_dir)}", key_path=key_path, timeout=left)
    except SshTimeout as exc:
        raise OpDeadlineError(str(exc)) from exc
    return {"run_dir": run_dir, "truncated": len(out) > _RUN_LS_CAP, "tree": out[:_RUN_LS_CAP]}


def _build_upload_xlsx(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Render one 4-sheet upload workbook per A.* sample type from CC-composed rows.

    args["rows"]: JSON array of {"SampleType", "json_metadata", "assay_ids"}. Runs QA
    per type (a HARD_REJECT type is skipped, its report returned). Returns the rendered
    workbooks under ``saved_files`` plus the per-type QA reports. No NExtSEEK write —
    the user reviews the workbook(s) and uploads them via the batch-upload UI.
    """
    from NessieAI.ns.reingest_qa import HARD_REJECT, qa_rows
    from NessieAI.ns.upload_workbook import render_upload_workbook

    try:
        rows = json.loads(args["rows"])
    except ValueError as exc:
        raise OpValidationError(f"rows is not valid JSON: {exc}", field="rows", error_type="invalid_json") from exc
    if not isinstance(rows, list) or not rows:
        raise OpValidationError("rows must be a non-empty JSON array", field="rows", error_type="must_be_a_non_empty_json_array")

    existing = {u.strip() for u in str(args.get("existing_parent_uids") or "").split(",") if u.strip()}

    by_type: dict[str, list] = {}
    for row in rows:
        st = str((row or {}).get("SampleType") or "").strip()
        if not st:
            raise OpValidationError("every row needs a SampleType", field="rows", error_type="row_without_sampletype")
        by_type.setdefault(st, []).append(row)

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    known = set(by_type)  # permissive here; the real catalog validates on upload
    saved_files: dict[str, str] = {}
    qa: dict[str, dict] = {}
    for st, st_rows in by_type.items():
        report = qa_rows(st_rows, sample_type=st, known_sampletypes=known,
                         existing_parent_uids=existing)
        qa[st] = {"disposition": report.disposition, "hard": report.hard, "soft": report.soft}
        if report.disposition == HARD_REJECT:
            continue
        safe_name = st.replace("/", "_").replace(" ", "_")          # readable filename (keeps the dot)
        # The artifact KEY is the download URL segment, which the route only
        # accepts as [\w]+ — so it must be word-chars only (A.SCXP -> A_SCXP).
        # The file on disk keeps the dot; download serves it by its real name.
        safe_key = safe_name.replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}.xlsx")
        render_upload_workbook(st, st_rows, path)
        saved_files[f"reingest_{safe_key}"] = path
    return {"saved_files": saved_files, "qa": qa}

_HANDLERS: dict[str, Callable] = {
    "aggregate": _aggregate,
    "entity": _entity,
    "parse": _parse,
    "graph": _graph,
    "graph-schema": _graph_schema,
    "api-read": _api_read,
    "api-write": _api_write,
    "report": _report,
    "generate-submission": _generate_submission,
    "run-ls": _run_ls,
    "build-upload-xlsx": _build_upload_xlsx,
}
