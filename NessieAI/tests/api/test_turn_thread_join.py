"""A turn's daemon thread is joined at teardown, so the next test never flushes tables under it.

The two tests run in file order: the first leaves a thread that is still saving when it returns (as a
turn does after its task row is terminal); the second proves the teardown waited for it. Without the
autouse fixture in ``nextseek_api/conftest.py`` the second fails.
"""
import threading
import time

import pytest

from nextseek_api.conftest import join_turn_threads

_state = {"saved": False}


def _late_save():
    time.sleep(0.3)
    _state["saved"] = True


_late_save.__module__ = "NessieAI.cc.turn"   # what the fixture recognizes a turn thread by


def test_a_turn_thread_is_still_saving_when_its_test_returns():
    threading.Thread(target=_late_save, daemon=True).start()
    assert _state["saved"] is False


def test_the_next_test_sees_the_earlier_turn_thread_finished():
    assert _state["saved"] is True


def test_a_turn_thread_that_outlives_the_timeout_fails_the_teardown():
    stop = threading.Event()

    def _stuck():
        stop.wait(5)

    _stuck.__module__ = "NessieAI.ns.turn"
    threading.Thread(target=_stuck, daemon=True).start()
    try:
        with pytest.raises(AssertionError, match="still running"):
            join_turn_threads(timeout=0.05)
    finally:
        stop.set()


def test_a_thread_alive_before_the_test_is_ignored():
    stop = threading.Event()

    def _leaked():
        stop.wait(5)

    _leaked.__module__ = "NessieAI.cc.turn"
    leaked = threading.Thread(target=_leaked, daemon=True)
    leaked.start()
    try:
        join_turn_threads(timeout=0.05, ignore={leaked})   # what the fixture passes: the threads it saw at setup
        with pytest.raises(AssertionError, match="still running"):
            join_turn_threads(timeout=0.05)
    finally:
        stop.set()
