"""The follow-up branch answers from the loop or says it could not; it never answers from a stored snapshot."""
from __future__ import annotations

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents.followup import FOLLOWUP_UNAVAILABLE_REPLY, resolve_followup_outcome


@pytest.mark.parametrize("outcome", [
    None,
    {"unsupported": True},
    {"reply": None, "queries": [], "computes": [], "tool_calls": ["read_stored_result"]},
])
def test_a_failed_unavailable_or_empty_loop_gets_the_fixed_reply(outcome):
    assert resolve_followup_outcome(outcome) == FOLLOWUP_UNAVAILABLE_REPLY


def test_a_reply_keeps_its_caveats():
    out = {"reply": "745 CC mice.", "caveats": ["The CC filter was applied by genotype."], "queries": []}
    assert resolve_followup_outcome(out) == "745 CC mice.\n\n- The CC filter was applied by genotype."


def test_work_without_a_reply_is_reported_as_partial():
    out = {"reply": None, "queries": [], "tool_calls": ["compute_over_rows"],
           "computes": [{"source": "stored", "result": {"ok": True, "count": 626}}]}
    assert "626" in resolve_followup_outcome(out)


def test_the_orchestrator_no_longer_has_the_stored_result_answer():
    assert not hasattr(orch, "memory_agent_answer")
