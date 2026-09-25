"""One turn's spend and one case's spend, summed one way.

The bug this module exists to end: `run_case` read a case's cost as
`qc.get("total_cost_usd", v_cost)` on every turn, so the LAST turn won. A case of
three CC turns reported only the third, an NS turn (no key) carried the previous
value forward, and a key present with a null erased an earlier cost. The
2026-09-25 dev run printed $1.57 for one case set that really cost $2.49.

These tests pin the arithmetic on plain payload dicts. The runner, the manifest
and the output skill's pull all call the same functions, so each rule is stated
exactly once.
"""
from __future__ import annotations

import pytest

from NessieAI.tests.nessie_tests import turn_cost as tc


def _rd(**data):
    base = {"route": "container_cc", "model_class": "opus", "source": "baml", "reasoning": ""}
    base.update(data)
    return {"event": "route_decided", "data": base}


def _qc(**data):
    return {"event": "query_complete", "data": {"reply": "ok", **data}}


def _qe(**data):
    return {"event": "query_error", "data": {"error": "x", **data}}


# ── reading one turn ─────────────────────────────────────────────────────────

def test_the_router_fields_come_from_route_decided_and_the_engine_fields_from_the_end():
    payload = {"progress": [
        _rd(router_model="gemini-3.1-pro-preview", router_cost_usd=0.004,
            router_fallback=None, router_cost_partial=False),
        _qc(total_cost_usd=0.42, cost_partial=False,
            models_used=["us.anthropic.claude-opus-4-8"], model_fallback=[]),
    ]}

    t = tc.read_turn(payload)

    assert t["route"] == "container_cc" and t["source"] == "baml"
    assert t["router_cost"] == 0.004 and t["router_model"] == "gemini-3.1-pro-preview"
    assert t["router_fallback"] is None and t["router_cost_partial"] is False
    assert t["engine_cost"] == 0.42 and t["cost_partial"] is False
    assert t["models_used"] == ["us.anthropic.claude-opus-4-8"]
    assert t["model_fallback"] == []
    assert t["fallback_reported"] is True


def test_a_turn_that_ended_on_query_error_still_reports_what_it_carried():
    """A model failure ends the turn on `query_error`, which carries `model_fallback`
    when known. Reading only `query_complete` would hide the very turns a reader of
    fallbacks most needs to see."""
    fb = {"agent": "graph", "from": "gemini-3.5-flash", "to": "us.anthropic.claude-sonnet-4-6",
          "reason": "timeout"}
    payload = {"progress": [_rd(route="nextseek_query", model_class=None),
                            _qe(reason="model_unavailable", model_fallback=[fb])]}

    t = tc.read_turn(payload)

    assert t["model_fallback"] == [fb]
    assert t["engine_cost"] is None


def test_the_last_terminal_event_wins_and_query_complete_beats_query_error():
    payload = {"progress": [_rd(), _qe(total_cost_usd=9.0), _qc(total_cost_usd=0.3)]}
    assert tc.read_turn(payload)["engine_cost"] == 0.3


def test_an_empty_payload_reads_as_nothing_observed():
    t = tc.read_turn({})
    assert t["route"] is None and t["source"] is None
    assert t["engine_cost"] is None and t["router_cost"] is None
    assert t["models_used"] == [] and t["model_fallback"] == []
    assert t["router_fallback"] is None and t["fallback_reported"] is False


@pytest.mark.parametrize("bad", [None, "0.4", True, float("nan"), [0.4], {"usd": 1}])
def test_a_cost_that_is_not_a_finite_number_is_not_observed(bad):
    """`True` is an int in Python; a flag must never be summed as one dollar."""
    payload = {"progress": [_rd(router_cost_usd=bad), _qc(total_cost_usd=bad)]}
    t = tc.read_turn(payload)
    assert t["engine_cost"] is None and t["router_cost"] is None


def test_an_integer_zero_is_an_observed_zero():
    t = tc.read_turn({"progress": [_rd(router_cost_usd=0), _qc(total_cost_usd=0)]})
    assert t["engine_cost"] == 0.0 and t["router_cost"] == 0.0


def test_a_malformed_record_reads_as_nothing_rather_than_raising():
    """The record is read before the turn is scored; a server bug in one field must
    not turn a paid turn into a harness error."""
    payload = {"progress": [
        "not an event",
        {"event": "route_decided", "data": ["not", "a", "dict"]},
        {"event": "query_complete", "data": {"total_cost_usd": 0.2, "models_used": "opus",
                                             "model_fallback": {"from": "a"},
                                             "router_fallback": "x"}},
    ]}
    t = tc.read_turn(payload)
    assert t["route"] is None and t["router_cost"] is None
    assert t["engine_cost"] == 0.2
    assert t["models_used"] == [] and t["model_fallback"] == []


def test_the_router_partial_flag_is_read():
    """Contract addendum: a router attempt cancelled at its time limit may still bill."""
    t = tc.read_turn({"progress": [_rd(router_cost_usd=0.001, router_cost_partial=True)]})
    assert t["router_cost_partial"] is True


# ── one turn's total ─────────────────────────────────────────────────────────

def _total(**kw):
    base = {"engine_cost": None, "router_cost": None, "route": "container_cc",
            "source": "baml", "cost_partial": False, "router_cost_partial": False}
    base.update(kw)
    return tc.turn_total(**base)


def test_both_parts_observed_is_their_sum_and_complete():
    assert _total(engine_cost=0.42, router_cost=0.004) == (pytest.approx(0.424), False)


def test_only_the_engine_observed_is_a_partial_turn():
    """The shape of every turn from a server older than the router price."""
    assert _total(engine_cost=0.42) == (0.42, True)


def test_a_route_tier_turn_counts_the_router_cost_it_saw():
    """The client stops at `route_decided`; the router's price is on that event."""
    assert _total(router_cost=0.004) == (0.004, True)


def test_a_turn_that_observed_nothing_is_unmeasured():
    assert _total() == (None, True)


def test_an_unrelated_turn_runs_no_engine_so_its_router_cost_is_the_whole_cost():
    """`unrelated` answers with canned text after `route_decided` and calls no model,
    so a missing engine cost there is not unobserved spend."""
    assert _total(route="unrelated", router_cost=0.003) == (0.003, False)


def test_an_unrelated_turn_without_a_router_price_is_still_unmeasured():
    """The router call was real; the harness never saw its price."""
    assert _total(route="unrelated") == (None, True)


def test_a_forced_turn_made_no_router_call_so_its_engine_cost_is_the_whole_cost():
    """`policy.decide_route` returns before the router is called when the route is forced."""
    assert _total(source="forced", engine_cost=0.5) == (0.5, False)


def test_a_sticky_turn_still_paid_for_the_router():
    """Only `forced` skips the router; sticky, followup and pipeline decide after it."""
    for source in ("sticky", "followup", "pipeline", "cc_unavailable", "heuristic"):
        assert _total(source=source, engine_cost=0.5) == (0.5, True), source


@pytest.mark.parametrize("flag", ["cost_partial", "router_cost_partial"])
def test_a_part_that_says_it_is_partial_makes_the_turn_partial(flag):
    assert _total(engine_cost=0.5, router_cost=0.01, **{flag: True}) == (pytest.approx(0.51), True)


def test_the_sum_does_not_leak_float_noise():
    cost, _ = _total(engine_cost=0.1, router_cost=0.2)
    assert cost == 0.3


# ── one case's total ─────────────────────────────────────────────────────────

def test_a_case_is_the_sum_of_every_turn_not_the_last_one():
    """The 2026-09-25 defect: three CC turns reported only the third."""
    assert tc.case_total([(0.5, False), (0.6, False), (0.7, False)]) == (1.8, False)


def test_an_unmeasured_turn_adds_nothing_and_marks_the_case_partial():
    assert tc.case_total([(0.5, False), (None, True)]) == (0.5, True)


def test_a_partial_turn_marks_the_case_partial():
    assert tc.case_total([(0.5, False), (0.2, True)]) == (0.7, True)


def test_a_case_that_observed_nothing_is_unmeasured_not_zero_and_not_partial():
    """`None` already says nothing was observed; `partial` qualifies a number."""
    assert tc.case_total([(None, True), (None, True)]) == (None, False)


def test_a_case_with_no_turn_at_all_is_unmeasured():
    assert tc.case_total([]) == (None, False)


def test_a_turn_that_was_sent_but_never_recorded_marks_the_case_partial():
    """A driver exception mid-case: the request may have been billed, and nothing
    about it was observed."""
    assert tc.case_total([(0.5, False)], missing_turns=1) == (0.5, True)


def test_an_observed_zero_case_is_complete():
    assert tc.case_total([(0.0, False)]) == (0.0, False)


# ── fallback ─────────────────────────────────────────────────────────────────

def test_a_turn_fell_back_when_any_model_did():
    fb = {"agent": "container_cc", "from": "a", "to": "b", "reason": "server_error"}
    assert tc.fell_back({"model_fallback": [fb], "router_fallback": None}) is True
    assert tc.fell_back({"model_fallback": [],
                         "router_fallback": {"from": "a", "to": "heuristic",
                                             "reason": "timeout"}}) is True
    assert tc.fell_back({"model_fallback": [], "router_fallback": None}) is False
    assert tc.fell_back({}) is False


def test_the_module_imports_nothing_outside_the_standard_library():
    """`output-skill/scripts/fetch_run.py` loads this file by path on an operator's
    host that has python3 and nothing else."""
    import ast
    import sys
    from pathlib import Path

    src = Path(tc.__file__).read_text(encoding="utf-8")
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names) | {"__future__"}, roots
