"""13b.2 for the sidecar ops: a wait on the sidecar ends before the host stops the turn.

The turn deadline (NEXTSEEK_CC_TURN_DEADLINE_EPOCH) bounded only nextseek-query's polling.
Every sidecar op, nextseek-graph among them and so every sample question, still waited up to
300 s for its answer inside a turn the host stops at 180 s by default: an op started late was
killed with the turn, the user got the bare timeout, and the server finished the work for
nobody. The sidecar client now waits no longer than the time left in the turn, less the same
headroom the assistant client keeps back, and never longer than the 300 s it always had.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _assistant_client as ac  # noqa: E402
import _sidecar_client as sc  # noqa: E402

ENV = "NEXTSEEK_CC_TURN_DEADLINE_EPOCH"
HEADROOM = ac._TURN_DEADLINE_HEADROOM_S
ANSWER = json.dumps({"status": "ok", "result": {"rows": []}})


class _Socket:
    """A sidecar connection that records the timeout of each wait for an answer."""

    def __init__(self, answer=ANSWER):
        self.answer = answer
        self.timeouts = []

    def send(self, payload):
        self.sent = payload

    def recv(self, timeout=None):
        self.timeouts.append(timeout)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    def close(self):
        pass


@pytest.fixture
def socket(monkeypatch):
    sock = _Socket()
    monkeypatch.setattr(sc, "_connect", lambda url: sock)
    monkeypatch.delenv(ENV, raising=False)
    return sock


def _graph():
    return sc.call_op("graph", {"query": "How many mouse samples?"}, ns_login=("u", "p"),
                      sidecar_url="ws://nextseek-sidecar:8765")


def test_a_sidecar_op_waits_no_longer_than_the_turn_has_left(monkeypatch, socket):
    """The engine writes whole seconds, so the wait is the time left less headroom, to within one."""
    monkeypatch.setenv(ENV, str(int(time.time() + 180)))

    assert _graph() == {"rows": []}

    (waited,) = socket.timeouts
    assert 180 - HEADROOM - 1 <= waited <= 180 - HEADROOM


def test_the_wait_is_never_longer_than_it_was(monkeypatch, socket):
    monkeypatch.setenv(ENV, str(int(time.time() + 3600)))
    _graph()
    assert socket.timeouts == [sc._RECV_CEILING_S] and sc._RECV_CEILING_S == 300


@pytest.mark.parametrize("raw", [None, "", "soon", "nan", "inf"])
def test_without_a_readable_deadline_the_wait_stays_at_its_ceiling(monkeypatch, socket, raw):
    """A host older than 13b.2, or the bin run by hand: the finite bound it always had."""
    if raw is not None:
        monkeypatch.setenv(ENV, raw)
    _graph()
    assert socket.timeouts == [sc._RECV_CEILING_S]


def test_an_op_started_late_still_gets_one_short_wait(monkeypatch, socket):
    monkeypatch.setattr(sc, "_wallclock", lambda: 1_800_000_000.0)
    monkeypatch.setenv(ENV, str(1_800_000_000 + 5))
    _graph()
    assert socket.timeouts == [ac._MIN_POLL_TIMEOUT_S]


def test_both_clients_read_one_deadline_and_keep_back_one_headroom(monkeypatch):
    """One helper, so the two waits cannot drift apart."""
    monkeypatch.setattr(sc, "_wallclock", lambda: 1_800_000_000.0)
    monkeypatch.setattr(ac, "_wallclock", lambda: 1_800_000_000.0)
    monkeypatch.setenv(ac._TURN_DEADLINE_ENV, str(1_800_000_000 + 150))
    assert sc.recv_timeout_s() == ac.poll_timeout_from_env() == 150 - HEADROOM


def test_a_sidecar_that_does_not_answer_in_time_is_a_transport_error_naming_the_wait(monkeypatch, socket):
    monkeypatch.setattr(sc, "_wallclock", lambda: 1_800_000_000.0)
    monkeypatch.setenv(ENV, str(1_800_000_000 + 100))
    socket.answer = TimeoutError("timed out while waiting to receive a message")

    with pytest.raises(sc.SidecarCallError) as exc:
        _graph()

    assert exc.value.code == "TRANSPORT_ERROR"
    assert f"{100 - HEADROOM:.0f} s" in exc.value.message
