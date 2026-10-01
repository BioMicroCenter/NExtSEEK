"""13b.2: nextseek-query stops polling before the host stops the turn it runs in.

The poll deadline was a constant 300 s while the host stops a Container-CC turn
at 180 s by default, so a long query was cut off with the whole turn and the
agent never got the chance to say so. The host now hands the agent the moment
its turn will be stopped (NEXTSEEK_CC_TURN_DEADLINE_EPOCH, Unix seconds), and
the poll budget is the time left before it, less headroom for the agent to act
on a timed-out query and finish its turn.
"""
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _assistant_client as ac  # noqa: E402

NOW = 1_800_000_000.0
HEADROOM = ac._TURN_DEADLINE_HEADROOM_S
TASK = "22222222-2222-2222-2222-222222222222"
SESSION = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _frozen_wallclock(monkeypatch):
    monkeypatch.setattr(ac, "_wallclock", lambda: NOW)
    monkeypatch.delenv(ac._TURN_DEADLINE_ENV, raising=False)


def _turn_ends_in(monkeypatch, seconds):
    monkeypatch.setenv(ac._TURN_DEADLINE_ENV, str(int(NOW + seconds)))


def test_budget_is_the_time_left_in_the_turn_less_headroom(monkeypatch):
    _turn_ends_in(monkeypatch, 180)
    assert ac.poll_timeout_from_env() == 180 - HEADROOM


def test_budget_counts_the_part_of_the_turn_already_spent(monkeypatch):
    """A query issued 100 s into a 180 s turn has 80 s, not 180 s."""
    _turn_ends_in(monkeypatch, 80)
    assert ac.poll_timeout_from_env() == 80 - HEADROOM


def test_headroom_outlasts_one_progress_get_that_overruns_the_deadline():
    """The loop checks the deadline BEFORE each GET, so one GET started just in
    time can run a full request_timeout past it."""
    request_timeout = ac.AssistantClient(
        base_url="http://t", assistant_prefix="p", auth=("u", "p"))._request_timeout
    assert HEADROOM > request_timeout


def test_budget_is_never_below_the_floor(monkeypatch):
    _turn_ends_in(monkeypatch, 5)
    assert ac.poll_timeout_from_env() == ac._MIN_POLL_TIMEOUT_S


@pytest.mark.parametrize("raw", [None, "", "soon", "nan", "inf", "-inf"])
def test_an_absent_or_unreadable_deadline_falls_back_below_the_default_ceiling(
        monkeypatch, raw):
    if raw is not None:
        monkeypatch.setenv(ac._TURN_DEADLINE_ENV, raw)
    assert ac.poll_timeout_from_env() == ac._FALLBACK_POLL_TIMEOUT_S
    assert ac._FALLBACK_POLL_TIMEOUT_S < 180


def _never_finishing_task():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query/async/"):
            return httpx.Response(202, json={"task_id": TASK, "session_id": SESSION})
        return httpx.Response(200, json={
            "task_id": TASK, "session_id": SESSION, "status": "running",
            "progress": [], "result": None,
        })
    return httpx.MockTransport(handler)


@pytest.fixture
def clock(monkeypatch):
    """A simulated clock: every poll-interval sleep advances both clocks."""
    state = {"t": 0.0}
    monkeypatch.setattr(ac, "_monotonic", lambda: state["t"])
    monkeypatch.setattr(ac, "_wallclock", lambda: NOW + state["t"])
    monkeypatch.setattr(ac, "_sleep", lambda s: state.__setitem__("t", state["t"] + s))
    return state


def _client(**kwargs):
    return ac.AssistantClient(
        base_url="http://t", assistant_prefix="nextseek_api/assistant",
        auth=("u", "p"), transport=_never_finishing_task(), **kwargs)


def test_run_query_gives_up_before_the_turn_is_stopped(monkeypatch, clock):
    _turn_ends_in(monkeypatch, 180)

    terminal, _events = _client().run_query("q", mode="standard")

    assert terminal == {"__error__": ac.STREAM_ENDED_SENTINEL, "agent": None}
    assert 180 - HEADROOM <= clock["t"] <= 180 - HEADROOM + ac._DEFAULT_POLL_INTERVAL, (
        "polling must end with the headroom still left in the turn, not run on "
        "into the moment the host stops the container"
    )


def test_run_query_without_a_deadline_uses_the_fallback(clock):
    terminal, _events = _client().run_query("q", mode="standard")

    assert terminal["__error__"] == ac.STREAM_ENDED_SENTINEL
    assert clock["t"] <= ac._FALLBACK_POLL_TIMEOUT_S + ac._DEFAULT_POLL_INTERVAL


def test_an_explicit_timeout_still_wins(monkeypatch, clock):
    _turn_ends_in(monkeypatch, 180)

    _client(timeout=7.0).run_query("q", mode="standard")

    assert 7.0 <= clock["t"] <= 7.0 + ac._DEFAULT_POLL_INTERVAL
