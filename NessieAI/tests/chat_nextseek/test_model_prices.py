"""The price table every turn is costed from, and what one model call costs.

``NessieAI/chat_nextseek/model_prices.json`` holds USD per 1M tokens for every model a
turn can reach, each price with its source, the day it was checked, and whether it was
read off the source (``confirmed``) or computed from it (``derived``, with how).
``chat_nextseek.model_prices`` loads it and prices one call's usage: Gemini bills its
cached prompt tokens at the cache rate and its thinking tokens as output; Bedrock
reports cached tokens outside ``inputTokens`` and bills a cache write at the rate of
the TTL it was asked for.

The guard at the end is the one that matters for a paid run: every model the shipped
config can reach (the ``default`` profile, each provider's first fallback profile, the
``_fallback`` block, the router's BAML clients and the Container-CC model map) has a
price, so no turn is silently partial.
"""
from __future__ import annotations

import json
import re
from datetime import date

import pytest

from NessieAI import paths
from chat_nextseek import model_prices
from chat_nextseek.schemas.schema_helper import FALLBACK_OVERRIDE_KEY, _FALLBACK_CHAINS

TABLE = model_prices.load_price_table()
CHECKED = "2026-09-25"
GEMINI_PAGE = "https://ai.google.dev/gemini-api/docs/pricing"
CLAUDE_PAGE = "https://platform.claude.com/docs/en/about-claude/pricing"


def _rates(model, **kw):
    return model_prices.rates_for(model, table=TABLE, **kw)


# ---------------------------------------------------------------------------- the file

def test_the_table_sits_beside_the_agent_model_catalog():
    assert model_prices.PRICE_TABLE_FILE == paths.CHAT_NEXTSEEK_DIR / "model_prices.json"
    assert (paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json").is_file()
    assert model_prices.PRICE_TABLE_FILE.is_file()


def test_the_table_has_a_version_and_counts_per_million_tokens():
    raw = json.loads(model_prices.PRICE_TABLE_FILE.read_text(encoding="utf-8"))
    assert raw["version"] == TABLE.version
    assert raw["per_tokens"] == 1_000_000
    assert raw["currency"] == "USD"


def test_every_price_names_its_source_its_check_date_and_how_it_was_had():
    for model, entry in TABLE.models.items():
        assert entry["prices"], model
        for price in entry["prices"]:
            assert price["status"] in ("confirmed", "derived"), (model, price)
            assert price["source"].startswith("https://"), (model, price)
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", price["checked"]), (model, price)
            if price["status"] == "derived":
                assert len(price.get("derivation") or "") > 40, f"{model}: say how it was derived"
            for key in ("input", "output", "cache_read"):
                assert isinstance(price[key], (int, float)) and price[key] > 0, (model, key)


def test_a_malformed_table_is_refused(tmp_path):
    bad = tmp_path / "prices.json"
    bad.write_text(json.dumps({"version": "x", "currency": "USD", "per_tokens": 1_000_000, "models": {
        "m": {"provider": "gcp", "prices": [{"input": 1, "output": 2, "cache_read": 0.1}]}}}))
    with pytest.raises(ValueError, match="source"):
        model_prices.load_price_table(bad)


# ---------------------------------------------------------------------------- the values

@pytest.mark.parametrize("model, expected", [
    ("gemini-3.5-flash", (1.50, 9.00, 0.15)),
    ("gemini-2.5-flash", (0.30, 2.50, 0.03)),
])
def test_the_flat_gemini_prices(model, expected):
    r = _rates(model)
    assert (r["input"], r["output"], r["cache_read"]) == expected
    assert (r["status"], r["source"], r["checked"]) == ("confirmed", GEMINI_PAGE, CHECKED)


@pytest.mark.parametrize("model, small, large", [
    ("gemini-3.1-pro-preview", (2.00, 12.00, 0.20), (4.00, 18.00, 0.40)),
    ("gemini-2.5-pro", (1.25, 10.00, 0.125), (2.50, 15.00, 0.25)),
])
def test_the_pro_models_price_by_prompt_length(model, small, large):
    at_limit = _rates(model, prompt_tokens=200_000)
    over = _rates(model, prompt_tokens=200_001)
    assert (at_limit["input"], at_limit["output"], at_limit["cache_read"]) == small
    assert (over["input"], over["output"], over["cache_read"]) == large


def test_gemini_3_8_flash_changes_price_on_the_first_of_january_2027():
    last = _rates("gemini-3.8-flash", on=date(2026, 12, 31))
    first = _rates("gemini-3.8-flash", on=date(2027, 1, 1))
    assert (last["input"], last["output"], last["cache_read"]) == (0.75, 3.75, 0.075)
    assert (first["input"], first["output"], first["cache_read"]) == (1.50, 7.50, 0.15)


@pytest.mark.parametrize("model, expected, first_party", [
    ("us.anthropic.claude-opus-4-7", (5.50, 27.50, 6.875, 11.00, 0.55), (5, 25, 6.25, 10, 0.50)),
    ("us.anthropic.claude-opus-4-8", (5.50, 27.50, 6.875, 11.00, 0.55), (5, 25, 6.25, 10, 0.50)),
    ("us.anthropic.claude-opus-5-5", (4.40, 22.00, 5.50, 8.80, 0.22), (4, 20, 5, 8, 0.20)),
    ("us.anthropic.claude-sonnet-4-6", (3.30, 16.50, 4.125, 6.60, 0.33), (3, 15, 3.75, 6, 0.30)),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", (1.10, 5.50, 1.375, 2.20, 0.11), (1, 5, 1.25, 2, 0.10)),
])
def test_the_bedrock_us_prices_are_first_party_list_plus_the_regional_ten_percent(model, expected, first_party):
    r = _rates(model)
    got = (r["input"], r["output"], r["cache_write_5m"], r["cache_write_1h"], r["cache_read"])
    assert got == pytest.approx(expected)
    assert got == pytest.approx(tuple(round(v * 1.1, 6) for v in first_party))
    assert (r["status"], r["source"], r["checked"]) == ("derived", CLAUDE_PAGE, CHECKED)
    assert "10%" in r["derivation"]


@pytest.mark.parametrize("model", [
    "anthropic.claude-sonnet-4-5-20250929-v1:0",   # anth:lite
    "anthropic.claude-opus-4-5-20251101-v1:0",     # anth:lite
    "aws:son",
])
def test_the_lite_and_aws_ids_are_left_unpriced(model):
    assert _rates(model) is None
    cost = model_prices.call_cost(model, {"prompt_tokens": 10, "completion_tokens": 5}, table=TABLE)
    assert cost.cost_usd is None and cost.reason == "unpriced"


# ---------------------------------------------------------------------------- one call's cost

def test_a_gemini_call_bills_cached_tokens_at_the_cache_rate_and_thinking_as_output():
    usage = {"prompt_tokens": 10_000, "completion_tokens": 200, "cached_tokens": 8_000, "thoughts_tokens": 1_000}
    cost = model_prices.call_cost("gemini-3.5-flash", usage, table=TABLE)
    expected = (2_000 * 1.50 + 8_000 * 0.15 + (200 + 1_000) * 9.00) / 1_000_000
    assert cost.cost_usd == pytest.approx(expected)
    assert cost.tokens == {"input": 2_000, "cache_read": 8_000, "cache_write_5m": 0, "cache_write_1h": 0,
                           "output": 1_200}


def test_a_gemini_pro_call_is_tiered_on_its_whole_prompt_cached_part_included():
    usage = {"prompt_tokens": 250_000, "completion_tokens": 0, "cached_tokens": 100_000}
    cost = model_prices.call_cost("gemini-3.1-pro-preview", usage, table=TABLE)
    assert cost.cost_usd == pytest.approx((150_000 * 4.00 + 100_000 * 0.40) / 1_000_000)


def test_a_bedrock_call_bills_reads_and_writes_beside_uncached_input_and_thinking_inside_output():
    usage = {"prompt_tokens": 1_000, "completion_tokens": 500, "cache_read_tokens": 20_000,
             "cache_write_tokens": 4_000, "cache_ttl": "1h"}
    cost = model_prices.call_cost("us.anthropic.claude-opus-4-7", usage, table=TABLE)
    expected = (1_000 * 5.50 + 20_000 * 0.55 + 4_000 * 11.00 + 500 * 27.50) / 1_000_000
    assert cost.cost_usd == pytest.approx(expected)
    assert cost.tokens["cache_write_1h"] == 4_000 and cost.tokens["cache_write_5m"] == 0


def test_a_bedrock_write_with_no_ttl_named_is_priced_at_the_default_five_minutes():
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cache_write_tokens": 1_000}
    cost = model_prices.call_cost("us.anthropic.claude-sonnet-4-6", usage, table=TABLE)
    assert cost.cost_usd == pytest.approx(1_000 * 4.125 / 1_000_000)


def test_bedrock_per_ttl_write_counts_beat_the_requested_ttl():
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cache_write_tokens": 3_000, "cache_ttl": "1h",
             "cache_write_5m_tokens": 1_000, "cache_write_1h_tokens": 2_000}
    cost = model_prices.call_cost("us.anthropic.claude-sonnet-4-6", usage, table=TABLE)
    assert cost.cost_usd == pytest.approx((1_000 * 4.125 + 2_000 * 6.60) / 1_000_000)


def test_a_call_with_no_token_counts_is_not_priced_as_zero():
    cost = model_prices.call_cost("gemini-3.5-flash", {"prompt_tokens": None, "completion_tokens": None},
                                  table=TABLE)
    assert cost.cost_usd is None and cost.reason == "no_usage"
    assert model_prices.call_cost("gemini-3.5-flash", None, table=TABLE).reason == "no_usage"


# ---------------------------------------------------------------------------- the guard

def _catalog():
    return json.loads((paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json").read_text(encoding="utf-8"))


def _profile_models(catalog, profile):
    return {m for m in (catalog.get(profile) or {}).get("models", {}) if m != "__default__"}


def _reachable_models() -> dict[str, str]:
    """Every model the shipped config can send a call to, and why."""
    catalog = _catalog()
    reach: dict[str, str] = {}
    for m in _profile_models(catalog, "default"):
        reach[m] = "default profile"
    for provider in ("gcp", "anth"):
        first = _FALLBACK_CHAINS[("default", provider)][0]
        for m in _profile_models(catalog, first):
            reach.setdefault(m, f"first fallback after a {provider} failure ({first})")
    for agent, cfg in catalog[FALLBACK_OVERRIDE_KEY].items():
        if isinstance(cfg, dict) and cfg.get("model"):
            reach.setdefault(cfg["model"], f"{FALLBACK_OVERRIDE_KEY} block ({agent})")
    clients = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "clients.baml").read_text(encoding="utf-8")
    for m in re.findall(r'\bmodel\s+"([^"]+)"', clients):
        reach.setdefault(m, "a BAML client (router)")
    cc_map = json.loads((paths.DMAC_BUILD_CONTEXT / "router_model_class_map.json").read_text(encoding="utf-8"))
    for key, m in cc_map.items():
        reach.setdefault(m, f"router_model_class_map.json ({key})")
    return reach


def test_the_guard_sees_the_models_it_is_meant_to():
    reach = _reachable_models()
    for m in ("gemini-3.5-flash", "us.anthropic.claude-opus-4-7", "us.anthropic.claude-sonnet-4-6",
              "gemini-3.1-pro-preview", "us.anthropic.claude-opus-4-8"):
        assert m in reach


def test_every_model_a_turn_can_reach_has_a_price():
    unpriced = {m: why for m, why in _reachable_models().items() if _rates(m) is None}
    assert not unpriced, f"price these in model_prices.json: {unpriced}"
