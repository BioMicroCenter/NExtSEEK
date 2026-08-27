"""
Regression lock for T1.7: a refine with no result bundle must say so.

A Container CC turn writes no result bundle (task 795 closed with bid=None, task 796
opened bid=1), so `results_history` was empty, the refine plan was built with Nones,
and the turn quietly became a fresh search. The router had explicitly asked for a
memory follow-up — 796's routing reasoning names `memory_lookup` — and nothing
detected the mismatch.

This is deliberately NOT fixed by synthesising a bundle. A bundle's contract is a
real REST call plus its response; fabricating one would let the parser re-POST an
invented api_plan and return confidently wrong rows, which is strictly worse than
the current degrade. The fix is to make the degrade loud.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents.parser import (
    REFINE_WITHOUT_BUNDLE_NOTE,
    _apply_parser_guardrails,
    _note_refine_without_bundle,
)
from chat_nextseek.schemas import ParserPlan


class _Session(dict):
    """Minimal stand-in for SessionState — `.get` is all the guard uses."""


def _refine_plan(**over) -> ParserPlan:
    base = dict(
        mode="refine_last_search",
        target_endpoint="/nextseek_api/samples/advanced_search/",
        intent_summary="narrow those to males",
        notes="",
    )
    base.update(over)
    return ParserPlan(**base)


def test_a_refine_with_no_bundle_becomes_a_declared_new_search():
    plan = _note_refine_without_bundle(_Session(results_history=[]), _refine_plan())

    assert plan.mode == "new_search", "the turn already ran as a fresh search; say so"
    assert REFINE_WITHOUT_BUNDLE_NOTE in plan.notes


def test_the_note_reaches_the_debug_block_via_notes():
    """`notes` is what flows into the reply debug block, so it is the assertable field."""
    plan = _note_refine_without_bundle(_Session(results_history=[]), _refine_plan())

    assert "no result bundle" in plan.notes
    assert "fresh search" in plan.notes


def test_an_existing_note_is_preserved():
    plan = _note_refine_without_bundle(
        _Session(results_history=[]), _refine_plan(notes="user asked to narrow")
    )

    assert plan.notes.startswith("user asked to narrow")
    assert REFINE_WITHOUT_BUNDLE_NOTE in plan.notes


def test_stale_previous_fields_are_cleared():
    plan = _note_refine_without_bundle(
        _Session(results_history=[]),
        _refine_plan(previous_api_plan={"endpoint": "/x"}, previous_user_query="old"),
    )

    assert plan.previous_api_plan is None
    assert plan.previous_user_query is None


def test_a_refine_with_a_real_bundle_is_untouched():
    session = _Session(results_history=[{"id": 1, "api_plan": {"endpoint": "/x"},
                                         "user_query": "find NHP"}])
    plan = _refine_plan()

    out = _note_refine_without_bundle(session, plan)

    assert out is plan
    assert out.mode == "refine_last_search"


@pytest.mark.parametrize("mode", ["new_search", "graph_query", "ask_about_last_results",
                                  "reporter", "unsupported"])
def test_other_modes_are_untouched(mode):
    plan = _refine_plan(mode=mode)
    assert _note_refine_without_bundle(_Session(results_history=[]), plan) is plan


def test_no_session_is_a_no_op():
    plan = _refine_plan()
    assert _note_refine_without_bundle(None, plan) is plan


def test_a_session_that_raises_does_not_break_the_turn():
    class _Broken:
        def get(self, *a, **k):
            raise RuntimeError("session backend down")

    plan = _refine_plan()
    assert _note_refine_without_bundle(_Broken(), plan) is plan


def test_the_guardrail_entry_point_applies_it():
    """parser_agent routes through _apply_parser_guardrails, so the guard must run there."""
    out = _apply_parser_guardrails(
        "narrow those to males", _refine_plan(), session=_Session(results_history=[])
    )

    assert out.mode == "new_search"
    assert REFINE_WITHOUT_BUNDLE_NOTE in out.notes


def test_the_guardrail_entry_point_still_works_without_a_session():
    """Back-compat: session is optional and omitting it must not change behaviour."""
    plan = _refine_plan()
    assert _apply_parser_guardrails("narrow those to males", plan).mode == "refine_last_search"
