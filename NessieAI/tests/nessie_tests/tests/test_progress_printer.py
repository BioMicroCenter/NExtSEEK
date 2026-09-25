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
                 failed_criteria=(), expected_fail=False, outage=False, cost_partial=False,
                 fallback_turns=0):
        self.id, self.status, self.route = id, status, route
        self.elapsed_s, self.cost = elapsed_s, cost
        self.failed_criteria = list(failed_criteria)
        self.expected_fail, self.outage = expected_fail, outage
        self.cost_partial, self.fallback_turns = cost_partial, fallback_turns


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
    on_case(3, 3, _Entry("c", "passed", cost=None))  # unobservable, not zero
    assert "run $0.01" in _lines(cmd)[0]
    assert "run $0.03" in _lines(cmd)[1]
    assert "$0.03" in _lines(cmd)[2]


def test_a_case_whose_cost_was_never_observed_is_marked_not_added_as_zero():
    """It used to add nothing and say nothing, so the running total read as the
    whole spend while a turn the harness stopped watching went on billing."""
    cmd, on_case = _printer()
    on_case(1, 2, _Entry("a", "passed", cost=0.01))
    on_case(2, 2, _Entry("b", "passed", cost=None))
    first, second = _lines(cmd)
    assert "case $0.01" in first and "run $0.01" in first
    assert "case $?" in second
    assert "run >=$0.01" in second, "the running total is now a floor and must say so"


def test_a_partial_case_is_marked_and_still_counted():
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("a", "passed", cost=0.42, cost_partial=True))
    (line,) = _lines(cmd)
    assert "case ~$0.42" in line
    assert "run >=$0.42" in line


def test_a_router_only_cost_is_printed_to_the_same_precision_as_the_summary():
    """A route-tier gate's router price is a fraction of a cent. At two places it
    printed an unmarked `$0.00`, the very zero the summary refuses to show."""
    cmd, on_case = _printer()
    on_case(1, 1, _Entry("gate.cc", "passed", cost=0.003, cost_partial=True))
    (line,) = _lines(cmd)
    assert "case ~$0.0030" in line and "run >=$0.0030" in line
    assert "$0.00 " not in line


def test_once_a_floor_always_a_floor():
    """A later fully priced case does not make the running total whole again."""
    cmd, on_case = _printer()
    on_case(1, 2, _Entry("a", "passed", cost=None))
    on_case(2, 2, _Entry("b", "passed", cost=0.10))
    assert "run >=$0.10" in _lines(cmd)[1]


def test_a_skipped_case_sent_nothing_so_the_total_stays_whole():
    cmd, on_case = _printer()
    on_case(1, 2, _Entry("a", "skipped", cost=None))
    on_case(2, 2, _Entry("b", "passed", cost=0.10))
    first, second = _lines(cmd)
    assert "case -" in first and "$?" not in first
    assert "run $0.10" in second and ">=" not in second


def test_a_case_with_a_fallback_turn_says_so():
    cmd, on_case = _printer()
    on_case(1, 2, _Entry("a", "passed", cost=0.1, fallback_turns=2))
    on_case(2, 2, _Entry("b", "passed", cost=0.1))
    first, second = _lines(cmd)
    assert "fallback 2" in first
    assert "fallback" not in second


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


# ── the closing summary ──────────────────────────────────────────────────────

def _entry(vid, cost=None, turns=()):
    from NessieAI.tests.nessie_tests.manifest import NessieManifestEntry, case_money
    money = case_money(list(turns), turns_sent=len(turns)) if turns else {"cost": cost}
    return NessieManifestEntry(id=vid, family="f", tier="full", status="passed", **money)


def _fell_back_turn():
    from NessieAI.tests.nessie_tests.manifest import TurnMeta
    return TurnMeta(turn="t0", engine_cost=0.1, router_cost=0.01, cost=0.11,
                    fallback_reported=True, model_fallback=[
                        {"agent": "graph", "from": "a", "to": "b", "reason": "timeout"}])


def test_the_summary_names_how_many_turns_fell_back_and_where(tmp_path):
    from NessieAI.tests.nessie_tests import runner
    from NessieAI.tests.nessie_tests.manifest import NessieManifest

    m = NessieManifest(started_at="a", ended_at="b", tier="full", scope="all", entries=[
        _entry("cc.fell", turns=[_fell_back_turn()]), _entry("ns.fine", cost=0.2)])
    cmd = Command()
    cmd.stdout = type(cmd.stdout)(io.StringIO())

    cmd._summarize(m, "full", "all", str(tmp_path), runner)

    out = cmd.stdout._out.getvalue()
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("fallback"))
    assert "1 of 1 turn(s) fell back" in line
    assert "f/cc.fell" in out


def test_each_arm_says_how_many_turns_fell_back(tmp_path):
    from NessieAI.tests.nessie_tests import runner
    from NessieAI.tests.nessie_tests.manifest import NessieManifest, TurnMeta

    m = NessieManifest(started_at="a", ended_at="b", tier="full", scope="arm:graph",
                       entries=[_entry("q.one", turns=[_fell_back_turn()])])
    cmd = Command()
    cmd.stdout = type(cmd.stdout)(io.StringIO())

    silent = NessieManifest(started_at="a", ended_at="b", tier="full", scope="arm:api",
                            entries=[_entry("q.one", turns=[TurnMeta(turn="t0", cost=0.1)])])
    cmd._summarize_arms({"progress": {"state": "complete", "turns_driven": 2},
                         "run_meta": {}, "manifests": {"graph": m, "api": silent},
                         "arms_file": str(tmp_path / "arms.json")}, str(tmp_path), runner)

    lines = cmd.stdout._out.getvalue().splitlines()
    graph = next(ln for ln in lines if ln.strip().startswith("arm graph"))
    api = next(ln for ln in lines if ln.strip().startswith("arm api"))
    assert "fallback: 1 of 1 turn(s) fell back" in graph
    assert "fallback: 0 of 1 turn(s) fell back" in api
    assert "1 turn(s) did not report whether they fell back" in api, (
        "a turn that said nothing must not read as no fallback")
