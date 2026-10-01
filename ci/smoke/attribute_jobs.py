"""Wait for an attribute batch mutation to finish before asserting on it.

/nextseek_api/attributes/batch-{create,patch,delete}/ runs synchronously only
while the affected sample rows stay under ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD
(5,000 by default, dmac/settings.py). Above it the service answers 202 with a
status_url and a worker applies the change later. A caller that reads the
attribute list straight after a 202 is reading the state before the write.

Measured on fairdata-dev 2026-09-22: TIS holds 53,091 samples there, so every
create on it is asynchronous; the jobs succeeded 12 to 45 s after acceptance,
and the write lane, which searched at once, reported "was not created".
"""
from __future__ import annotations

import time
from typing import Any, Callable

JOB_PATH_PREFIX = "/nextseek_api/attributes/jobs/"
NONTERMINAL = frozenset({"queued", "running"})


class JobNotSettled(AssertionError):
    """The job did not reach a terminal state, or its 202 could not be followed."""


def settle(
    session: Any,
    base_url: str,
    response: Any,
    *,
    timeout_s: float = 300.0,
    interval_s: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, Any]:
    """The mutation's final (status code, body).

    A non-202 answer is already final and comes back untouched. A 202 is followed
    to its status_url and polled until the job leaves queued/running; the job
    document is returned with the poll's status code. Raises JobNotSettled when
    the deadline passes first, so a stuck worker fails the test by name instead
    of surfacing later as a missing attribute.
    """
    if response.status_code != 202:
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text
    body = response.json()
    status_url = str((body or {}).get("status_url") or "")
    if not status_url.startswith(JOB_PATH_PREFIX):
        raise JobNotSettled(f"202 carried no usable status_url: {status_url!r}")

    deadline = clock() + timeout_s
    state = None
    while True:
        r = session.get(base_url + status_url, timeout=60)
        if r.status_code == 200:
            job = r.json()
            state = job.get("state")
            if state not in NONTERMINAL:
                return r.status_code, job
        else:
            state = f"HTTP {r.status_code}"
        if clock() + interval_s > deadline:
            raise JobNotSettled(
                f"attribute job {body.get('job_id')} still {state} after {timeout_s:.0f} s")
        sleep(interval_s)
