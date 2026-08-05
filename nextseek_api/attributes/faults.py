"""Minimal cross-process fault-injection primitive T07 requires.

T07's frozen ``M-TXN-01`` mutant anchor (``MUTATION-ADAPTERS.json``, root-owned,
hash-pinned by ``VERIFICATION-MANIFEST.json``) requires a literal
``attribute_fault("async.during_active_type")`` call site inside
``executor.execute_type_plan``, and T07's own ``executor.*`` fault points
(``M-TXN-01``'s real DB rollback proof among them) require a production-code
hook compatible with the already-merged, test-only
``nextseek_api.attributes.tests.real_boundary.AttributeFaultController``
control-file protocol. Neither T08's ``jobs.py``/``tasks.py`` (the async
orchestration surface that later extends this module with ``async.*``
dispatch points) nor a standalone T07 file existed when this task's mutant
anchor was frozen, so this module is created now, scoped to exactly the
inert, pure primitive both the frozen adapter and T07's own fault points
need. It carries zero Celery/job-store coupling (out of T07's scope) and is
additive: task-08 extends call sites, not this function's contract.

Outside a lane run (``ATTRIBUTE_TEST_FAULT_CONTROL`` unset) this is a no-op,
so it is inert in production.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class InjectedAttributeFault(RuntimeError):
    """Raised when a lane-armed fault point is hit."""


def attribute_fault(point: str) -> None:
    """Hit fault point ``point``.

    Mirrors ``AttributeFaultController``'s control-file protocol exactly
    (mode-0600 JSON at ``ATTRIBUTE_TEST_FAULT_CONTROL``, ``{armed, observed,
    events}``, one exclusive ``flock`` per hit): atomically decrement one
    armed hit for ``point`` and record an observation; raise
    ``InjectedAttributeFault`` only when a hit was actually armed and
    consumed. Outside the disposable test lane this returns immediately.
    The control file is lazily created by the test-side
    ``AttributeFaultController`` (only instantiated by tests that actually
    request the ``attribute_faults`` fixture, e.g. real DB-lane nodes); a
    lane run's ``ATTRIBUTE_TEST_FAULT_CONTROL`` env var can be set with no
    file behind it yet (unit-lane nodes never create it), which must be
    inert here, not a crash.
    """
    raw = os.environ.get("ATTRIBUTE_TEST_FAULT_CONTROL")
    if not raw:
        return
    path = Path(raw)
    if not path.is_file():
        return
    path = path.resolve(strict=True)
    remaining = 0
    with path.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            value = json.load(stream)
            remaining = int(value["armed"].get(point, 0))
            value["observed"][point] = int(value["observed"].get(point, 0)) + 1
            if remaining:
                value["armed"][point] = remaining - 1
                value["events"].append({
                    "point": point,
                    "pid": os.getpid(),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                })
            stream.seek(0)
            stream.truncate()
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
    if remaining:
        raise InjectedAttributeFault(point)
