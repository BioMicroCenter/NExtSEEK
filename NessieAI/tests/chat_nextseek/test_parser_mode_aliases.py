"""An aliased parser mode reaches a branch that exists (F6).

`schemas/router.py` has documented `memory_lookup` as an accepted alias of
`ask_about_last_results` since it was added, and published it in the enum the model is
handed. Nothing performed the normalisation, so a parser that took the schema at its
word produced a mode the orchestrator has no branch for, and the user got the whole of:
"The parser returned an unexpected mode='memory_lookup'. I don't yet know how to handle
this case." (memory.fresh_session_has_no_history).

The planner's own step mapping already routed `memory_lookup` to its memory tool; this
is the single-plan path, which did not.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents.parser import _MODE_ALIASES, _apply_parser_guardrails, _normalise_mode_aliases
from chat_nextseek.schemas.router import PARSER_MODES, ParserPlan


def test_memory_lookup_normalises_to_the_mode_that_has_a_branch():
    plan = _normalise_mode_aliases(ParserPlan(mode="memory_lookup", intent_summary="what did we find"))
    assert plan.mode == "ask_about_last_results"
    assert "normalised" in plan.notes


def test_the_alias_is_normalised_through_the_guardrails():
    plan = _apply_parser_guardrails("what did you find earlier?",
                                    ParserPlan(mode="memory_lookup", intent_summary="recall"))
    assert plan.mode == "ask_about_last_results"


@pytest.mark.parametrize("mode", [m for m in PARSER_MODES if m not in _MODE_ALIASES])
def test_every_other_published_mode_is_left_alone(mode):
    assert _normalise_mode_aliases(ParserPlan(mode=mode)).mode == mode


def test_an_unknown_mode_still_reaches_the_civil_reply():
    """Only a documented alias is rewritten: anything else must keep failing loudly."""
    assert _normalise_mode_aliases(ParserPlan(mode="teleport")).mode == "teleport"


def test_every_alias_target_is_a_mode_the_orchestrator_dispatches():
    for alias, target in _MODE_ALIASES.items():
        assert alias in PARSER_MODES, alias
        assert target in PARSER_MODES, target
