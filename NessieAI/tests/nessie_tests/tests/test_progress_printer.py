"""The live per-case line `manage.py nessie` prints while a paid run is going.

Before this, a full-tier run printed nothing at all until it finished. That is half
an hour or more of real model turns with a silent terminal, no way to tell a working
run from a hung one, and no record whatsoever of the completed cases if the run died
before the manifest was written.

These tests pin the two properties that make the line worth having: it says which
case, how it went and what has been spent, and a failing printer never takes the run
down with it.
"""
from __future__ import annotations

import io

import pytest

from nextseek_api.management.commands.nessie import Command


class _Entry:
    """The manifest-entry fields the printer reads, and no more."""

    def __init__(self, id, status, route="nextseek_query", elapsed_s=1.0, cost=None,
                 failed_criteria=(), expected_fail=False, outage=False):
        self.id, self.status, self.route = id, status, route
        self.elapsed_s, self.cost = elapsed_s, cost
        self.failed_criteria = list(failed_criteria)
        self.expected_fail, self.outage = expected_fail, outage


def _printer():
    cmd = Command()
    cmd.stdout = type(cmd.stdout)(io.StringIO())
    return cmd, cmd._progress_printer()


def _lines(cmd):
    return cmd.stdout._out.getvalue().splitlines()


def test_a_passing_case_prints_its_id_route_and_position():
    cmd, on_case = _printer()
    on_case(1, 44, _Entry("harmon.organ_lung_case_split", "passed", route="nextseek_query",
                          elapsed_s=12.4))
    (line,) = _lines(cmd)
    assert "[  1/44]" in line
    assert "PASS" in line
    assert "harmon.organ_lung_case_split" in line
    assert "nextseek_query" in line
    assert "12.4s" in line


def test_the_cost_accumulates_across_cases():
    """A per-case cost tells you nothing about the budget; the running total does."""
    cmd, on_case = _printer()
    on_case(1, 3, _Entry("a", "passed", cost=0.01))
    on_case(2, 3, _Entry("b", "passed", cost=0.02))
    on_case(3, 3, _Entry("c", "passed", cost=None))  # route-tier: unobservable, not zero
    assert "$0.01" in _lines(cmd)[0]
    assert "$0.03" in _lines(cmd)[1]
    assert "$0.03" in _lines(cmd)[2]


def test_a_failure_shows_the_first_failed_criterion():
    """A bare FAIL while a paid run is going is not actionable; the report is 40 minutes away."""
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("advanced.rna_from_the_kamm_lab", "failed",
                         failed_criteria=["last_reply: no match for 140", "route: eq"]))
    (line,) = _lines(cmd)
    assert "FAIL" in line
    assert "last_reply: no match for 140" in line
    assert "route: eq" not in line, "only the first: the rest are in the report"


def test_an_expected_failure_does_not_read_as_a_red():
    """A known_fail that failed as expected is excluded from the gate, so the running
    tally must not make the run look worse than the summary that follows it."""
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("known.thing", "failed", expected_fail=True))
    assert "xfail" in _lines(cmd)[0]


def test_an_outage_says_so_and_names_the_recovery():
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("x", "error", outage=True))
    line = _lines(cmd)[0]
    assert "ERR" in line
    assert "--resume" in line


@pytest.mark.parametrize("status,mark", [
    ("passed", "PASS"), ("failed", "FAIL"), ("error", "ERR"), ("skipped", "SKIP"),
    ("xpass", "XPASS"), ("no_assertions", "NOASRT"),
])
def test_every_manifest_status_has_a_mark(status, mark):
    """A status with no mark would print the raw enum and read as a harness bug."""
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("x", status))
    assert mark in _lines(cmd)[0]


def test_a_route_that_was_never_decided_prints_a_placeholder():
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("x", "skipped", route=None))
    assert _lines(cmd)[0].count("None") == 0


def test_the_printer_never_takes_the_run_down():
    """run_suite swallows what this raises, but the printer should not raise at all.

    An entry missing a field it expects is a harness-version skew, not a reason to
    lose the paid turns already driven.
    """
    cmd, on_case = _printer()

    class Bare:
        id, status, route, elapsed_s, cost = "x", "passed", None, 0.0, None
        failed_criteria: list = []

    on_case(1, 1, Bare())
    assert _lines(cmd)
