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
# Reingest map notes, for administrators: dmac/settings.py routes this one logger to
# logs/django.log (nothing else under NessieAI has a handler).
_map_notes_log = logging.getLogger("NessieAI.ns.reingest.map_notes")

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


def _failed_parse(plan: Any) -> bool:
    """A parse that made no plan: parser_agent returns one with ``metadata.failure`` on a timeout or an error."""
    return bool((getattr(plan, "metadata", None) or {}).get("failure"))


def op_plan(turn: Any, agent_plan: Any, entity_out: Any, parse: Callable[[], Any]) -> tuple[Any, str]:
    """``(the plan this op runs, its source)``. The agent's plan when it sent one, else the turn's, else one fallback
    parse (``parse()``). Under a turn the plan an op ran becomes the turn's, except a turn plan reused as it is and a
    failed parse, which is never stored and, stored, counts as none."""
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
        if plan is None or _failed_parse(plan):
            plan, source = parse(), "parser"
    if turn is not None and source != "turn" and not _failed_parse(plan):
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
        from chat_nextseek.schemas.graph import no_cypher_error

        return plan_dump, cypher, cypher, {"ok": False, "error": no_cypher_error(plan_dump), "data": []}
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
        # The graph agent reads only the plan's resolved block: carry the entity step's house and category types into
        # it, as the NS clamp does (dtd3 S1). The lab codes are left as they are; this is not the lab-code clamp.
        from chat_nextseek.agents.entity import carry_code_owned_types
        parser_plan = carry_code_owned_types(parser_plan, entity_out)
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


def _uid_check(config, query, neo4j_exec, filter_uids=None):
    """``(note for the graph agent, notes for the reply, the checks)``: the check the aggregate runs in its prelude,
    over the question's UIDs and ``filter_uids`` (the agent plan's). Nothing when neither names a UID or the check
    fails (a failed check claims nothing either way)."""
    from chat_nextseek.helpers.uid_check import check_uids, uid_notes, uids_in
    try:
        uids = uids_in(query, filter_uids)
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
    agent_note, reply_notes, checks = _uid_check(config, args["query"], neo4j_exec,
                                                 agent_plan.filters.uids if agent_plan is not None else None)
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
    from chat_nextseek.agents.reporter import report_coder_agent
    from chat_nextseek.portable import generate_report_outputs, report_writer_agent
    from chat_nextseek.reports.outputs import ReportCodePathError
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
    try:
        # Above 10 samples report_coder writes the workbook from the metadata map, as on the NS road: the writer
        # alone overflowed its input on a large set (prod 10-14, 1,641 mice).
        _reporter_result, report_writer_output, saved_files, _reply = generate_report_outputs(
            config=config,
            user_query=user_query,
            parser_plan={"report_type": report_type},
            reporter_plan=reporter_plan,
            uids=uids,
            log_dir=log_dir,
            report_writer_fn=report_writer_agent,
            report_coder_fn=report_coder_agent,
            per_sample_reports=False,
        )
    except ReportCodePathError as failed:
        # The code path could not build it: no workbook, and the plain reply for the agent to relay.
        logger.info("generate-submission: code path failed: %s", failed)
        return {"report_type": report_type, "report": {}, "notes": failed.reply, "saved_files": {}}
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


def _launch_record_for(run_dir: str):
    """The PipelineRun Nessie recorded when it launched ``run_dir``, or None."""
    from NessieAI.ns.reingest import launch_record
    return launch_record.launch_record_for(run_dir)


def _validate_run_dir(args: dict, config: Any) -> tuple[str, dict, str]:
    """Shared by run-ls / run-harvest / run-checksum: the run dir must be
    under the cluster runs root. An unvalidated run_dir is an arbitrary read
    of the shared Luria account.

    This check is purely LEXICAL (``normpath`` + a string prefix test) --
    it says nothing about where ``run_dir`` actually resolves on the
    cluster. That is deliberate: this process has no filesystem access to
    Luria, only SSH, so the only place a symlink can be resolved is on the
    remote side. Callers that go on to read files under ``run_dir``
    (run-harvest's ``_STAGE_SCRIPT``, run-checksum's ``_CHECKSUM_SCRIPT``)
    MUST also confine against the returned ``runs_root``, resolved
    REMOTELY -- never against a resolve() of ``run_dir`` itself. See
    Important 1 of the 2026-09-16 whole-branch review: a symlink at
    ``<runs_root>/foo`` pointing to ``/home/someone/else`` passes this
    lexical check (the string still looks like a normal subpath) and then,
    if a remote script anchors its containment check on
    ``run_dir.resolve()``, RE-ANCHORS confinement to the symlink's target --
    every file under that target then trivially "resolves inside run_dir".
    Anchoring on ``runs_root.resolve()`` instead closes this: a file
    reached through the target directory no longer resolves inside the
    runs root, symlinked run_dir or not.
    """
    luria_env = getattr(config, "LURIA_ENV", None) or {}
    working_path = str(luria_env.get("working_path") or "").rstrip("/")
    if not working_path or not luria_env.get("key"):
        # NExtSEEK's own configuration, not the caller's argument: AGENT_FAILED internal (operator ruling 2026-10-02).
        raise RuntimeError("Luria is not configured (LURIA_ENV incomplete)")
    runs_root = working_path + "/runs"
    run_dir = os.path.normpath(str(args["run_dir"]))
    if run_dir != runs_root and not run_dir.startswith(runs_root + "/"):
        raise OpValidationError(f"run_dir must be under {runs_root}", field="run_dir", error_type="outside_the_runs_root")
    return run_dir, luria_env, runs_root


def _run_ls(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Read-only recursive listing of a finished Luria run dir (reingest input).

    Validates ``run_dir`` is under ``<LURIA working_path>/runs`` (no traversal),
    then SSHes Luria and runs ``ls -laR``. Returns the tree text (capped). Never
    writes to Luria.

    Unlike run-harvest/run-checksum, this op does NOT need the
    run_dir-is-itself-a-symlink guard (Important 1, 2026-09-16 review):
    ``ls -laR <run_dir>`` never dereferences a symlink named on its own
    command line, and ``-R`` does not follow symlinks it encounters while
    recursing either -- so a symlinked run_dir only ever lists as a single
    symlink entry, never its target's contents.
    """
    import shlex
    run_dir, luria_env, _runs_root = _validate_run_dir(args, config)
    from chat_nextseek import call_scope
    from chat_nextseek.luria.ssh import SshTimeout, luria_key, ssh_run
    from NessieAI.ns.op_limits import OP_LIMITS_S
    left = call_scope.time_left_for(OP_LIMITS_S["run-ls"] if limit_s is None else float(limit_s))
    if left is None:
        raise OpDeadlineError("the op ran out of time before the Luria listing could start")
    try:
        with luria_key(luria_env["key"]) as key_path:
            out = ssh_run(luria_env, f"ls -laR {shlex.quote(run_dir)}", key_path=key_path, timeout=left)
    except SshTimeout as exc:
        raise OpDeadlineError(str(exc)) from exc
    return {"run_dir": run_dir, "truncated": len(out) > _RUN_LS_CAP, "tree": out[:_RUN_LS_CAP]}


# The remote-side half of the run-harvest op's staging: matches GENERIC_GLOBS
# on the CLUSTER, using Python's own pathlib.Path.glob -- the same call
# harvest_local makes locally -- rather than the remote shell's globbing.
#
# GENERIC_GLOBS' MultiQC pattern ("multiqc*/**/*_data/multiqc_*.txt") is a
# multi-segment "**" pattern, which pathlib treats as zero-or-more
# intervening directories. A POSIX shell only matches "**" that way when the
# `globstar` option is explicitly turned on -- which a non-interactive
# `ssh host cmd` invocation does NOT do by default -- and even then a dash/sh
# login shell (a plausible remote default) does not support "**" at all.
# Confirmed directly: `ls -1d multiqc*/**/*_data/multiqc_*.txt` (no globstar)
# silently returns nothing -- no error, just an empty listing -- for a run
# whose MultiQC directory has zero or two intervening directories, while
# pathlib.Path.glob resolves both correctly. That is exactly the failure this
# staging method exists to avoid: every sample's `metrics`/`derived` would
# come back empty, with nothing to say why.
#
# The remote script tars every match into ONE stream piped back over a
# single ssh round trip, instead of one `ls` + one `cat` per file: a
# `cat`-per-file transfer also runs every file's bytes through this
# process's own text codec (ssh_run decodes stdout as text), which can alter
# a byte before harvest_local ever reads it.
#
# Two things the SSH account's confinement to `run_dir` depends on are
# enforced HERE, on the cluster side, because by the time bytes reach this
# process the read has already happened:
#
# 1. Symlinks. `run_dir` sits on a SHARED filesystem: the pipeline itself, or
#    anyone else with write access to it, can drop a symlink inside an
#    otherwise-valid run_dir that points anywhere on the cluster. Earlier,
#    this script called `path.is_file()` (which dereferences symlinks) and
#    opened the tar with `dereference=True`, on the reasoning that "a local
#    extract can never follow a link outside the staging directory." That
#    reasoning only protects the LOCAL extract step below -- the remote read
#    of the symlink's target has already happened on the cluster by then,
#    which is the side the shared account actually needs confining on. So
#    every match is now checked, before it is added to the tar, for (a) not
#    being a symlink itself and (b) resolving to a path still inside the
#    resolved run_dir (catches a symlinked ancestor directory too). Anything
#    that fails either check is skipped and reported in the trailing
#    `__nextseek_stage_report__.json` tar entry (parsed back out by
#    `_stage_run_dir`) rather than silently dropped -- see Important 1 of the
#    2026-09-16 review.
# 1b. Hardlinks. A follow-up adversarial pass on the same 2026-09-16 review
#    found the symlink checks above miss a hardlink: `os.link(outside, run_dir
#    / "x.csv")` makes `x.csv` a directory entry INSIDE run_dir that shares an
#    inode with a file elsewhere -- it is not a symlink (`is_symlink()` is
#    False) and has no separate target path to resolve away from
#    (`resolve()` returns itself), so neither existing check catches it and
#    the outside file's real content would be tarred and shipped. Every match
#    is now also checked for `st_nlink > 1` and skipped (reported the same
#    way) if so. This is deliberately blunt -- it also rejects a benign
#    multiply-linked file -- but GENERIC_GLOBS is small QC/metadata text
#    (params, versions, samplesheets, MultiQC/RSeQC tables), not the large
#    deduplicated BAM/FASTQ pipeline outputs where multi-link files actually
#    occur, so false positives here should be rare. Residual: on a filesystem
#    without `fs.protected_hardlinks`, an attacker who already has write
#    access to run_dir is the threat this closes -- it does not defend
#    against a write-capable adversary using some OTHER, still-unknown
#    mechanism to make a directory entry alias an outside inode.
# 1c. run_dir ITSELF as a symlink. `_validate_run_dir`'s check is lexical
#    only (see its docstring) -- it never asks the cluster whether run_dir
#    resolves anywhere. Earlier, this script anchored every containment
#    check on `run_dir.resolve()`: if run_dir were, say,
#    `<runs_root>/foo` -> `/home/someone/else` (a symlink, not a plain
#    directory), that resolve() call RELOCATES the anchor to
#    `/home/someone/else`, and every file glob-matched through it then
#    trivially "resolves inside run_dir" -- the confinement re-anchors to
#    whatever the caller's symlink points at. The anchor is now
#    `runs_root.resolve()` instead (passed in as its own argument, computed
#    the same way in `_CHECKSUM_SCRIPT`): a directory entry reached through
#    a symlinked run_dir no longer resolves inside the runs root, so it is
#    caught by the same relative_to() check as any other escape. If
#    run_dir itself escapes the runs root this way, every match fails that
#    check -- reported via `run_dir_escapes_runs_root` in the trailing
#    report entry, which `_stage_run_dir` turns into a hard
#    OpValidationError (this is a caller-named top-level path, not an
#    incidental glob hit -- see Important 1 of the 2026-09-16 whole-branch
#    review).
# 2. Size/count caps. `harvest_local`'s MAX_FILE_BYTES / MAX_TOTAL_BYTES /
#    MAX_FILES caps used to apply only after the ENTIRE tar had already been
#    transferred and extracted (`ssh_run_bytes` buffers the whole stream in
#    memory). RSeQC writes per-sample files at ~108 MB in real runs, so an
#    oversized tree was fully shipped over the wire before ever being
#    capped. The same three caps (imported from `harvest`, never
#    re-invented, so the two halves cannot drift) are now enforced here,
#    remotely, before a match is added to the tar -- an oversized file or an
#    over-cap tree fails fast and is never transferred. Cap hits are
#    reported the same way as skipped symlinks. See Important 2 of the
#    2026-09-16 review.
#
# 3. The inventory (nfcore-reingest addendum). The same script also walks
#    `harvest.inventory_globs()` -- the candidate OUTPUT files (BAMs, merged
#    matrices, h5ad/mtx/rds objects, VCFs, the MultiQC html report,
#    contaminant reports) -- and emits one `{"path", "bytes"}` entry per
#    match into the trailing report, alongside `skipped`. This is a LISTING
#    only: nothing here is added to the tar, nothing is read, nothing is
#    transferred beyond the path string and the integer from `stat()`.
#    Listing a file's name and size is a smaller disclosure than shipping
#    its content, but the account is shared all the same, so every
#    inventory candidate passes through the EXACT SAME containment checks as
#    a staged one -- reject a symlink, reject anything that resolves outside
#    `resolved_runs_root`, reject `st_nlink > 1` -- before it is ever listed.
#    A candidate that fails any of those, or that arrives after
#    `max_inventory_files` matches have already been listed, is skipped and
#    reported exactly like a staging skip, never dropped silently. See
#    `harvest.inventory_globs()` and `manifest.RunManifest.outputs` /
#    `.named_outputs` for the two different consumers this feeds.
_STAGE_REPORT_NAME = "__nextseek_stage_report__.json"

# Auto-hash the cheap ones: thresholds for hashing an inventoried output
# during run-harvest's existing staging SSH call -- never a second round
# trip. Deliberately separate module constants from run-checksum's own
# `_CHECKSUM_MAX_FILE_BYTES` / `_CHECKSUM_MAX_TOTAL_BYTES` below: those bound
# an EXPLICIT, caller-requested hash of files the caller already knows are
# worth the wait (up to 10 GB each, 20 GB aggregate); these bound an
# AUTOMATIC, no-one-asked-for-it hash that happens on every run-harvest call,
# so they must be small enough that harvest_local's own
# `_HARVEST_SSH_TIMEOUT_S` (150s, itself under the 180s CC-turn hard cap) is
# never meaningfully at risk from hashing alone -- getting a manifest back at
# all matters far more than getting a checksum for free. At a conservative
# shared-cluster-disk throughput of ~150 MB/s (the same figure run-checksum's
# own comment uses), the 500 MB aggregate ceiling hashes in a bit over 3
# seconds -- negligible next to the tar transfer and local extraction that
# already dominate a harvest call's wall clock. The 100 MB per-file ceiling
# covers a typical MultiQC html report, a small per-sample count matrix, or a
# modest BAM; it deliberately excludes the multi-GB primary alignment files
# this whole feature exists to avoid blocking on -- those still need
# `run-checksum` explicitly.
_HARVEST_CHECKSUM_MAX_FILE_BYTES = int(
    os.environ.get("NEXTSEEK_HARVEST_CHECKSUM_MAX_FILE_BYTES", 100_000_000))
_HARVEST_CHECKSUM_MAX_TOTAL_BYTES = int(
    os.environ.get("NEXTSEEK_HARVEST_CHECKSUM_MAX_TOTAL_BYTES", 500_000_000))

_STAGE_SCRIPT = """\
import hashlib, io, json, pathlib, re, sys, tarfile
verdict_re = re.compile(rb"Workflow execution completed (?:un)?successfully[^<]*")
verdict_scan_bytes = 64 * 1024 * 1024
run_dir = pathlib.Path(sys.argv[1])
runs_root = pathlib.Path(sys.argv[2])
resolved_runs_root = runs_root.resolve()
resolved_run_dir = run_dir.resolve()
max_file_bytes = int(sys.argv[3])
max_total_bytes = int(sys.argv[4])
max_files = int(sys.argv[5])
max_inventory_files = int(sys.argv[6])
max_checksum_file_bytes = int(sys.argv[7])
max_checksum_total_bytes = int(sys.argv[8])
report_name = sys.argv[9]
n_patterns = int(sys.argv[10])
patterns = sys.argv[11:11 + n_patterns]
inventory_patterns = sys.argv[11 + n_patterns:]
skipped = []
total_bytes = 0
files_added = 0
tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
run_dir_escapes_runs_root = False
try:
    resolved_run_dir.relative_to(resolved_runs_root)
except ValueError:
    run_dir_escapes_runs_root = True


def confined_stat(path, rel):
    # The SAME containment guard, shared by staging and the inventory
    # listing below: not a symlink, resolves inside the runs root, and no
    # extra hardlink alias. Returns None (already recorded in `skipped`) if
    # rejected, else the path's stat() result. A non-regular-file match (a
    # directory, a fifo, ...) returns None WITHOUT being reported -- that is
    # an ordinary glob miss, not an adversarial one.
    if path.is_symlink():
        skipped.append({"path": rel, "reason": "symlink (run_dir confinement cannot follow it safely)"})
        return None
    if not path.is_file():
        return None
    try:
        path.resolve().relative_to(resolved_runs_root)
    except ValueError:
        skipped.append({"path": rel, "reason": "resolves outside run_dir"})
        return None
    st = path.stat()
    if st.st_nlink > 1:
        # Hardlink: a directory entry inside run_dir sharing an inode with
        # a file elsewhere. No separate target path exists to resolve
        # away from (unlike a symlink), so this can only be caught by the
        # link count itself. Fail closed -- see the "1b. Hardlinks" note
        # above _STAGE_SCRIPT for why false positives here should be rare.
        skipped.append({"path": rel, "reason": "hardlinked; cannot confirm no other path reaches it"})
        return None
    return st


inventory = []
if not run_dir_escapes_runs_root:
    seen = set()
    for pattern in patterns:
        for path in sorted(run_dir.glob(pattern)):
            rel = str(path.relative_to(run_dir))
            if rel in seen:
                continue
            seen.add(rel)
            st = confined_stat(path, rel)
            if st is None:
                continue
            size = st.st_size
            if size > max_file_bytes:
                skipped.append({"path": rel, "reason": "exceeds max file bytes (%d > %d)" % (size, max_file_bytes)})
                continue
            if total_bytes + size > max_total_bytes:
                skipped.append({"path": rel, "reason": "exceeds total byte cap (%d + %d > %d)" % (total_bytes, size, max_total_bytes)})
                continue
            if files_added >= max_files:
                skipped.append({"path": rel, "reason": "exceeds max file count (%d)" % max_files})
                continue
            # TOCTOU: the resolve()/stat() checks above and this tar.add() are not
            # atomic -- the path could be swapped between them. This window is
            # currently inert only because (a) tar.add() no longer dereferences
            # symlinks (dereference=True was removed, see "1." above) and (b) the
            # local extraction in _stage_run_dir filters on member.isfile(), so a
            # symlink swapped in here would tar as a symlink member and be
            # dropped on extraction, not followed. A future edit to either of
            # those two behaviours reopens this window -- keep them paired.
            tar.add(str(path), arcname=rel)
            total_bytes += size
            files_added += 1

    seen_inventory = set()
    checksum_bytes_used = 0
    for pattern in inventory_patterns:
        for path in sorted(run_dir.glob(pattern)):
            rel = str(path.relative_to(run_dir))
            if rel in seen_inventory:
                continue
            seen_inventory.add(rel)
            st = confined_stat(path, rel)
            if st is None:
                continue
            if len(inventory) >= max_inventory_files:
                skipped.append({"path": rel, "reason": "exceeds max inventory file count (%d)" % max_inventory_files})
                continue
            entry = {"path": rel, "bytes": st.st_size}

            # Checksum_PrimaryData, for free where possible (nfcore-reingest
            # addendum): hash the file itself, bounded twice (a per-file
            # ceiling and a running total budget across the whole run -- see
            # _HARVEST_CHECKSUM_MAX_FILE_BYTES/_HARVEST_CHECKSUM_MAX_TOTAL_BYTES
            # above for why these numbers). Exhausting either bound is a
            # reported `skipped` entry, never a failure -- getting the rest
            # of the manifest back matters more than one more checksum.
            if st.st_size > max_checksum_file_bytes:
                skipped.append({"path": rel, "reason": "exceeds max checksum file bytes (%d > %d); use run-checksum explicitly" % (st.st_size, max_checksum_file_bytes)})
            elif checksum_bytes_used + st.st_size > max_checksum_total_bytes:
                skipped.append({"path": rel, "reason": "exceeds checksum byte budget (%d + %d > %d); use run-checksum explicitly" % (checksum_bytes_used, st.st_size, max_checksum_total_bytes)})
            else:
                h = hashlib.md5()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                entry["checksum"] = h.hexdigest()
                checksum_bytes_used += st.st_size

            inventory.append(entry)

    # Nextflow's completion verdict, from the newest execution report. The
    # report embeds every task's data and can exceed max_file_bytes, so it is
    # never staged: only its one verdict sentence is read here and returned,
    # under the same containment guard as every staged and listed file.
    # Reading stops at the sentence, or after verdict_scan_bytes.
    reports = sorted(run_dir.glob("pipeline_info/execution_report*.html"))
    if reports:
        path = reports[-1]
        rel = str(path.relative_to(run_dir))
        if confined_stat(path, rel) is not None:
            found = None
            scanned = 0
            with open(path, "rb") as fh:
                for line in fh:
                    match = verdict_re.search(line)
                    if match:
                        found = " ".join(match.group(0).decode("utf-8", "replace").split())
                        break
                    scanned += len(line)
                    if scanned > verdict_scan_bytes:
                        break
            verdict = json.dumps({"path": rel, "verdict": found}).encode()
            info = tarfile.TarInfo(name="__nextseek_run_verdict__.json")
            info.size = len(verdict)
            tar.addfile(info, fileobj=io.BytesIO(verdict))

report = json.dumps({
    "skipped": skipped,
    "run_dir_escapes_runs_root": run_dir_escapes_runs_root,
    "inventory": inventory,
}).encode()
info = tarfile.TarInfo(name=report_name)
info.size = len(report)
tar.addfile(info, fileobj=io.BytesIO(report))
tar.close()
"""


def _stage_run_dir(luria_env: dict, run_dir: str, runs_root: str, staged_dir: str,
                    key_path: str) -> tuple[list[dict], list[dict]]:
    """Stage every ``harvest.GENERIC_GLOBS`` match from ``run_dir`` on Luria
    into ``staged_dir``, ready for ``harvest.harvest_local``, and separately
    LIST (never stage) every ``harvest.inventory_globs()`` match. See
    ``_STAGE_SCRIPT`` above for why this runs the glob matching remotely, in
    Python, and packs the staged matches into a single tar stream rather than
    an `ls` + per-file `cat`, and for the symlink/cap enforcement it does
    before ever adding a match to that stream or the inventory listing.

    ``runs_root`` (not ``run_dir``) is the confinement anchor the remote
    script resolves against -- see "1c. run_dir ITSELF as a symlink" above
    _STAGE_SCRIPT. If the remote script reports ``run_dir`` itself resolves
    outside ``runs_root``, that is a hard :class:`OpValidationError`, not a
    skip: ``run_dir`` is a caller-named top-level path, not an incidental
    glob hit.

    Returns ``(skipped, inventory)``: ``skipped`` is the list of
    ``{"path", "reason"}`` entries the remote script skipped (symlinks,
    escapes, hardlinks, or cap hits -- from either half) so the caller can
    surface them rather than let the omission pass silently; ``inventory``
    is the list of ``{"path", "bytes"}`` entries -- plus an optional
    ``"checksum"`` key when the file was cheap enough to hash under
    ``_HARVEST_CHECKSUM_MAX_FILE_BYTES``/``_HARVEST_CHECKSUM_MAX_TOTAL_BYTES``
    (see the checksum block in ``_STAGE_SCRIPT`` above) -- for
    ``harvest_local``'s ``inventory`` parameter.
    """
    import io
    import shlex
    import tarfile

    from chat_nextseek.luria.ssh import ssh_run_bytes
    from NessieAI.ns.reingest.harvest import (
        MAX_FILE_BYTES, MAX_FILES, MAX_INVENTORY_FILES, MAX_TOTAL_BYTES, inventory_globs,
        staged_globs,
    )

    # GENERIC_GLOBS plus every map's own harvest_globs -- the maps' per-pipeline
    # lists used to be declared and never read, so their files were never
    # fetched. `len()` below tells the remote script where the stage patterns
    # end and the inventory patterns begin, so it must be this list's length.
    stage_patterns = staged_globs()
    inventory_patterns = inventory_globs()

    remote_cmd = " ".join([
        "python3", "-c", shlex.quote(_STAGE_SCRIPT), shlex.quote(run_dir), shlex.quote(runs_root),
        shlex.quote(str(MAX_FILE_BYTES)), shlex.quote(str(MAX_TOTAL_BYTES)), shlex.quote(str(MAX_FILES)),
        shlex.quote(str(MAX_INVENTORY_FILES)),
        shlex.quote(str(_HARVEST_CHECKSUM_MAX_FILE_BYTES)), shlex.quote(str(_HARVEST_CHECKSUM_MAX_TOTAL_BYTES)),
        shlex.quote(_STAGE_REPORT_NAME), shlex.quote(str(len(stage_patterns))),
        *(shlex.quote(pattern) for pattern in stage_patterns),
        *(shlex.quote(pattern) for pattern in inventory_patterns),
    ])
    # Bounded, not indefinite: see _HARVEST_SSH_TIMEOUT_S below for why this
    # backstops a stalled shared filesystem rather than an oversized
    # request (the size/count caps enforced remotely, above, are that).
    tar_bytes = ssh_run_bytes(luria_env, remote_cmd, key_path=key_path, timeout=_HARVEST_SSH_TIMEOUT_S)
    skipped: list[dict] = []
    inventory: list[dict] = []
    run_dir_escapes_runs_root = False
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|") as tar:
        for member in tar:
            if member.name == _STAGE_REPORT_NAME:
                report_fileobj = tar.extractfile(member)
                report = json.loads(report_fileobj.read()) if report_fileobj else {}
                skipped = report.get("skipped", [])
                inventory = report.get("inventory", [])
                run_dir_escapes_runs_root = report.get("run_dir_escapes_runs_root", False)
                continue
            if not member.isfile():
                continue
            name = os.path.normpath(member.name)
            if name.startswith("..") or os.path.isabs(name):
                raise OpValidationError(f"staged tar entry escapes run_dir: {member.name!r}", field="run_dir", error_type="staged_entry_outside_the_run_dir")
            tar.extract(member, path=staged_dir)
    if run_dir_escapes_runs_root:
        raise OpValidationError(f"run_dir resolves outside the runs root: {run_dir!r}", field="run_dir", error_type="resolves_outside_the_runs_root")
    return skipped, inventory


# Wall-clock backstop for the run-harvest staging SSH call, mirroring
# run-checksum's _CHECKSUM_SSH_TIMEOUT_S (added in an earlier review pass;
# see its docstring for the full reasoning). run-harvest's own byte/count
# ceilings (harvest.MAX_FILE_BYTES / MAX_TOTAL_BYTES / MAX_FILES, enforced
# remotely in _STAGE_SCRIPT before any match is added to the tar) bound the
# LEGITIMATE work; this bounds the illegitimate/unexpected case (a stalled
# shared filesystem) instead. ssh_run_bytes had no timeout kwarg at all
# until this review pass (Important 2, 2026-09-16): a stalled remote glob
# or a wedged network hung the whole CC turn indefinitely, unlike
# run-checksum's ssh_run call, which already had one. Sized the same as
# run-checksum's ceiling (150s), comfortably under the 180s CC turn hard
# cap (NEXTSEEK_CC_TIMEOUT_HARD_MAX, cc_engine.py) with room left for the
# rest of the turn (local tar extraction + harvest_local parsing).
_HARVEST_SSH_TIMEOUT_S = int(os.environ.get("NEXTSEEK_HARVEST_SSH_TIMEOUT_S", 150))


def _pick_waiting_run(user):
    """run-harvest with no run_dir: "my run is done" names no folder.

    One waiting run (launched for ``user``, not yet reingested) is the run.
    Several come back as ``candidates`` for the agent to ask which, without
    staging anything. None is a refusal that asks for the folder.
    """
    from NessieAI.ns.reingest import launch_record

    waiting = launch_record.waiting_runs(user)
    if not waiting:
        raise OpValidationError(
            "no run you launched is waiting to be reingested; pass --run-dir with the "
            "finished run's folder", field="run_dir", error_type="no_launched_run_is_waiting")
    if len(waiting) == 1:
        return waiting[0].run_dir
    return {"manifest_id": None, "candidates": [
        {"run_dir": run.run_dir, "run_name": run.run_name, "pipeline": run.pipeline,
         "launched_at": run.launched_at.isoformat() if run.launched_at else None,
         "slurm_job_id": run.slurm_job_id, "samples": len(run.cohort or [])}
        for run in waiting]}


def _remove_key_copy(key_path: str, source_key: str) -> None:
    """Remove the temp key copy ``prepare_key`` made, and nothing else (never the source key or a device)."""
    if (key_path != source_key and os.path.basename(key_path).startswith("luria_key_")
            and os.path.isfile(key_path) and not os.path.islink(key_path)):
        try:
            os.remove(key_path)
        except OSError:
            pass


def _run_harvest(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Reingest step 1 — stage the allowlisted files off the cluster and parse
    them into a RunManifest. Read-only; like run-ls it never calls the write
    gate (see nextseek_api/assistant/CONTRACT.md).
    """
    import tempfile

    from NessieAI.ns.reingest import harvest
    from NessieAI.ns.reingest.store import save_manifest

    if not args.get("run_dir"):
        # A run whose upload job outlived its call is settled first, so it is not picked again.
        _settle_late_uploads(session, config)
        picked = _pick_waiting_run(getattr(session, "user", None))
        if isinstance(picked, dict):
            return picked
        args = {**args, "run_dir": picked}
    run_dir, luria_env, runs_root = _validate_run_dir(args, config)
    launch = _launch_record_for(run_dir)
    if launch is None:
        raise OpValidationError(
            "reingest covers Nessie-launched runs; this run has no launch record", field="run_dir", error_type="no_launch_record_for_this_run")
    user = getattr(session, "user", None)
    if not getattr(user, "is_superuser", False) and (
            launch.launched_by_id is None or launch.launched_by_id != getattr(user, "pk", None)):
        raise OpValidationError("this run was launched by someone else", field="run_dir", error_type="not_your_run")
    from chat_nextseek.luria.ssh import prepare_key
    key_path = prepare_key(luria_env["key"])
    try:
        with tempfile.TemporaryDirectory() as staged:
            skipped, inventory = _stage_run_dir(luria_env, run_dir, runs_root, staged, key_path)
            # run_dir (the cluster path) is passed through as harvest_local's
            # provenance/lookup label -- `staged` is only the local read root.
            # Without this, the manifest and PipelineRun lookup are both keyed
            # off the temp staging path, which never matches the launch
            # record's run_dir (see harvest_local's docstring). `inventory` is
            # the remote inventory_globs() listing -- harvest_local never sees
            # `run_dir` (the real cluster directory) itself, only `staged`, so
            # it cannot gather that listing on its own.
            run_manifest = harvest.harvest_local(
                staged, run_dir=run_dir, lookup_by_fastq=_d_seq_by_fastq,
                sample_type_lookup=_sample_types_for_uids, inventory=inventory)
    finally:
        _remove_key_copy(key_path, luria_env["key"])

    run_manifest.launch_record_id = launch.pk
    if not run_manifest.pipeline.name and launch.pipeline:
        run_manifest.pipeline.name = launch.pipeline
    if not run_manifest.pipeline.version and launch.revision:
        run_manifest.pipeline.version = launch.revision

    # Files the remote stage skipped (a symlink escaping run_dir, or a
    # size/count cap hit) must stay visible, not vanish quietly -- surfaced
    # both in the manifest's own warnings and in the op result below.
    skipped = skipped or []
    run_manifest.warnings.extend(
        f"staging skipped {item['path']}: {item['reason']}" for item in skipped)

    # A failed run's outputs may be partial or truncated. Registering them into a
    # database of record by default is the wrong choice; proceeding is a decision
    # the user makes explicitly.
    if run_manifest.execution.failed and not args.get("allow_failed_run"):
        raise OpValidationError(
            f"{run_manifest.execution.failed} process(es) failed in this run. "
            f"Re-run with --allow-failed-run to reingest it anyway.", field="allow_failed_run", error_type="run_has_failed_processes")

    manifest_id = save_manifest(run_manifest)
    return {"run_dir": run_dir, "manifest_id": manifest_id,
            "manifest": run_manifest.model_dump(), "skipped": skipped}


_CHECKSUM_MAX_FILES = int(os.environ.get("NEXTSEEK_CHECKSUM_MAX_FILES", 200))

# Byte ceilings for run-checksum, deliberately NOT harvest.py's MAX_FILE_BYTES
# / MAX_TOTAL_BYTES (4 MB / 32 MB). Those cap small QC/metadata TEXT files
# (params.json, MultiQC tables, ...) that get fully read into memory and
# parsed into a manifest -- a ceiling sized for that job would reject a
# legitimate BAM/FASTQ outright, which is exactly the primary data this op
# exists to checksum. These ceilings instead bound the one thing that makes
# run-checksum the DoS this op was split out to avoid (see the docstring
# below and Important 1 of the 2026-09-16 review): the remote hashing time.
# Sized so that _CHECKSUM_MAX_FILES (200) files at the per-file ceiling could
# never blow the SSH call's own timeout (_CHECKSUM_SSH_TIMEOUT_S below) even
# at a conservative shared-cluster-disk md5 throughput (~150 MB/s): the
# aggregate ceiling (20 GB) hashes in ~135s at that rate, comfortably inside
# a 150s SSH timeout that itself sits under the default 180s CC turn hard cap
# (NEXTSEEK_CC_TIMEOUT_HARD_MAX, cc_engine.py) with room left for the rest of
# the turn. The per-file ceiling (10 GB) is generous for one sequencing file
# without needing to equal the aggregate.
_CHECKSUM_MAX_FILE_BYTES = int(os.environ.get("NEXTSEEK_CHECKSUM_MAX_FILE_BYTES", 10_000_000_000))
_CHECKSUM_MAX_TOTAL_BYTES = int(os.environ.get("NEXTSEEK_CHECKSUM_MAX_TOTAL_BYTES", 20_000_000_000))
# Wall-clock backstop for the SSH call itself: the byte ceilings above bound
# the LEGITIMATE work to well under this, so this timeout exists to bound the
# illegitimate/unexpected case (a stalled shared filesystem, a wedged
# network) rather than to be the primary defense against an oversized
# request -- that job is the byte ceilings, enforced before any file is
# opened for reading (see _CHECKSUM_SCRIPT).
_CHECKSUM_SSH_TIMEOUT_S = int(os.environ.get("NEXTSEEK_CHECKSUM_SSH_TIMEOUT_S", 150))

# The remote-side half of run-checksum: unlike run-harvest's GENERIC_GLOBS
# matches, ``--paths`` is CALLER-supplied -- an explicit request, not an
# incidental glob hit. So every one of the guards below is surfaced as an
# "escaped" entry that _run_checksum turns into a hard OpValidationError,
# never folded silently into "skipped" (see the module docstring and
# test_run_checksum_op.py's docstring for why that distinction matters).
# The one exception is the byte-ceiling checks added below: per BINDING
# CONSTRAINTS a size refusal may be a skip, but it is never silent -- it is
# always reported by name in "skipped", exactly like a missing file.
#
# The escape guards mirror _STAGE_SCRIPT's exactly, and for the same reason:
# the read already happens on the cluster side, before any byte reaches this
# process, so confinement can only be enforced there.
# 1. Symlinks. A path inside run_dir can be a symlink pointing anywhere on
#    the shared cluster account; is_symlink() is checked before anything
#    else touches the path.
# 2. Escape via a symlinked ancestor directory (or any other resolution
#    mismatch): resolve() must still land inside the resolved run_dir.
# 3. Hardlinks. `os.link(outside, run_dir/x)` makes `x` a directory entry
#    INSIDE run_dir sharing an inode with a file elsewhere -- it is not a
#    symlink and has no separate target path to resolve away from, so only
#    `st_nlink > 1` catches it. Deliberately blunt (also rejects a benign
#    multiply-linked file), same trade-off as run-harvest's staging guard.
# A missing file or a non-regular-file match (a directory, a fifo, ...) is
# NOT an escape -- those are ordinary misses, reported in "skipped".
#
# 4. Size. `st.st_size` is read from the SAME stat() call already made for
#    the hardlink check above -- so the per-file/total ceilings below are
#    enforced BEFORE `open()`/`read()` ever touches the file. A file over the
#    per-file ceiling is skipped without a single byte read; once the
#    running total would cross the aggregate ceiling, every remaining path is
#    skipped the same way -- unhashed. This is what closes Important 1: 200
#    caller-named multi-GB files can no longer make this op read past a
#    bounded amount of data no matter how large the files actually are.
# 5. run_dir ITSELF as a symlink (Important 1, 2026-09-16 whole-branch
#    review). Anchored on `run_dir.resolve()`, every ``rel`` reached through
#    a symlinked run_dir used to "resolve inside run_dir" trivially, for the
#    same reason _STAGE_SCRIPT's equivalent bug did -- see its "1c." note.
#    Anchoring on `runs_root.resolve()` instead (passed in as its own
#    argument) means every ``rel`` fails the relative_to() check the moment
#    run_dir escapes the runs root, landing in ``escaped`` -- which
#    `_run_checksum` already turns into a hard OpValidationError, exactly
#    like any other caller-named escape.
_CHECKSUM_SCRIPT = """\
import hashlib, json, pathlib, sys
run_dir = pathlib.Path(sys.argv[1])
runs_root = pathlib.Path(sys.argv[2])
resolved_runs_root = runs_root.resolve()
resolved_run_dir = run_dir.resolve()
max_file_bytes = int(sys.argv[3])
max_total_bytes = int(sys.argv[4])
rels = sys.argv[5:]
checksums = {}
skipped = []
escaped = []
total_bytes = 0
run_dir_escapes_runs_root = False
try:
    resolved_run_dir.relative_to(resolved_runs_root)
except ValueError:
    run_dir_escapes_runs_root = True


for rel in rels:
    if run_dir_escapes_runs_root:
        escaped.append({"path": rel, "reason": "run_dir resolves outside the runs root"})
        continue
    path = run_dir / rel
    if path.is_symlink():
        escaped.append({"path": rel, "reason": "symlink (run_dir confinement cannot follow it safely)"})
        continue
    if not path.exists():
        skipped.append({"path": rel, "reason": "does not exist"})
        continue
    if not path.is_file():
        skipped.append({"path": rel, "reason": "not a regular file"})
        continue
    try:
        path.resolve().relative_to(resolved_runs_root)
    except ValueError:
        escaped.append({"path": rel, "reason": "resolves outside run_dir"})
        continue
    st = path.stat()
    if st.st_nlink > 1:
        escaped.append({"path": rel, "reason": "hardlinked; cannot confirm no other path reaches it"})
        continue
    size = st.st_size
    if size > max_file_bytes:
        skipped.append({"path": rel, "reason": "exceeds max file bytes (%d > %d)" % (size, max_file_bytes)})
        continue
    if total_bytes + size > max_total_bytes:
        skipped.append({"path": rel, "reason": "exceeds total byte cap (%d + %d > %d)" % (total_bytes, size, max_total_bytes)})
        continue
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    checksums[rel] = h.hexdigest()
    total_bytes += size
print(json.dumps({"checksums": checksums, "skipped": skipped, "escaped": escaped}))
"""


def _run_checksum(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Reingest step 2 — md5 a settled set of primary-data files on the cluster.

    Separate from run-harvest on purpose: which files become File_PrimaryData is
    only known after sample types are assigned, and hashing multi-GB BAMs would
    put the harvest outside the CC turn's wall clock. Read-only; like run-ls and
    run-harvest it never calls the write gate (see
    nextseek_api/assistant/CONTRACT.md).

    ``paths`` is caller-supplied, not a glob match, so every containment guard
    below (traversal, symlink, hardlink, ancestor escape) is a hard
    OpValidationError rather than a silent "skipped" entry -- an explicit
    request to read outside run_dir is adversarial, not incidental. Only a
    genuinely missing/non-regular-file match is a reported skip.

    The file COUNT cap (``_CHECKSUM_MAX_FILES``) alone does not bound how
    long this op can hang: 200 files can still be arbitrarily large, which is
    exactly the wall-clock blowout this op was split out to avoid (see
    above). ``_CHECKSUM_MAX_FILE_BYTES`` / ``_CHECKSUM_MAX_TOTAL_BYTES`` close
    that gap remotely, inside ``_CHECKSUM_SCRIPT``, before a single byte of
    an oversized file is read; a size refusal is reported in "skipped", never
    silently dropped. ``_CHECKSUM_SSH_TIMEOUT_S`` bounds the SSH call itself
    so an unexpected hang (not just an oversized request) is also bounded.

    An optional ``manifest_id`` folds this call's checksums into a manifest
    run-harvest already saved: loaded, merged (``RunManifest.checksums``,
    additive -- an earlier checksum for a path not named this time survives),
    and re-saved. ``store.save_manifest`` derives the id from a content
    digest, so a manifest carrying checksums is a genuinely DIFFERENT,
    immutable manifest -- this never mutates the manifest behind the
    original id, which keeps loading exactly what it always has. The new id
    is returned as ``manifest_id`` for the caller to thread into
    ``build-upload-xlsx`` in place of the original. Omitting ``manifest_id``
    keeps every existing caller's behaviour byte-identical (no such key in
    the result at all), since not every caller of this read-only op has a
    manifest to fold checksums into.
    """
    import shlex

    run_dir, luria_env, runs_root = _validate_run_dir(args, config)
    if run_dir == runs_root:
        raise OpValidationError("run_dir is the runs root", field="run_dir", error_type="the_runs_root")
    rels = [p.strip() for p in str(args.get("paths") or "").split(",") if p.strip()]
    if not rels:
        raise OpValidationError("paths must name at least one file", field="paths", error_type="names_no_file")
    if len(rels) > _CHECKSUM_MAX_FILES:
        raise OpValidationError(
            f"too many files: {len(rels)} > {_CHECKSUM_MAX_FILES}; narrow the set", field="paths", error_type="too_many_files")
    for rel in rels:
        joined = os.path.normpath(os.path.join(run_dir, rel))
        if joined != run_dir and not joined.startswith(run_dir + "/"):
            raise OpValidationError(f"path outside the run dir: {rel!r}", field="paths", error_type="outside_the_run_dir")

    # Validated -- and the manifest loaded, if named -- before the expensive
    # remote SSH hashing below, the same "fail cheap before failing
    # expensive" ordering _validate_run_dir already applies to run_dir/paths.
    manifest_id = str(args.get("manifest_id") or "").strip()
    run_manifest = None
    if manifest_id:
        if not manifest_id.isalnum():
            raise OpValidationError(f"manifest_id must be alphanumeric, got {manifest_id!r}", field="manifest_id", error_type="must_be_alphanumeric")
        from NessieAI.ns.reingest.store import load_manifest
        try:
            run_manifest = load_manifest(manifest_id)
        except FileNotFoundError:
            raise OpValidationError(f"no manifest {manifest_id!r}", field="manifest_id", error_type="no_such_manifest")
        # Both `run_dir` and `manifest_id` are caller-supplied off the same CC
        # turn, and nothing else ties them together: a session that reingests
        # run A then run B, but passes run A's stale manifest_id while hashing
        # run B's files, would otherwise have this call's checksums merge into
        # run A's manifest. Relative output paths collide across nf-core runs
        # by construction (every run has its own "star_salmon/SAMPLE_1...bam"),
        # so `_primary_output` would then match run A's OutputRecord for that
        # identical path and ship run B's digest as run A's measured value --
        # exactly the model-authored-metadata failure the manifest-id design
        # exists to eliminate, just laundered through a caller mismatch
        # instead of a hand-typed number. Cheap (no I/O) and done before the
        # expensive SSH hash, like every other check in this function.
        # `_validate_run_dir` already normalised `run_dir` with `normpath`;
        # normalise the manifest's own `run_dir` the same way before
        # comparing, since `harvest.harvest_local` stores it verbatim from
        # whatever `_run_harvest` passed in (also `_validate_run_dir`'s
        # output, but from that separate call, not necessarily
        # byte-identical as a raw string).
        manifest_run_dir = os.path.normpath(str(run_manifest.run_dir))
        if manifest_run_dir != run_dir:
            raise OpValidationError(
                f"manifest_id {manifest_id!r} was harvested from {manifest_run_dir!r}, "
                f"not the run_dir being hashed ({run_dir!r})", field="manifest_id", error_type="harvested_from_another_run_dir")

    from chat_nextseek.luria.ssh import prepare_key, ssh_run
    key_path = prepare_key(luria_env["key"])
    try:
        remote_cmd = " ".join([
            "python3", "-c", shlex.quote(_CHECKSUM_SCRIPT), shlex.quote(run_dir), shlex.quote(runs_root),
            shlex.quote(str(_CHECKSUM_MAX_FILE_BYTES)), shlex.quote(str(_CHECKSUM_MAX_TOTAL_BYTES)),
            *(shlex.quote(rel) for rel in rels),
        ])
        # Bounded, not indefinite: see _CHECKSUM_SSH_TIMEOUT_S above for why
        # the byte ceilings (not this timeout) are the primary defense, and
        # this is the backstop for the unexpected hang (a stalled shared
        # filesystem) rather than the oversized request.
        out = ssh_run(luria_env, remote_cmd, key_path=key_path, timeout=_CHECKSUM_SSH_TIMEOUT_S)
    finally:
        _remove_key_copy(key_path, luria_env["key"])

    try:
        payload = json.loads(out)
    except ValueError as exc:
        raise OpValidationError(f"checksum script produced no parseable output: {exc}", field="paths", error_type="checksum_output_unreadable") from exc

    # A refusal is reported, never a silent omission: every escaped path is
    # named, with its reason, in the raised message -- not dropped, and not
    # quietly merged into "skipped" (see the module note above _CHECKSUM_SCRIPT).
    escaped = payload.get("escaped") or []
    if escaped:
        detail = "; ".join(f"{item['path']!r}: {item['reason']}" for item in escaped)
        raise OpValidationError(f"path outside the run dir or unsafe: {detail}", field="paths", error_type="outside_the_run_dir_or_unsafe")

    checksums = payload.get("checksums") or {}
    result = {"run_dir": run_dir, "checksums": checksums,
              "skipped": payload.get("skipped") or []}

    if run_manifest is not None:
        from NessieAI.ns.reingest.store import save_manifest
        # Merge keyed by the NORMALISED path, not the caller's raw `--paths`
        # token verbatim: `OutputRecord.path` is harvest-normalised (see
        # harvest.py), so a caller-supplied "./star_salmon/x.bam" or
        # "star_salmon//x.bam" would otherwise land in `checksums` under a
        # key `mapper._primary_output`'s harvested path never matches,
        # silently losing the checksum this call just paid an SSH round trip
        # to compute. `result["checksums"]` (returned to the caller) keeps
        # the raw token as its key -- it is just an echo of what was asked
        # for, not a lookup key into the manifest.
        run_manifest.checksums.update(
            {os.path.normpath(rel): digest for rel, digest in checksums.items()})
        result["manifest_id"] = save_manifest(run_manifest)

    return result


def _d_seq_by_fastq(path: str, types: tuple[str, ...] = ("D.SEQ",)) -> list[str]:
    """UIDs of a sample type in ``types`` whose File_PrimaryData /
    Link_PrimaryData mentions ``path``. Defaults to D.SEQ only, this
    lookup's original scope -- ``harvest.harvest_local`` passes the run's
    own pipeline map's ``accepts_parent_types`` here instead when it has
    one (see maps.PipelineMap.accepts_parent_types)."""
    from nextseek_api.services.reingest_lookups import uids_by_primary_data
    return uids_by_primary_data(path, types=types)


def _sample_types_for_uids(uids: list[str]) -> dict[str, str]:
    """The SampleType title per resolved parent UID, for the QC backfill.

    A parent is no longer necessarily a D.SEQ -- a pipeline whose map declares
    a wider ``accepts_parent_types`` can be launched from an already-analysed
    A.* sample -- so the backfill row must be typed from what the parent
    actually IS. The UID's own prefix is not a safe source for that: measured
    against the live instance, 766 of 51,372 samples do not start with their
    type code, so parsing it would be quietly wrong for those. This asks the
    database instead. A UID it cannot resolve is OMITTED, and the backfill
    writes no row for that sample rather than guessing a type."""
    from nextseek_api.services.reingest_lookups import sample_types_for_uids
    return sample_types_for_uids(uids)


def _build_upload_xlsx(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Reingest step 2 — render reviewable workbooks.

    Two calling conventions:

    * ``args["manifest_id"]`` (current) — the manifest-driven path. CC sends
      only a manifest_id and a mode; the server loads its own copy of the
      harvested manifest (``store.load_manifest``), maps it
      (``mapper.apply``), and derives every row itself. No measured number
      ever round-trips through the model. Writes no NExtSEEK data — proposals
      are RETURNED in the envelope; the service layer
      (``nextseek_api/services/assistant.py``) persists them alongside the
      artifact bundle.
    * ``args["rows"]`` (legacy) — CC-composed rows, kept working verbatim for
      any caller that still builds its own
      ``{"SampleType", "json_metadata", "assay_ids"}`` rows directly.
    """
    if args.get("manifest_id"):
        return _build_upload_xlsx_from_manifest(args, outputs_dir, session=session)
    if args.get("rows") is not None:
        if args.get("answers"):
            raise OpValidationError("answers need manifest_id; the legacy rows path takes none", field="answers", error_type="need_manifest_id")
        return _build_upload_xlsx_from_rows(args, outputs_dir)
    raise OpValidationError("build-upload-xlsx needs either manifest_id or rows", field="args", error_type="needs_manifest_id_or_rows")


def _build_upload_xlsx_from_rows(args, outputs_dir):
    """Legacy path: render one 4-sheet upload workbook per A.* sample type from
    CC-composed rows.

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

    from nextseek_api.services.reingest_lookups import attributes_for, known_sample_types

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    # known_sample_types() returning empty means "the catalog could not be read"
    # (context_catalog's house rule: any failure costs the caller an empty
    # catalog, never an exception), not "every type here is unknown". Taking it
    # literally would hard-reject a whole reingest whenever the catalog table is
    # briefly unreachable. So an EMPTY catalog falls back to the old permissive
    # set(by_type) — derived from the rows themselves, so unknown_sampletype
    # can't fire — and only a POPULATED catalog lets that check actually reject.
    #
    # This is DELIBERATELY inconsistent with the manifest-driven path below
    # (_build_upload_xlsx_from_manifest), which raises on the same empty-catalog
    # signal instead of falling back (matching proposals.attribute_exists' own
    # "an entirely empty catalog is an outage, not an answer" rule). The two
    # paths differ because their callers differ: the manifest path is Task 7's
    # DB-backed reingest pipeline, where an outage silently producing an
    # unvalidated workbook is the exact failure the fallback-vs-raise design
    # decision exists to prevent. This legacy rows path is called with
    # CC-composed rows from callers that make no assumption the sample-type
    # table is reachable (or even present) at all, and
    # test_build_upload_xlsx_op.py::test_empty_catalog_falls_back_to_permissive_known_types
    # pins exactly this permissive behaviour, deliberately without django_db, so
    # that it can exercise a genuinely unreachable table. Raising here would
    # hard-reject every legacy call whenever that table is briefly unreachable,
    # which is the regression this fallback was written to avoid in the first
    # place -- so it stays, and this comment is the reconciliation the review
    # asked for rather than a silent, unexplained divergence between the two.
    known = known_sample_types() or set(by_type)
    saved_files: dict[str, str] = {}
    qa: dict[str, dict] = {}
    for st, st_rows in by_type.items():
        attrs = attributes_for(st)
        required = [a["title"] for a in attrs if a.get("required")]
        # a.get("server_required", a.get("required")): an attributes_for
        # entry that never set the new key (an un-updated test double, or a
        # caller mocking the old two-key shape) falls back to that same
        # entry's own "required" -- keeping today's HARD-everything
        # behaviour for anyone who hasn't wired the new flag through, per
        # reingest_qa.qa_rows' own None-vs-explicit-list contract.
        server_required = [a["title"] for a in attrs if a.get("server_required", a.get("required"))]
        report = qa_rows(st_rows, sample_type=st, known_sampletypes=known,
                         required_fields=required, server_required_fields=server_required,
                         existing_parent_uids=existing)
        qa[st] = {"disposition": report.disposition, "hard": report.hard, "soft": report.soft}
        if report.disposition == HARD_REJECT:
            continue
        safe_name = st.replace("/", "_").replace(" ", "_")          # readable filename (keeps the dot)
        # The artifact KEY is the download URL segment, which the route only
        # accepts as [\w]+ — so it must be word-chars only (A.SCXP -> A_SCXP).
        # The file on disk keeps the dot; download serves it by its real name.
        #
        # NessieAI/ns/reingest/report.py's `_resolve_artifact` reconstructs
        # this same key from a bare sample type to look the workbook back up,
        # and duplicates this exact normalisation rather than importing it
        # from here (report.py may not import granular.py). If you change
        # this normalisation, change the copy there too, or a sample type
        # with a hyphen/space/slash silently drops out of the QA report.
        safe_key = safe_name.replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}.xlsx")
        render_upload_workbook(st, st_rows, path,
                               schema_fields=[a["title"] for a in attrs])
        saved_files[f"reingest_{safe_key}"] = path
    return {"saved_files": saved_files, "qa": qa}


def _existing_notes(rows) -> dict[str, str]:
    """Current Notes for every UID in ``rows``.

    A UID absent from the result means the fetch failed for it, and QA turns that
    into a hard reject rather than letting a blind write destroy curator text.
    """
    from nextseek_api.services.reingest_lookups import notes_for_uids

    uids = [str((r.get("json_metadata") or {}).get("UID") or "").strip()
            for r in rows]
    return notes_for_uids([u for u in uids if u])


def _build_upload_xlsx_from_manifest(args, outputs_dir, session=None, rebuilt_from=None):
    """Manifest-driven path: render workbooks from a harvested manifest.

    Takes a manifest_id, not values: CC sends a mode and the server fills every
    number from its own copy of the manifest (``store.load_manifest`` +
    ``mapper.apply``). A measured value therefore never round-trips through
    the model.

    Writes no NExtSEEK data. Attribute proposals are RETURNED; the service
    layer persists them alongside the artifact bundle -- this function must
    never call the recording function itself (see NessieAI/ns/CLAUDE.md's
    "build-upload-xlsx never writes to NExtSEEK" invariant).

    A mapped D.SEQ backfill attribute (map- or approved-origin) that is not
    actually defined on D.SEQ's schema (``proposals.attribute_exists``) is
    parked into that sample's ``Notes`` instead of a normal column (never
    silently invented as a real attribute), and a ``needs_definition``
    proposal is queued for it -- see
    docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md section 8.
    ``attribute_exists`` raises on an outage (an entirely empty catalog) by
    design; that exception is deliberately let through here rather than
    caught and defaulted to False, which would fabricate a schema gap.
    """
    import datetime
    import re
    from types import SimpleNamespace

    from NessieAI.ns.reingest import mapper, maps, proposals, report as user_report
    from NessieAI.ns.reingest import answers as answers_mod
    from NessieAI.ns.reingest import build_records
    from NessieAI.ns.reingest.notes import compose as compose_notes
    from NessieAI.ns.reingest.store import load_manifest
    from NessieAI.ns.reingest import assay_plan as assay_plan_mod
    from NessieAI.ns.reingest_qa import (
        ASSAY_SPLIT_ACROSS_STUDIES, HARD, HARD_REJECT, NO_ATTRIBUTES_TO_WRITE,
        PARENTS_JOIN_ASSAY, SOFT, Finding, qa_rows)
    from NessieAI.ns.upload_workbook import MODE_NEW, MODE_UPDATE, render_upload_workbook
    from nextseek_api.services.reingest_lookups import (
        attributes_for, existing_uids_for_primary_files_strict, known_sample_types,
        lab_code_from_uid, lab_for_code_strict, next_name_ordinal_strict,
        project_ids_for_uids_strict)

    def _slug(name: str) -> str:
        # Artifact keys are word characters only; the download route accepts
        # nothing else -- re.sub(r"\W+", ...) also folds whitespace (unlike
        # the legacy safe_name at :889, which only replaces "/" and " "
        # explicitly), so a pipeline name with a space still produces a
        # route-safe key.
        return re.sub(r"\W+", "_", name.split("/")[-1])

    manifest_id = str(args.get("manifest_id") or "").strip()
    if not manifest_id.isalnum():
        raise OpValidationError(f"manifest_id must be alphanumeric, got {manifest_id!r}", field="manifest_id", error_type="must_be_alphanumeric")
    mode = str(args.get("mode") or MODE_NEW)
    if mode not in (MODE_NEW, MODE_UPDATE):
        raise OpValidationError(f"mode must be {MODE_NEW!r} or {MODE_UPDATE!r}", field="mode", error_type="must_be_new_or_update")
    # Parsed before any lookup, so a malformed payload costs nothing.
    try:
        answers = answers_mod.parse_answers(args.get("answers"))
    except answers_mod.AnswerRejected as exc:
        raise OpValidationError(str(exc), field="answers", error_type="rejected") from exc

    # manifest_id is user-supplied (comes off the CC turn, not a value this
    # process minted), so an unknown-but-well-formed id must be a caller-visible
    # refusal, not the FileNotFoundError load_manifest's open() raises escaping
    # as an opaque 502.
    try:
        run_manifest = load_manifest(manifest_id)
    except FileNotFoundError:
        raise OpValidationError(f"no manifest {manifest_id!r}", field="manifest_id", error_type="no_such_manifest")
    user = getattr(session, "user", None)
    test_only = run_manifest.launch_record_id is None
    if test_only and not bool(getattr(user, "is_superuser", False)):
        raise OpValidationError(
            "this manifest is from a run Nessie didn't launch; only an admin can build it, "
            "for testing", field="manifest_id", error_type="test_only_manifest_needs_an_admin")
    pipeline = run_manifest.pipeline.name or "nf-core/rnaseq"
    run_name = run_manifest.pipeline.run_name or manifest_id
    pipeline_map = maps.load(pipeline)
    # The rules for this run's version. mapper.apply takes the base map and
    # resolves the version itself; everything else here that reads rules (the
    # assay per sample type, the curator-answer allowlist) reads this one, so
    # a rule only a version override adds is not missed.
    effective_map, _ = maps.effective(pipeline_map, run_manifest.pipeline.version)
    # Scientist is the person running the reingest, which is session state, not
    # anything the run directory knows. No session (a test, or the legacy rows
    # path) leaves it blank for a curator rather than attributing to nobody.
    full_name = ""
    if user is not None:
        full_name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    # `next_name_ordinal` is injected rather than imported by mapper, which
    # stays a pure function of manifest + map (same shape as harvest.py's
    # `lookup_by_fastq`). It raises on an unreachable catalog by design: a
    # fabricated "1" is how two A.GEX rows end up sharing a Name.
    approved = proposals.approved_rules(pipeline)

    # (sample_type, attribute) -> bool, memoised so a 20-sample backfill does
    # not re-query the schema catalog once per sample for the same attribute.
    exists_cache: dict[tuple[str, str], bool] = {}

    def _attribute_exists(sample_type: str, attribute: str) -> bool:
        key = (sample_type, attribute)
        if key not in exists_cache:
            # Deliberately not try/except'd: an outage (RuntimeError) must
            # propagate, never be swallowed into a fabricated False -- see
            # proposals.attribute_exists' own docstring.
            exists_cache[key] = proposals.attribute_exists(sample_type, attribute)
        return exists_cache[key]

    result = mapper.apply(run_manifest, pipeline_map,
                          approved_rules=approved,
                          context={"user_full_name": full_name,
                                   "next_name_ordinal": next_name_ordinal_strict,
                                   "lab_code_from_uid": lab_code_from_uid,
                                   "lab_for_code": lab_for_code_strict,
                                   "has_attribute": _attribute_exists})

    # An entirely empty catalog is an outage signal (proposals.attribute_exists'
    # own contract, right above), never an answer -- so this must raise here
    # too, exactly like that function does, rather than quietly substituting a
    # set derived from the very rows it is supposed to validate (which would
    # make UNKNOWN_SAMPLETYPE unfireable for the whole duration of an outage).
    # See NessieAI/ns/granular.py's legacy `_build_upload_xlsx_from_rows` (the
    # `known = known_sample_types() or set(by_type)` line) for the one place
    # in this module that still falls back on purpose, and why.
    known = known_sample_types()
    if not known:
        raise RuntimeError(
            "sample type catalog came back empty; treating this as an "
            "outage rather than validating this reingest run's sample types "
            "against an empty schema"
        )
    # Every D.SEQ UID the manifest resolved is, by construction, a legitimate
    # Parent target: mapper.py sets Parent only from a sample's own d_seq_uid
    # (its _HAS_PARENT set) or, for a multi-run sample, from its
    # d_seq_uid_multirun list. qa_rows' new-mode Parent-resolvability check
    # needs this cohort, both halves of it, or a row's Parent fails resolution
    # and hard-rejects the whole batch (a multi-run sample's did, PR 135 SF-4).
    existing_parent_uids = {s.d_seq_uid for s in run_manifest.samples if s.d_seq_uid}
    existing_parent_uids |= {uid for s in run_manifest.samples
                             for uid in s.d_seq_uid_multirun if uid}

    # A new row whose data file is already a sample of its type, under the same
    # parent, is left out: reingesting a run that was uploaded must never make a
    # second copy. A partly uploaded run still builds the rows it lacks. A row whose
    # path matches a sample but whose content has changed (a run redone in the same
    # folder) is left out too, and the reply names the older sample: the upload skips
    # a new row whose data file name is already a sample's (NAME_CHECK in
    # nextseek_api/batch_upload/orchestrator.py), so building it would promise a
    # sample the upload never makes (PR 135 F2). An outage raises, as every strict
    # lookup here does; it never reads as "nothing there".
    already_present: dict[str, dict] = {}
    path_changed: dict[str, list] = {}
    if mode == MODE_NEW:
        def _cell(row, name):
            attr = row.attributes.get(name)
            return attr.value if attr is not None else ""

        checked: dict[str, list] = {}
        for row in result.rows:
            if row.rule_id and row.sample_type != "D.SEQ":
                checked.setdefault(row.sample_type, []).append(row)
        dropped: set[int] = set()
        for sample_type, type_rows in checked.items():
            found = existing_uids_for_primary_files_strict(sample_type, [
                {"link": _cell(row, "Link_PrimaryData"),
                 "checksum": _cell(row, "Checksum_PrimaryData"),
                 "parent": _cell(row, "Parent")} for row in type_rows])
            uids = sorted({uid for match in found for uid in match["uids"]})
            if uids:
                dropped |= {id(row) for row, match in zip(type_rows, found) if match["uids"]}
                already_present[sample_type] = {"uids": uids, "total": len(type_rows)}
            changed = sorted({uid for match in found if not match["uids"]
                              for uid in match["changed"]})
            if changed:
                dropped |= {id(row) for row, match in zip(type_rows, found)
                            if not match["uids"] and match["changed"]}
                path_changed[sample_type] = changed
        if dropped:
            result = result.model_copy(
                update={"rows": [row for row in result.rows if id(row) not in dropped]})

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"

    today = datetime.date.today().isoformat()

    # Assay per sample type, by internal assay title, off the committed map.
    # A sample type with no `assay` declared resolves to no ASSAY rows, which
    # is what every map did before the field existed.
    assay_name_by_type: dict[str, str] = {
        rule.sample_type: rule.assay
        for rule in effective_map.outputs
        if rule.assay
    }
    plan_cache: dict[tuple[str, str], object] = {}

    class _TwoHouseAssays(Exception):
        """house_assay_title_strict's ValueError, kept apart from any other ValueError."""

    def _assay_plan(row, row_index: int, parent_value):
        """The row's study assay plan (NessieAI/ns/reingest/assay_plan.py).

        Only output-rule rows are filed; a QC backfill row (no rule_id) keeps
        its sample's own assays. Keyed on (sample_type, Parent): every row
        sharing a parent set shares an answer. An outage (RuntimeError) is not
        caught: it stops the build rather than reading as "no assay". A name
        matching two house assays (ValueError) blocks the row with
        HOUSE_ASSAY_AMBIGUOUS instead of escaping as an opaque error.
        """
        from NessieAI.ns.reingest import assay_plan
        from NessieAI.ns.reingest_qa import HOUSE_ASSAY_AMBIGUOUS
        from nextseek_api.services.reingest_lookups import (
            house_assay_title_strict, study_assays_for_parents_strict,
        )

        def house_title(name):
            try:
                return house_assay_title_strict(name)
            except ValueError as exc:
                logger.warning("reingest: %s", exc)
                raise _TwoHouseAssays from exc

        if not row.rule_id:
            return None
        parents = sorted({u.strip() for u in str(parent_value or "").split(";") if u.strip()})
        key = (row.sample_type, ";".join(parents))
        if key not in plan_cache:
            house_name = assay_name_by_type.get(row.sample_type)
            try:
                plan_cache[key] = assay_plan.plan_row(
                    sample_type=row.sample_type, row_index=row_index,
                    house_name=house_name, parents=parents,
                    test_only=test_only, house_title=house_title,
                    study_assays=study_assays_for_parents_strict)
            except _TwoHouseAssays:
                plan_cache[key] = assay_plan.RowAssayPlan(findings=[Finding(
                    code=HOUSE_ASSAY_AMBIGUOUS, severity=HARD, sample_type=row.sample_type,
                    row_index=row_index, detail={"assay": house_name})])
        cached = plan_cache[key]
        # Same plan, this row's own index on its findings and creations.
        return assay_plan.RowAssayPlan(
            assay_ids=list(cached.assay_ids),
            findings=[f.__class__(**{**f.__dict__, "row_index": row_index})
                      for f in cached.findings],
            to_create=[dict(e, rows=[row_index]) for e in cached.to_create],
            parent_assays=list(cached.parent_assays), studies=cached.studies)

    notes_once: dict[str, dict | None] = {}

    def _assemble(mapped_rows) -> SimpleNamespace:
        """One pass: row dicts, provenance, parked Notes and QA for this mode.

        Runs once without answers and, when answers are given, again after
        they are applied. Existing Notes are fetched at most once across both
        passes: a blip on a second fetch would turn a readable UID into a
        spurious NOTES_WOULD_CLOBBER (see the comment on the fetch below).
        """
        # Surface 2 of the superuser surfaces: a File_PrimaryData the mapper
        # picked among two or more same-basename candidates with no checksum to
        # decisively break the tie (see mapper.py's `MappedAttribute.candidates`
        # docstring). Collected here, not left in `result.rows`, so it can ride
        # into `render_qa_for_user`'s reply the same way `needs_definition`
        # already rides into `proposals` below -- reusing `result.unmapped`
        # would have been wrong: that channel means "a raw metric key nobody
        # claimed", a different fact from "an attribute WAS set, but the pick
        # among ambiguous candidates was not forced by evidence".
        #
        # Keyed on (sample_type, attribute, tuple(candidates)) rather than
        # appended as a flat list: a per_run output rule (e.g. a shared
        # gene-counts matrix) sets the SAME ambiguous attribute, with the SAME
        # candidate set, on every one of a run's sample rows, so a flat list
        # would relay one near-identical entry per sample -- exactly the
        # enumerate-instead-of-count failure report.py's own module docstring
        # (rule 1) exists to prevent, and on a 20-sample run it is ~20 entries
        # for one genuine ambiguity. A true per-sample rule's candidates differ
        # by sample (the harvested path embeds the sample name), so those stay
        # distinct entries here, each affecting exactly the samples that hit it.
        ambiguous_primary_groups: dict[tuple, dict] = {}
        provenance_by_type: dict[str, list] = {}
        rows_by_type: dict[str, list] = {}
        mapped_by_type: dict[str, list] = {}
        needs_definition: list[dict] = []
        # rows carrying at least one parked value, so their Notes can be composed
        # once existing Notes for their UIDs are known (below, after this loop).
        dseq_parked: list[tuple[dict, dict, dict]] = []
        assay_plans_by_type: dict[str, list] = {}

        for row in mapped_rows:
            want_update = row.sample_type == "D.SEQ"
            if (mode == MODE_UPDATE) != want_update:
                continue

            meta: dict = {}
            provenance_entry: dict = {}
            parked_values: dict = {}
            parked_attrs: dict = {}
            for name, attr in row.attributes.items():
                origin = attr.origin
                if (want_update and name != "Parent"
                        and not _attribute_exists(row.sample_type, name)):
                    origin = mapper.ORIGIN_PARKED
                    parked_values[name] = attr.value
                    parked_attrs[name] = attr
                else:
                    meta[name] = attr.value
                provenance_entry[name] = {"origin": origin, "raw_key": attr.raw_key,
                                          "answered_by": attr.answered_by}
                if attr.candidates:
                    group_key = (row.sample_type, name, tuple(attr.candidates))
                    group = ambiguous_primary_groups.setdefault(group_key, {
                        "sample_type": row.sample_type, "attribute": name,
                        "chosen": attr.source_file, "candidates": attr.candidates,
                        "sample_count": 0,
                    })
                    group["sample_count"] += 1

            if row.uid:
                meta["UID"] = row.uid

            row_plan = _assay_plan(row, len(rows_by_type.get(row.sample_type, [])),
                                   meta.get("Parent"))
            if row_plan is not None:
                assay_plans_by_type.setdefault(row.sample_type, []).append(row_plan)
            row_dict = {"json_metadata": meta,
                        "assay_ids": row_plan.assay_ids if row_plan is not None else [],
                        "provenance": provenance_entry}
            rows_by_type.setdefault(row.sample_type, []).append(row_dict)
            mapped_by_type.setdefault(row.sample_type, []).append(row)
            if parked_values:
                dseq_parked.append((row_dict, parked_values, parked_attrs))

            provenance_by_type.setdefault(row.sample_type, []).extend(
                {"uid": row.uid or "", "attribute": a.attribute, "value": a.value,
                 "origin": (mapper.ORIGIN_PARKED if n in parked_values else a.origin),
                 "raw_key": a.raw_key, "source_file": a.source_file,
                 "answered_by": a.answered_by}
                for n, a in row.attributes.items())

        # Existing Notes for D.SEQ's update-mode rows, fetched AT MOST ONCE: the
        # `want_update` guard above means `rows_by_type` can only ever carry a
        # "D.SEQ" key when mode == MODE_UPDATE (every other sample type is
        # `continue`d past whenever mode == MODE_UPDATE), so the Notes-composition
        # step below (parked values) and qa_rows' own NOTES_WOULD_CLOBBER guard
        # both need Notes for exactly the same row set -- `rows_by_type["D.SEQ"]`.
        # Querying it twice was not just wasted work: a transient blip on the
        # SECOND fetch would drop a UID the first fetch had just proved readable,
        # turning it into a spurious NOTES_WOULD_CLOBBER hard reject for no
        # reason. One fetch, reused both places, closes that window.
        dseq_existing_notes: dict[str, str] | None = None
        if mode == MODE_UPDATE and rows_by_type.get("D.SEQ"):
            if "notes" not in notes_once:
                notes_once["notes"] = _existing_notes(rows_by_type["D.SEQ"])
            dseq_existing_notes = notes_once["notes"]

        # Compose Notes for every D.SEQ row carrying a parked value, gated by the
        # same existing-Notes fetch the NOTES_WOULD_CLOBBER guard uses: a sample
        # whose Notes could not be fetched gets no note written at all, per the
        # design's clobbering guard (section 8) -- guessing here would risk
        # destroying curator text deep_merge_metadata would overwrite wholesale.
        # The needs_definition proposal is queued regardless: the schema gap is
        # real whether or not THIS run could safely write it into Notes, and a
        # superuser defining the attribute is what stops the parking, not a
        # successful Notes fetch.
        if dseq_parked:
            existing_notes = dseq_existing_notes or {}
            for row_dict, parked_values, parked_attrs in dseq_parked:
                uid = row_dict["json_metadata"].get("UID")
                if uid in existing_notes:
                    row_dict["json_metadata"]["Notes"] = compose_notes(
                        existing_notes[uid], run_name, parked_values, today)
                for name, attr in parked_attrs.items():
                    needs_definition.append({
                        "raw_key": attr.raw_key, "proposed_attribute": name,
                        # Parking only ever happens on the D.SEQ backfill branch
                        # (the `want_update` guard above), so the target sample
                        # type is always D.SEQ here -- never the stale loop `row`
                        # from the (already-exited) row-construction loop above.
                        "proposed_target": "D.SEQ", "datatype": "string",
                        "example_value": attr.value, "source_file": attr.source_file,
                        "rationale": "mapped by reingest but not defined on D.SEQ (parked in Notes)",
                        "status": "needs_definition",
                    })

        reports_by_type = {}
        schema_fields_by_type: dict[str, list[str]] = {}
        for sample_type, type_rows in rows_by_type.items():
            attrs = attributes_for(sample_type)
            schema_fields_by_type[sample_type] = [a["title"] for a in attrs]
            required = [a["title"] for a in attrs if a.get("required")]
            # See the legacy-rows path above for why the fallback default is the
            # entry's own "required" rather than False.
            server_required = [a["title"] for a in attrs if a.get("server_required", a.get("required"))]
            built = qa_rows(type_rows, sample_type=sample_type, known_sampletypes=known,
                            required_fields=required, server_required_fields=server_required,
                            mode=mode,
                            existing_parent_uids=existing_parent_uids,
                            existing_notes=dseq_existing_notes if mode == MODE_UPDATE else None,
                            run_name=run_name)
            # Every attribute on this sample type's rows may have been parked into
            # Notes (attribute_exists said none of them are defined on the
            # schema) -- if the Notes fetch above also failed for every one of
            # these UIDs, no row even got a Notes column, and json_metadata is
            # left holding nothing but UID. render_upload_workbook refuses to
            # render that ("rows carry no json_metadata"): catch the same
            # condition here as a QA hard-reject instead of letting that
            # ValueError escape past this op's own VALIDATION/WRITE_BLOCKED/
            # AGENT_FAILED taxonomy as an opaque 502.
            if not any(set(row.get("json_metadata") or {}) - {"UID"} for row in type_rows):
                built.add(Finding(code=NO_ATTRIBUTES_TO_WRITE, severity=HARD,
                                  sample_type=sample_type))
                built._finalize()
            plans = assay_plans_by_type.get(sample_type, [])
            for row_plan in plans:
                for finding in row_plan.findings:
                    built.add(finding)
            if assay_plan_mod.split_across_studies(plans):
                built.add(Finding(code=ASSAY_SPLIT_ACROSS_STUDIES, severity=HARD,
                                  sample_type=sample_type))
            parent_pairs = assay_plan_mod.merge_parent_assays(
                [p for row_plan in plans for p in row_plan.parent_assays])
            if parent_pairs:
                built.add(Finding(code=PARENTS_JOIN_ASSAY, severity=SOFT, sample_type=sample_type,
                                  detail={"parents": len({p["parent_uid"] for p in parent_pairs}),
                                          "assays": sorted({p["internal_assay_title"]
                                                            for p in parent_pairs})}))
            built._finalize()
            reports_by_type[sample_type] = built
        return SimpleNamespace(
            ambiguous_primary_groups=ambiguous_primary_groups,
            provenance_by_type=provenance_by_type, rows_by_type=rows_by_type,
            mapped_by_type=mapped_by_type, needs_definition=needs_definition,
            reports_by_type=reports_by_type, schema_fields_by_type=schema_fields_by_type,
            assay_plans_by_type=assay_plans_by_type)

    def _existing_values(uids: list[str], attribute: str) -> dict:
        """What a place would overwrite: the samples' current SEEK values.

        Strict: an outage raises, and answers.check_place refuses the place on
        any exception. The driver text stays in the server log."""
        from nextseek_api.services.reingest_lookups import attribute_values_for_uids_strict
        try:
            return attribute_values_for_uids_strict(uids, attribute)
        except Exception:
            logger.warning("reingest: existing-values lookup failed for %s", attribute,
                           exc_info=True)
            raise

    first = _assemble(result.rows)
    answers_deferred: list[dict] = []
    if answers.is_empty():
        final = first
    else:
        this_call = set(first.mapped_by_type)
        other_call = {r.sample_type for r in result.rows} - this_call
        user_label = (full_name or getattr(user, "username", "") or "unknown curator")
        findings_by_type: dict[str, list] = {}
        try:
            answers, answers_deferred = answers_mod.split_for_call(
                answers, this_call=this_call, other_call=other_call)
            findings_by_type = {st: rep.findings for st, rep in first.reports_by_type.items()}
            answers_mod.validate(
                answers, findings_by_type=findings_by_type,
                mapped_by_type=first.mapped_by_type, unmapped=result.unmapped,
                groups=list(first.ambiguous_primary_groups.values()),
                run_sourced_for=lambda st: answers_mod.run_sourced_attributes(
                    effective_map, approved, st),
                attribute_exists=_attribute_exists,
                existing_values=_existing_values)
        except answers_mod.AnswerRejected as exc:
            raise OpValidationError(str(exc), field="answers", error_type="rejected") from exc
        answers_mod.apply_answers(
            answers, mapped_by_type=first.mapped_by_type,
            findings_by_type=findings_by_type, run_manifest=run_manifest,
            answered_by=f"{user_label} on {today}")
        final = _assemble(result.rows)

    def _project() -> tuple[int | None, str]:
        try:
            ids = project_ids_for_uids_strict(sorted(existing_parent_uids))
        except RuntimeError as exc:
            logger.warning("reingest: project lookup failed: %s", exc)
            return None, "project lookup failed (catalog unreachable)"
        if len(ids) == 1:
            return ids[0], ""
        if not ids:
            return None, "the parent samples belong to no project"
        return None, f"the parent samples span {len(ids)} projects ({ids}); upload one project at a time"

    project_id, project_note = _project()
    user_id = getattr(user, "pk", None)
    # The chat turn this build runs in, as the host sees it; upload-reingest
    # uploads the build only from a later turn of that chat.
    lookup = getattr(session, "chat_turn", None)
    chat_turn = lookup() if callable(lookup) else None
    builds: list[dict] = []

    saved_files, qa = {}, {}
    reports_by_type = final.reports_by_type
    for sample_type, built in reports_by_type.items():
        qa[sample_type] = {"disposition": built.disposition, "hard": built.hard,
                           "soft": built.soft}
        if built.disposition == HARD_REJECT:
            continue
        type_rows = final.rows_by_type[sample_type]
        safe_name = sample_type.replace("/", "_").replace(" ", "_")
        suffix = "_update" if mode == MODE_UPDATE else ""
        safe_key = f"reingest_{safe_name}{suffix}".replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}{suffix}.xlsx")
        render_upload_workbook(sample_type, type_rows, path, mode=mode,
                               provenance=final.provenance_by_type.get(sample_type),
                               schema_fields=final.schema_fields_by_type.get(sample_type))
        saved_files[safe_key] = path
        builds.append(build_records.write(
            path=path, artifact_key=safe_key, sample_type=sample_type, mode=mode,
            manifest_id=manifest_id, disposition=built.disposition,
            open_warnings=built.soft, row_count=len(type_rows),
            project_id=project_id, project_note=project_note,
            answers_digest=answers.digest(), user_id=user_id,
            test_only=test_only, chat_turn=chat_turn,
            answers=answers.model_dump(),
            assays_to_create=assay_plan_mod.merge_to_create(
                [e for p in final.assay_plans_by_type.get(sample_type, []) for e in p.to_create]),
            parent_assays=assay_plan_mod.merge_parent_assays(
                [e for p in final.assay_plans_by_type.get(sample_type, [])
                 for e in p.parent_assays]),
            rebuilt_from=rebuilt_from))

    # A HARD_REJECT sample type's own workbook was skipped above (the
    # `continue` two lines up), so asking the reader to confirm a
    # primary-file pick inside a workbook that was never produced would be
    # nonsensical on a blocked run. Every group's `sample_type` is known by
    # now -- `reports_by_type` was just populated for every key
    # `ambiguous_primary_groups` could possibly hold, since both are built
    # from the same `rows_by_type` sample-type set.
    ambiguous_primary = [
        group for group in final.ambiguous_primary_groups.values()
        if reports_by_type[group["sample_type"]].disposition != HARD_REJECT
    ]

    # Surface 1 of the superuser surfaces (design doc section 10): the
    # genuinely unmapped raw keys ride out in the same artifact bundle as the
    # workbooks, so whoever ran the reingest sees exactly what the mapper
    # could not place, immediately. This must join `saved_files` BEFORE
    # render_qa_for_user runs below, or the verbatim user-facing reply it
    # builds is composed from a saved_files snapshot that does not yet
    # mention this artifact.
    if result.unmapped:
        proposals_path = os.path.join(out_root, f"map_proposals_{_slug(pipeline)}.md")
        with open(proposals_path, "w", encoding="utf-8") as handle:
            handle.write(f"# Proposed sample attributes — {pipeline}\n\n")
            handle.write(f"Run: {run_manifest.run_dir}\n\n")
            handle.write("| Raw key | Example value | Example sample | Source file |\n")
            handle.write("|---|---|---|---|\n")
            for entry in result.unmapped:
                handle.write(f"| `{entry['raw_key']}` | {entry['example_value']} | "
                             f"{entry.get('example_sample', '')} | "
                             f"`{entry['source_file']}` |\n")
        saved_files[f"map_proposals_{_slug(pipeline)}"] = proposals_path

    reply = user_report.render_qa_for_user(
        reports_by_type, saved_files, run_name, ambiguous_primary=ambiguous_primary,
        row_counts={st: len(rows) for st, rows in final.rows_by_type.items()},
        unmapped_count=len(result.unmapped or []), missed_rules=result.missed_rules,
        already_present=already_present, path_changed=path_changed)
    # Map notes (an unchecked release, a companion with nowhere to go, values
    # with no home on the parent's type) are for whoever maintains the map; the
    # user can act on none of them, so they go to the server log, not the reply.
    # A missed required rule still reaches the user, as its blocker.
    for note in result.warnings:
        _map_notes_log.warning("reingest map note: %s (pipeline %s %s, run %s, manifest %s)",
                               note, pipeline, run_manifest.pipeline.version, run_name,
                               manifest_id)
    if test_only:
        reply = ("TEST ONLY: this run wasn't launched by Nessie. "
                 "These workbooks can't be uploaded.\n\n" + reply)

    # A key the curator placed is still proposed: the placement covers this
    # run only, and making it a standing map rule is an admin's call.
    placed = {p.raw_key: p for p in answers.place}
    pending = [
        {**entry,
         "proposed_attribute": placed[entry["raw_key"]].attribute if entry["raw_key"] in placed else "",
         "proposed_target": placed[entry["raw_key"]].sample_type if entry["raw_key"] in placed else "",
         **({"rationale": f"placed by a curator for run {run_name} only; needs admin review"}
            if entry["raw_key"] in placed else {}),
         "pipeline": pipeline, "manifest_digest": manifest_id,
         "run_dir": run_manifest.run_dir}
        for entry in result.unmapped
    ] + [
        {**entry, "pipeline": pipeline, "manifest_digest": manifest_id,
         "run_dir": run_manifest.run_dir}
        for entry in final.needs_definition
    ]

    return {
        "saved_files": saved_files,
        "qa": qa,
        "reply": reply,
        "proposals": pending,
        "answers_deferred": answers_deferred,
        "builds": builds,
    }


def _upload_reingest(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
    """Upload reviewed reingest workbooks. The gate fires first, before any
    lookup: only the boolean True confirms (passed through, never coerced).

    ``session.upload_context`` may be a callable (the REST layer passes the
    SEEK identity lookup that way), so an unconfirmed call never resolves it.
    The host also puts its batch-upload seams on the session
    (``dispatch_job``, ``stage_workbook``), so the engine never imports them, and
    its chat-turn lookups (``chat_turn``, ``turn_delivered``), so a build is
    uploaded only from a later turn of the chat it was shown in."""
    write_gate("api-write", None, None, args.get("confirmed_write"))
    from NessieAI.ns.reingest import upload

    # The job wait is cut to what is left of the op's limit (upload._wait_budget_s).
    started_at = upload._clock()
    _settle_late_uploads(session, config)
    upload_context = getattr(session, "upload_context", None)
    if callable(upload_context):
        upload_context = upload_context()
    try:
        result = upload.run(build_ids_raw=args.get("build_ids"),
                            user=getattr(session, "user", None),
                            upload_context=upload_context,
                            dispatch=getattr(session, "dispatch_job", None),
                            stage=getattr(session, "stage_workbook", None),
                            chat_turn=getattr(session, "chat_turn", None),
                            turn_delivered=getattr(session, "turn_delivered", None),
                            job_state=getattr(session, "job_state", None),
                            create_study_assay=getattr(session, "create_study_assay", None),
                            register_parents=getattr(session, "register_parents", None),
                            limit_s=limit_s, started_at=started_at,
                            rebuild=lambda record: _rebuild_record(
                                record, getattr(session, "user", None), outputs_dir))
    except upload.UploadRefused as exc:
        raise OpValidationError(str(exc), field="build_ids", error_type="upload_refused") from exc
    _mark_runs_reingested(result.get("jobs") or [], config)
    return result


def _rebuild_record(record: dict, user, outputs_dir) -> list[dict]:
    """Build a reviewed record's run again, after the upload created its study
    assays. Same manifest, mode and answers, as the same user, stamped as shown
    where the reviewed one was, into its own folder so the reviewed workbook
    stays on disk for the comparison."""
    from types import SimpleNamespace as _NS

    from NessieAI.ns.artifacts import default_outputs_dir

    # Absolute, so the upload's containment check finds it whatever the working directory.
    base = os.path.abspath(outputs_dir) if outputs_dir else default_outputs_dir()
    folder = os.path.join(base, "rebuilds", record["build_id"][:16])
    os.makedirs(folder, exist_ok=True)
    shown = record.get("shown_in")
    result = _build_upload_xlsx_from_manifest(
        {"manifest_id": record["manifest_id"], "mode": record["mode"],
         "answers": record.get("answers")},
        folder, session=_NS(user=user, chat_turn=lambda: shown),
        rebuilt_from=record["build_id"])
    return list(result.get("builds") or [])


def _mark_runs_reingested(jobs: list[dict], config=None) -> None:
    """A run whose upload had at least one job succeed stops waiting for run-harvest's
    no-folder pick, and its Luria folder gets nessie_status/saved.txt, which starts the
    weekly cleanup's 7-day clock for its work folder. Best-effort: the upload has already
    started."""
    from NessieAI.ns.reingest import launch_record, upload
    from NessieAI.ns.reingest.store import load_manifest

    succeeded = {job.get("manifest_id") for job in jobs
                 if job.get("job_id") and upload.job_is_done(job.get("outcome"))}
    for manifest_id in succeeded:
        try:
            manifest = load_manifest(manifest_id)
            launch_record.mark_reingested(manifest.launch_record_id)
        except Exception:  # noqa: BLE001 -- bookkeeping never fails an upload
            logger.exception("upload-reingest: could not mark manifest %s reingested", manifest_id)
            continue
        _write_saved_label(config, manifest)


def _settle_late_uploads(session, config=None) -> None:
    """Finish the bookkeeping of the caller's upload jobs that outlived their call (PR 135 F1): a job
    now done stamps its build uploaded, takes its run off the waiting pick and writes saved.txt, as
    ``_mark_runs_reingested`` does for a job the call saw finish. Uses the host's ``job_state`` seam on the
    session; without one, or on any failure, nothing changes. Best-effort: never fails the op."""
    job_state = getattr(session, "job_state", None)
    user_id = getattr(getattr(session, "user", None), "pk", None)
    if not callable(job_state) or not user_id:
        return
    from NessieAI.ns.reingest import upload

    try:
        done = upload.settle_waiting(user_id, job_state)
    except Exception:  # noqa: BLE001 -- bookkeeping never fails the op
        logger.exception("reingest: could not settle late upload jobs")
        return
    _mark_runs_reingested(done, config)


def _start_background(fn: Callable, *args) -> None:
    """Run `fn(*args)` on a daemon thread and return at once; the one seam tests replace to
    run it inline."""
    import threading

    threading.Thread(target=fn, args=args, daemon=True, name="nessie-saved-label").start()


def _write_saved_label(config, manifest) -> None:
    """Best-effort, like mark_reingested: when this fails, the run's work folder waits 30
    days instead of 7 (chat_nextseek/luria/README.md). The SSH write runs on a background
    thread so the upload request does not wait for Luria."""
    _start_background(_write_saved_label_now, getattr(config, "LURIA_ENV", None) or {},
                      getattr(manifest, "run_dir", None), getattr(manifest, "launch_record_id", None))


def _write_saved_label_now(luria_env, run_dir, launch_record_id) -> None:
    # Runs on a background thread: write_saved_label only uses SSH, never the database, so
    # there is no Django connection to manage here.
    try:
        from chat_nextseek.luria.status_labels import write_saved_label

        write_saved_label(luria_env, run_dir, launch_record_id)
    except Exception:  # noqa: BLE001 -- bookkeeping never fails an upload
        logger.exception("upload-reingest: could not write saved.txt for %s", run_dir or "?")


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
    "run-harvest": _run_harvest,
    "run-checksum": _run_checksum,
    "upload-reingest": _upload_reingest,
}
