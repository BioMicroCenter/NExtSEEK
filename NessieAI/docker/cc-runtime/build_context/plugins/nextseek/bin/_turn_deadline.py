"""How long an op may still wait on the server in this Container-CC turn (13b.2). Stdlib only.

The host stops a turn at a fixed moment and hands it to the agent as Unix seconds
(NessieAI/cc/cc_engine.py build_agent_environment, from the turn's own clamped timeout; the
default ceiling is 180 s). A client still waiting at that moment is killed along with the turn,
and the agent never gets to say what happened, so every wait an op makes on the server ends
before it. The assistant client (nextseek-query's polling) and the sidecar client (every
sidecar op, nextseek-graph among them) both take their budget from here.
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
