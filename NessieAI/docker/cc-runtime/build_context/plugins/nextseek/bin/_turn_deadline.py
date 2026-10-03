"""How long an op may still wait on the server in this Container-CC turn (13b.2). Stdlib only.

The host stops a turn at a fixed moment and hands it to the agent as Unix seconds
(NessieAI/cc/cc_engine.py build_agent_environment, from the turn's own clamped timeout; the
default ceiling is 180 s). A client still waiting at that moment is killed along with the turn,
and the agent never gets to say what happened, so every wait an op makes on the server ends
before it. The assistant client (nextseek-query's polling) and the sidecar client (every
sidecar op, nextseek-graph among them) both take their budget from here.
A model op with under MIN_USABLE_S usable is not started at all (``preflight``, piece 4);
MIN_WAIT_S now applies only to the ops that call no model.
"""
from __future__ import annotations

import math
import os

# The engine uses the same name; a test pins the two together.
TURN_DEADLINE_ENV = "NEXTSEEK_CC_TURN_DEADLINE_EPOCH"
# Seconds kept back from the turn deadline: for a wait that overruns its own check (one
# progress GET can run a full 30 s request_timeout past it), and for the agent to act on the
# failed op and finish its turn.
TURN_DEADLINE_HEADROOM_S: float = 45.0
# Never wait less than this, so an op issued late in a turn still gets one short chance.
MIN_WAIT_S: float = 10.0
# Piece 4: under this many seconds usable (deadline - now - TURN_DEADLINE_HEADROOM_S) a model op is not started. The
# server makes the same check (NessieAI/ns/op_limits.MIN_USABLE_S, pinned equal by a test) and is the control.
MIN_USABLE_S: float = 20.0
# The header that tells the server this turn's deadline; the server lets it only shorten its own.
DEADLINE_HEADER = "X-Nextseek-Deadline"


def not_enough_time_message(tool: str) -> str:
    """The refusal a model op gets with no usable time left (operator-approved wording, P03-T4+T12)."""
    return (f"Not enough time left in this turn to run {tool}. Answer from what you already have and say what is "
            "missing.")


def preflight(tool: str, now: float) -> str | None:
    """The refusal text when ``tool`` has under ``MIN_USABLE_S`` usable at ``now`` (Unix seconds), else None; None
    too when the deadline is absent or unreadable (the server decides then)."""
    left = seconds_left(now)
    if left is None:
        return None
    return not_enough_time_message(tool) if left - TURN_DEADLINE_HEADROOM_S < MIN_USABLE_S else None


def deadline_headers() -> dict[str, str]:
    """``{DEADLINE_HEADER: <whole Unix seconds>}`` for this turn's deadline, or ``{}`` when absent or unreadable."""
    raw = os.environ.get(TURN_DEADLINE_ENV, "").strip()
    try:
        deadline = float(raw)
    except ValueError:
        return {}
    return {DEADLINE_HEADER: str(int(deadline))} if math.isfinite(deadline) else {}


def seconds_left(now: float) -> float | None:
    """Seconds from ``now`` (Unix seconds) to the turn's deadline, or None when it is absent or
    unreadable."""
    raw = os.environ.get(TURN_DEADLINE_ENV, "").strip()
    try:
        deadline = float(raw)
    except ValueError:
        return None
    return deadline - now if math.isfinite(deadline) else None


def out_of_turn_message(left_s: float, waited_s: float) -> str:
    """The error text for a wait the turn's deadline cut short: the turn ran out, not the service.

    Operator ruling 2026-09-24: the 10 s floor stays (a longer wait would outlive the turn, and
    the user would get a timeout instead of an answer); the words tell the agent not to retry.
    """
    return (f"This turn was nearly out of time: about {max(0.0, left_s):.0f} s of it were left when "
            f"this op started, so it could wait only {waited_s:.0f} s for the answer, and the answer "
            "did not come in that time. The service did not report an error. Do not retry this op "
            "in this turn: answer now with what you already have, say this step did not finish in "
            "time, and offer to run it in the next turn.")


def no_time_to_retry_message(waited_s: float, service: str = "the sidecar") -> str:
    """The error text for a wait that started with most of the turn left and still got no answer:
    the service was slow, and the turn has no time left for another try."""
    return (f"{service} did not answer within {waited_s:.0f} s, and this turn has no time left for "
            "another try. Do not retry this op in this turn: answer now with what you already have, "
            "say this step did not finish because the service was slow, and offer to run it in the "
            "next turn.")


def wait_s(now: float, *, fallback_s: float, ceiling_s: float = math.inf,
           headroom_s: float = TURN_DEADLINE_HEADROOM_S, floor_s: float = MIN_WAIT_S) -> float:
    """Seconds a client may wait at ``now`` (Unix seconds): the time left in this turn less
    ``headroom_s``, at least ``floor_s`` and at most ``ceiling_s``. ``fallback_s`` when the
    deadline is absent or unreadable: a host older than 13b.2, or the bin run by hand."""
    raw = os.environ.get(TURN_DEADLINE_ENV, "").strip()
    try:
        deadline = float(raw)
    except ValueError:
        return fallback_s
    if not math.isfinite(deadline):
        return fallback_s
    return min(ceiling_s, max(floor_s, deadline - now - headroom_s))
