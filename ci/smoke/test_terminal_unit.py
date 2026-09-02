"""Unit tests for the suite's terminal reporting. No stack, no network.

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      --with playwright pytest ci/smoke/test_terminal_unit.py -q -p no:cacheprovider

A session fixture runs inside the first test's setup phase, where pytest's output
capture is on. On a pipe the terminal reporter's line slips through; on a real
terminal it goes into the capture buffer and is discarded with the passing test.
That is why a five-minute readiness floor looked like a hang: every progress
line the fixture wrote was invisible. These tests pin the two things that fix
it: the write happens with capture suspended, and the floor is a countdown.
"""
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.conftest import report_to_terminal, wait_out_floor


class FakeReporter:
    def __init__(self):
        self.lines = []
        self.captured_when_written = []

    def write_line(self, line):
        self.lines.append(line)


class FakeCaptureManager:
    """Records whether capture was suspended at the moment of each write."""

    def __init__(self, reporter):
        self.reporter = reporter
        self.suspended = False

    @contextmanager
    def global_and_fixture_disabled(self):
        self.suspended = True
        try:
            yield
        finally:
            self.suspended = False


class FakePluginManager:
    def __init__(self, plugins):
        self._plugins = plugins

    def get_plugin(self, name):
        return self._plugins.get(name)

    getplugin = get_plugin


class FakeConfig:
    def __init__(self, plugins):
        self.pluginmanager = FakePluginManager(plugins)


def _config(reporter=None, capman=None):
    plugins = {}
    if reporter is not None:
        plugins["terminalreporter"] = reporter
    if capman is not None:
        plugins["capturemanager"] = capman
    return FakeConfig(plugins)


# --------------------------------------------------------------------------- #
# report_to_terminal
# --------------------------------------------------------------------------- #

def test_the_line_is_written_while_capture_is_suspended():
    reporter = FakeReporter()
    capman = FakeCaptureManager(reporter)
    # Make the reporter observe the capture state at write time.
    original = reporter.write_line

    def observing_write(line):
        reporter.captured_when_written.append(capman.suspended)
        original(line)

    reporter.write_line = observing_write

    report_to_terminal(_config(reporter, capman), "[readiness] hello")

    assert reporter.lines == ["[readiness] hello"]
    assert reporter.captured_when_written == [True]
    assert capman.suspended is False, "capture must be restored afterwards"


def test_without_a_capture_manager_the_line_is_still_written():
    reporter = FakeReporter()
    report_to_terminal(_config(reporter, None), "[readiness] hello")
    assert reporter.lines == ["[readiness] hello"]


def test_without_a_reporter_nothing_is_raised():
    report_to_terminal(_config(None, None), "[readiness] hello")


# --------------------------------------------------------------------------- #
# wait_out_floor
# --------------------------------------------------------------------------- #

def test_the_floor_is_slept_in_steps_and_announced_after_each_full_step():
    slept, said = [], []
    wait_out_floor(75, say=said.append, sleep=slept.append, step=30)
    assert slept == [30, 30, 15]
    assert said == ["floor: 45s remaining", "floor: 15s remaining"]


def test_a_floor_shorter_than_one_step_sleeps_once_and_says_nothing():
    slept, said = [], []
    wait_out_floor(12, say=said.append, sleep=slept.append, step=30)
    assert slept == [12]
    assert said == []


def test_a_zero_floor_neither_sleeps_nor_speaks():
    slept, said = [], []
    wait_out_floor(0, say=said.append, sleep=slept.append, step=30)
    assert slept == []
    assert said == []


def test_the_total_slept_always_equals_the_floor():
    for floor in (1, 29, 30, 31, 59, 60, 61, 300):
        slept = []
        wait_out_floor(floor, say=lambda _: None, sleep=slept.append, step=30)
        assert sum(slept) == floor, floor
