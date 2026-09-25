"""A Container-CC turn is also costed on the NS price table, beside Claude Code's own number.

Claude Code prices Bedrock with its own table: its result frame's ``modelUsage`` says
``costBasis: "list"``, and on 2.1.282 a turn of 12 input and 5 output tokens on Opus 4.8
reported $0.000185, the first-party list price with no US-geo premium. An NS turn and
the router are priced on ``NessieAI/chat_nextseek/model_prices.json``, which carries it.
So the CC ``query_complete`` keeps ``total_cost_usd`` as Claude Code's number and adds
``cost_by_price_table_usd``, computed from ``modelUsage`` on that table, so the two
engines, and two runs, compare on one table.

Hermetic: frames only.
"""
from __future__ import annotations

import pytest

from NessieAI.cc.translate import CCStreamTranslator
from chat_nextseek import model_prices

OPUS_48 = "us.anthropic.claude-opus-4-8"
OPUS_47 = "us.anthropic.claude-opus-4-7"


def _result(model_usage=None, usage=None, **extra):
    frame = {"type": "result", "subtype": "success", "is_error": False, "result": "done",
             "session_id": "s-1", "total_cost_usd": 0.000185, **extra}
    if model_usage is not None:
        frame["modelUsage"] = model_usage
    if usage is not None:
        frame["usage"] = usage
    return frame


def _complete(frame):
    (event, data), = CCStreamTranslator(model_id=OPUS_48).handle(frame)
    assert event == "query_complete"
    return data


def _mu(inp, out, read=0, create=0, thinking=0):
    return {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read,
            "cacheCreationInputTokens": create, "thinkingTokens": thinking, "costUSD": 0.0,
            "provider": "bedrock", "costBasis": "list"}


def test_the_2_1_282_frame_is_priced_with_the_us_premium_and_claude_codes_number_is_kept():
    data = _complete(_result({OPUS_48: _mu(12, 5)}))
    assert data["total_cost_usd"] == 0.000185, "Claude Code's own number is untouched"
    assert data["cost_by_price_table_usd"] == pytest.approx((12 * 5.50 + 5 * 27.50) / 1_000_000)
    assert data["cost_by_price_table_usd"] == pytest.approx(0.000185 * 1.1)


def test_thinking_is_inside_output_so_it_is_not_added_again():
    data = _complete(_result({OPUS_48: _mu(100, 400, thinking=300)}))
    assert data["cost_by_price_table_usd"] == pytest.approx((100 * 5.50 + 400 * 27.50) / 1_000_000)


def test_cache_reads_and_writes_are_priced_and_writes_split_by_the_frames_ttl_counts():
    usage = {"cache_creation": {"ephemeral_1h_input_tokens": 3000, "ephemeral_5m_input_tokens": 1000}}
    data = _complete(_result({OPUS_48: _mu(10, 20, read=50_000, create=4000)}, usage))
    expected = (10 * 5.50 + 50_000 * 0.55 + 1000 * 6.875 + 3000 * 11.00 + 20 * 27.50) / 1_000_000
    assert data["cost_by_price_table_usd"] == pytest.approx(expected)


def test_writes_with_no_ttl_counts_are_priced_at_five_minutes():
    data = _complete(_result({OPUS_48: _mu(0, 0, create=1000)}))
    assert data["cost_by_price_table_usd"] == pytest.approx(1000 * 6.875 / 1_000_000)


def test_a_turn_that_fell_back_is_priced_on_both_models():
    data = _complete(_result({OPUS_48: _mu(1000, 10), OPUS_47: _mu(2000, 300)}))
    assert data["cost_by_price_table_usd"] == pytest.approx(
        (1000 * 5.50 + 10 * 27.50 + 2000 * 5.50 + 300 * 27.50) / 1_000_000)


def test_a_model_the_table_does_not_price_leaves_no_comparable_number():
    data = _complete(_result({OPUS_48: _mu(10, 5), "us.anthropic.claude-unknown-9": _mu(10, 5)}))
    assert data["cost_by_price_table_usd"] is None


def test_a_model_that_billed_nothing_does_not_need_a_price():
    """Claude Code can list a model it asked nothing of; with no tokens it cost nothing."""
    data = _complete(_result({OPUS_48: _mu(10, 5), "us.anthropic.claude-unknown-9": _mu(0, 0)}))
    assert data["cost_by_price_table_usd"] == pytest.approx((10 * 5.50 + 5 * 27.50) / 1_000_000)


@pytest.mark.parametrize("model_usage", [None, {}])
def test_no_model_usage_leaves_no_comparable_number(model_usage):
    assert _complete(_result(model_usage))["cost_by_price_table_usd"] is None


def test_the_number_is_the_price_tables_call_cost_summed():
    """One pricing rule for every engine: the translator adds model_prices.call_cost."""
    data = _complete(_result({OPUS_48: _mu(700, 70, read=7000)}))
    one = model_prices.call_cost(OPUS_48, {"prompt_tokens": 700, "completion_tokens": 70,
                                           "cache_read_tokens": 7000}).cost_usd
    assert data["cost_by_price_table_usd"] == pytest.approx(one)
