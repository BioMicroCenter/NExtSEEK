"""Unit tests for ci/smoke/attribute_jobs.py. No stack, no network.

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      pytest ci/smoke/test_attribute_jobs_unit.py -q -p no:cacheprovider

The rule under test: a batch mutation the service accepted with 202 has not
happened yet. On fairdata-dev (2026-09-22) the write lane asserted on TIS right
after a 202, while the job it had queued (TIS holds 53,091 samples, over the
5,000-row synchronous threshold) was still waiting for a worker, so a create
that succeeded 12 to 45 s later read as "was not created".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from ci.smoke.attribute_jobs import JobNotSettled, settle

BASE = "http://box"
JOB = "123e4567-e89b-12d3-a456-426614174000"
STATUS_URL = f"/nextseek_api/attributes/jobs/{JOB}/"


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _Session:
    """Answers each GET with the next queued job document and records the URLs."""

    def __init__(self, states):
        self.states = list(states)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        state = self.states.pop(0)
        return _Resp(200, {"job_id": JOB, "state": state,
                           "result": {"status": state} if state not in ("queued", "running") else None})


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def _accepted():
    return _Resp(202, {"mode": "asynchronous", "job_id": JOB, "status_url": STATUS_URL, "counts": {}})


def test_a_synchronous_answer_is_returned_untouched():
    session = _Session([])
    done = _Resp(200, {"mode": "synchronous", "overall_status": "succeeded"})
    assert settle(session, BASE, done) == (200, {"mode": "synchronous", "overall_status": "succeeded"})
    assert session.urls == []


def test_an_accepted_job_is_polled_until_it_is_terminal():
    clock = _Clock()
    session = _Session(["queued", "running", "succeeded"])
    code, body = settle(session, BASE, _accepted(), sleep=clock.sleep, clock=clock, interval_s=2)
    assert (code, body["state"]) == (200, "succeeded")
    assert session.urls == [BASE + STATUS_URL] * 3


@pytest.mark.parametrize("terminal", ["partial", "failed", "cancelled"])
def test_every_terminal_state_ends_the_wait(terminal):
    clock = _Clock()
    code, body = settle(_Session(["running", terminal]), BASE, _accepted(), sleep=clock.sleep, clock=clock)
    assert body["state"] == terminal


def test_a_job_that_never_settles_fails_loudly_instead_of_returning():
    clock = _Clock()
    session = _Session(["running"] * 1000)
    with pytest.raises(JobNotSettled, match="running"):
        settle(session, BASE, _accepted(), sleep=clock.sleep, clock=clock, timeout_s=30, interval_s=5)
    assert len(session.urls) <= 8


def test_a_status_url_outside_the_job_route_is_refused():
    bad = _Resp(202, {"mode": "asynchronous", "job_id": JOB, "status_url": "http://elsewhere/x/"})
    with pytest.raises(JobNotSettled, match="status_url"):
        settle(_Session([]), BASE, bad)
