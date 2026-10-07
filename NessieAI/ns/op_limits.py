"""How long each Container-CC op may run, in Django (approach 1, piece 2; operator rulings 2026-09-28).

``run_op`` takes the op's limit at run time (``limit_s``) and opens the op's ``call_scope`` with it, so every model
call, Neo4j statement and REST call inside the op is cut to the time left (chat_nextseek/call_scope.py
``time_left_for``). The limit is the table's value capped by the turn: ``op_limit_s`` takes the turn's deadline
(piece 4, plan 04); the view passes ``turn_deadline_epoch``.

The tool in the container waits the limit plus 10 s; the plugin's ``bin/_op_road.py`` keeps a copy of the table and
NessieAI/tests/cc/test_op_road_parity.py pins the two together.
"""
from __future__ import annotations

import math

#: Seconds each op may run. graph, aggregate, parse, entity and api-read keep the 55 s they had behind the sidecar;
#: api-write, refused under a turn pass, gets the read ops' 55 s for the callers that may still send it.
OP_LIMITS_S: dict[str, float] = {
    "entity": 55.0,
    "parse": 90.0,
    "graph": 90.0,
    "aggregate": 90.0,
    "api-read": 55.0,
    "api-write": 55.0,
    "generate-submission": 150.0,
    "report": 150.0,
    "graph-schema": 60.0,
    "run-ls": 60.0,
    "build-upload-xlsx": 60.0,
}

#: What an op leaves of the turn for the agent to write its answer.
ANSWER_RESERVE_S = 45.0
#: An op with less than this usable is not started: TIME_UP.
MIN_USABLE_S = 20.0
#: On the sidecar road (NEXTSEEK_CC_OPS_ROAD=sidecar, kept one release) every op keeps the 55 s it had inside the
#: sidecar's 60 s wait (ns-sidecar/app/ns_client.py). Removed with the sidecar.
SIDECAR_ROAD_CAP_S = 55.0


def op_limit_s(op: str, deadline_epoch: float | None, now: float) -> float:
    """Seconds ``op`` may run from ``now`` (Unix seconds): its table value, or less when the turn's deadline less the
    answer reserve comes first. May be zero or negative; the caller refuses anything under ``MIN_USABLE_S``.
    ``KeyError`` for an op that is not in the table."""
    limit = OP_LIMITS_S[op]
    if deadline_epoch is None:
        return limit
    return min(limit, deadline_epoch - now - ANSWER_RESERVE_S)

#: The floor a no-model op keeps late in a turn: the tool's MIN_WAIT_S (operator ruling 2026-09-24).
NO_MODEL_FLOOR_S = 10.0

#: The ops whose server side calls a model: the ones TIME_UP refuses with no usable time left (ruling 3). The tool's
#: own list (plugin bin/_nextseek_runner._MODEL_AGENTS) is this plus query, plan and pipeline; a test pins the two.
MODEL_OPS = frozenset({"entity", "parse", "graph", "aggregate", "api-read", "api-write", "generate-submission"})

#: X-Nextseek-Deadline as Django's request.META names it.
DEADLINE_HEADER_META = "HTTP_X_NEXTSEEK_DEADLINE"


def turn_deadline_epoch(turn, header: str | None) -> float | None:
    """The turn's deadline as Unix time: ``CCTurn.deadline_at``, brought forward by an earlier
    ``X-Nextseek-Deadline`` and never pushed back by a later one. None without a turn deadline: a header alone sets
    nothing."""
    deadline_at = getattr(turn, "deadline_at", None) if turn is not None else None
    if deadline_at is None:
        return None
    server = deadline_at.timestamp()
    try:
        sent = float(str(header).strip()) if header not in (None, "") else None
    except ValueError:
        sent = None
    if sent is None or not math.isfinite(sent):
        return server
    return min(server, sent)


def nested_deadline(turn, header: str | None, now: float) -> tuple[float | None, bool]:
    """``(the Unix time a nested NS turn's model calls must end by, whether it is TIME_UP)``: the turn's deadline less
    ``ANSWER_RESERVE_S`` (Opus still has to use the answer), or ``(None, False)`` without a turn deadline."""
    deadline = turn_deadline_epoch(turn, header)
    if deadline is None:
        return None, False
    until = deadline - ANSWER_RESERVE_S
    return until, (until - now < MIN_USABLE_S)
