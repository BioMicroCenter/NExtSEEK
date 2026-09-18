"""What every NExtSEEK writer calls after it writes (the spec's sections 4 and 12; CI-6).

A hook writes one row to ``graph_sync_outbox`` and returns: no request waits on Neo4j. The loop
(``manage.py graph_sync --loop``) drains the row through the same functions the nightly and weekly syncs use.

``enqueue`` never raises into its caller. The writer's own write has already happened, and a failure here must not
undo it or turn a success into an error: it is logged with its traceback, counted per kind (``failure_counts``), and
reported as False. The nightly targeted sync then finds the change the lost row would have carried.

Call it after the writer's commit. Inside a transaction on the dmac (``default``) database the row commits or rolls
back with that transaction, and a failed insert rolls back to its own savepoint, so the caller's transaction stays
usable. The kinds and keys are the spec's section 12 table, checked by ``state.check_item``: ``samples`` with
``sample:<id>`` (or ``batch:<name>`` and the ids as ``payload``), ``samples_of_type`` with ``type:<id>``, ``retire``
with ``sample:<id>``, and ``catalog``, ``assay_map``, ``protocol_map``, ``isa``, ``membership`` with ``*``.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

from nextseek_api.graph_sync import state

log = logging.getLogger(__name__)

_failures: Counter[str] = Counter()
_failures_lock = threading.Lock()


def _count_failure(kind: Any) -> None:
    try:
        name = kind if isinstance(kind, str) else repr(kind)
    except Exception:  # noqa: BLE001
        name = "?"
    with _failures_lock:
        _failures[name] += 1


def enqueue(kind: str, key: str, payload: Any = None, *, delay_s: float = 0) -> bool:
    """``state.enqueue`` that never raises. True when the row was written; False, logged and counted, when not.

    ``delay_s`` keeps the row from any worker for that long: for a writer that cannot tell whether its write has
    landed yet (``state.enqueue``)."""
    try:
        state.enqueue(kind, key, payload, delay_s=delay_s)
        return True
    except Exception:  # noqa: BLE001
        # Swallowed on purpose: this is the one place in graph_sync that does, because the caller's write stands.
        _count_failure(kind)
        log.exception("graph_sync hook could not enqueue %s %s; the nightly targeted sync will find the change",
                      kind, key)
        return False


def failure_counts() -> dict[str, int]:
    """Enqueue failures in this process since it started (or since ``reset_failure_counts``), by kind."""
    with _failures_lock:
        return dict(_failures)


def reset_failure_counts() -> None:
    with _failures_lock:
        _failures.clear()
