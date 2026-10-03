"""What a turn shares with its pre-run and its ops (plan 04, piece 3), in chat_nextseek's own terms.

* ``CallScope.seed`` / ``strikes``: the failed models a turn stores, as ``[provider, model, reason]``, going into a
  scope and coming out of it; a scope's own mark stands.
* ``TurnSpend.absorb``: the pre-run's calls become the NS turn's own; one still running makes the cost partial.
* ``suggestions.peek`` and ``orchestrator.vocabulary_not_needed``: whether a chip or the pipeline wizard will take
  this message's turn, read without clearing the chip offer (``accept`` clears it).
"""
from __future__ import annotations

import pytest

from chat_nextseek import call_scope, model_prices, turn_spend
from chat_nextseek import orchestrator as orch
from chat_nextseek.helpers import suggestions as sg
from chat_nextseek.llm_clients import LLMResponse

FLASH = "gemini-3.5-flash"
SONNET = "us.anthropic.claude-sonnet-4-6"
USAGE = {"prompt_tokens": 4000, "completion_tokens": 100, "thoughts_tokens": 300, "cached_tokens": 1000}
CHIP_QUERY = "Only RNA-Seq?"
RERUN = {"mode": "direct", "cypher": "MATCH (s:T_PAT) RETURN count(s) AS n", "parameters": {}}


def test_a_scope_seeded_with_strikes_treats_them_as_failed_and_keeps_its_own_marks():
    scope = call_scope.CallScope()
    scope.mark_failed(("gcp", FLASH), reason="unavailable", agent="graph_agent")

    scope.seed([["gcp", FLASH, "timeout"], ["bedrock", SONNET, "unavailable"], ["bad"], "x", ["", "m", "r"]])

    assert scope.failed(("gcp", FLASH))["reason"] == "unavailable", "the first mark stands"
    assert scope.failed(("bedrock", SONNET))["reason"] == "unavailable"
    assert scope.strikes() == [["gcp", FLASH, "unavailable"], ["bedrock", SONNET, "unavailable"]]


def _priced(spend: turn_spend.TurnSpend) -> None:
    spend.record({"agent": "entity", "provider": "gcp", "model": FLASH, "attempt": 1, "outcome": "ok"},
                 resp=LLMResponse(content="x", raw=None, usage=dict(USAGE), model=FLASH, provider="gcp", metadata={}))


def test_absorbing_a_finished_collector_takes_its_calls_as_the_turns_own():
    prerun, turn = turn_spend.TurnSpend(), turn_spend.TurnSpend()
    _priced(prerun)
    _priced(turn)

    turn.absorb(prerun)

    record = turn.summary()
    assert record["total_cost_usd"] == pytest.approx(2 * model_prices.call_cost(FLASH, USAGE).cost_usd, abs=1e-6)
    assert record["cost_partial"] is False
    assert record["models_used"] == [FLASH]
    assert len(record["cost"]["calls"]) == 2


def test_absorbing_an_unfinished_collector_makes_the_turn_partial_not_cheaper():
    turn = turn_spend.TurnSpend()
    _priced(turn)

    turn.absorb(turn_spend.TurnSpend(), finished=False)

    record = turn.summary()
    assert record["cost_partial"] is True
    assert "pre-run" in record["cost"]["unobserved_calls"][0]["why"]


def test_absorbing_nothing_changes_nothing():
    turn = turn_spend.TurnSpend()
    turn.absorb(None)
    assert turn.summary()["total_cost_usd"] == 0.0


def _session(items, for_turn=3):
    return {"chat_log": [{"turn_id": 3, "user_query": "q", "mode": "graph_query", "status": "completed"}],
            sg.SESSION_KEY: {"for_turn": for_turn, "items": items}}


def test_peek_finds_the_clicked_chip_and_leaves_the_offer_in_place():
    session = _session([{"query": CHIP_QUERY, "rerun": RERUN}])

    assert sg.peek(session, f"  {CHIP_QUERY} ", last_turn_id=3)["query"] == CHIP_QUERY
    assert sg.SESSION_KEY in session
    assert sg.accept(session, CHIP_QUERY, last_turn_id=3)["query"] == CHIP_QUERY
    assert sg.SESSION_KEY not in session, "accept still clears the offer"


def test_peek_answers_none_for_another_text_or_a_later_turn():
    session = _session([{"query": CHIP_QUERY, "rerun": RERUN}])
    assert sg.peek(session, "something else", last_turn_id=3) is None
    assert sg.peek(session, CHIP_QUERY, last_turn_id=4) is None


def test_no_vocabulary_is_needed_when_a_chip_with_a_rerun_takes_the_turn():
    session = _session([{"query": CHIP_QUERY, "rerun": RERUN}])
    assert orch.vocabulary_not_needed(session, CHIP_QUERY) is True
    assert sg.SESSION_KEY in session, "the check must not clear the offer: run_query's own accept reads it"


def test_a_chip_without_a_rerun_runs_the_whole_pipeline_so_it_needs_one():
    assert orch.vocabulary_not_needed(_session([{"query": CHIP_QUERY}]), CHIP_QUERY) is False


def test_an_open_wizard_takes_the_turn():
    assert orch.vocabulary_not_needed({"pipeline_agent": {"active": True}}, "yes, launch it") is True


def test_an_ordinary_message_needs_one():
    assert orch.vocabulary_not_needed({}, "how many mice") is False
