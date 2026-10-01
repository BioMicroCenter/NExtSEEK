"""Model prices, and what one model call cost.

The prices live in ``NessieAI/chat_nextseek/model_prices.json``, beside
``agent_model_catalog.json``: USD per 1M tokens, each price with its source, the day it
was checked, and whether it was read off the source or derived from it. This module
loads that file and prices one call's usage. The NS turn collector
(``chat_nextseek.turn_spend``), the router (``NessieAI/router/router.py``) and the
Container-CC translator (``NessieAI/cc/translate.py``) all price through it, so every
turn, and every run, is costed on one table.

Standard library only: the router and the CC translator import it too.

How a call's tokens are billed depends on how its provider counts them, and the usage
dicts the clients write keep each provider's own convention (``llm_clients.py``):

* Gemini (``provider: gcp``): ``prompt_tokens`` is the whole prompt, ``cached_tokens``
  is the part of it read from the cache (billed at ``cache_read``), and
  ``thoughts_tokens`` (thinking) is outside ``completion_tokens`` but billed as output.
  A tiered model is tiered on the whole prompt.
* Bedrock (``provider: bedrock``): ``prompt_tokens`` excludes cached tokens,
  ``cache_read_tokens`` and ``cache_write_tokens`` come beside it, and thinking is
  already inside ``completion_tokens``. A cache write is billed at the rate of its TTL:
  the per-TTL counts Bedrock reports (``cache_write_5m_tokens``,
  ``cache_write_1h_tokens``) when present, else the TTL the call asked for
  (``cache_ttl``), else Bedrock's default of five minutes.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "PRICE_TABLE_FILE",
    "PriceTable",
    "CallCost",
    "load_price_table",
    "rates_for",
    "billed_tokens",
    "call_cost",
    "today",
]

# src/chat_nextseek/model_prices.py -> parents[2] is NessieAI/chat_nextseek/.
PRICE_TABLE_FILE = Path(__file__).resolve().parents[2] / "model_prices.json"

_RATE_KEYS = ("input", "output", "cache_read")
_BEDROCK_WRITE_KEYS = ("cache_write_5m", "cache_write_1h")
_STATUSES = ("confirmed", "derived")
_PER = 1_000_000


@dataclass(frozen=True)
class PriceTable:
    version: str
    models: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class CallCost:
    """One call priced. ``cost_usd`` is None when the call could not be priced:
    ``reason`` is then ``unpriced`` (no price for the model) or ``no_usage`` (the
    call reported no token counts). ``tokens`` is what was billed, by rate."""

    model: str
    cost_usd: float | None
    reason: str | None = None
    tokens: dict[str, int] = field(default_factory=dict)


def today() -> date:
    """The day a call is priced on (UTC)."""
    return datetime.now(timezone.utc).date()


def _validate(raw: Any, source: Path) -> PriceTable:
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), dict):
        raise ValueError(f"{source}: a price table is an object with a 'models' object")
    if raw.get("per_tokens") != _PER or raw.get("currency") != "USD":
        raise ValueError(f"{source}: prices must be USD per {_PER:,} tokens")
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{source}: the table needs a version")
    for model, entry in raw["models"].items():
        prices = entry.get("prices") if isinstance(entry, dict) else None
        if entry.get("provider") not in ("gcp", "bedrock") or not isinstance(prices, list) or not prices:
            raise ValueError(f"{source}: {model} needs a provider (gcp or bedrock) and a list of prices")
        for price in prices:
            for key in _RATE_KEYS + (_BEDROCK_WRITE_KEYS if entry["provider"] == "bedrock" else ()):
                if not isinstance(price.get(key), (int, float)) or isinstance(price.get(key), bool):
                    raise ValueError(f"{source}: {model} has no numeric {key}")
            if not str(price.get("source") or "").startswith("https://"):
                raise ValueError(f"{source}: every price of {model} needs its source URL")
            if price.get("status") not in _STATUSES:
                raise ValueError(f"{source}: every price of {model} is confirmed or derived")
            if price["status"] == "derived" and not price.get("derivation"):
                raise ValueError(f"{source}: a derived price of {model} says how it was derived")
            for key in ("checked", "from", "until"):
                if key in price:
                    date.fromisoformat(price[key])
            if "checked" not in price:
                raise ValueError(f"{source}: every price of {model} needs the day it was checked")
    return PriceTable(version=version, models=raw["models"])


@functools.lru_cache(maxsize=8)
def _load(path: str) -> PriceTable:
    source = Path(path)
    return _validate(json.loads(source.read_text(encoding="utf-8")), source)


def load_price_table(path: Path | str | None = None) -> PriceTable:
    """The price table at ``path`` (default: the shipped one), read once per process.

    Raises ``ValueError`` on a malformed table and ``OSError`` on a missing one; the
    callers that price a turn catch both and report the turn's cost as partial.
    """
    return _load(str(Path(path) if path is not None else PRICE_TABLE_FILE))


def rates_for(model: str, *, on: date | None = None, prompt_tokens: int | None = None,
              table: PriceTable | None = None) -> dict[str, Any] | None:
    """The price entry in force for ``model`` on ``on`` (default today) for a prompt of
    ``prompt_tokens``, or None when the model has no price."""
    table = table or load_price_table()
    entry = table.models.get(model)
    if not entry:
        return None
    day = on or today()
    size = prompt_tokens or 0
    for price in entry["prices"]:
        if "from" in price and day < date.fromisoformat(price["from"]):
            continue
        if "until" in price and day > date.fromisoformat(price["until"]):
            continue
        if "max_prompt_tokens" in price and size > price["max_prompt_tokens"]:
            continue
        return {"provider": entry["provider"], **price}
    return None


def _count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _has_counts(usage: Any) -> bool:
    return isinstance(usage, dict) and any(
        isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool)
        for k in ("prompt_tokens", "completion_tokens")
    )


def _provider_of(model: str, table: PriceTable) -> str | None:
    entry = table.models.get(model)
    return entry.get("provider") if entry else None


def billed_tokens(provider: str, usage: dict) -> tuple[dict[str, int], int]:
    """(tokens by rate, the prompt size a tiered price is chosen on) for one call."""
    tokens = {"input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 0}
    prompt = _count(usage, "prompt_tokens")
    if provider == "gcp":
        cached = min(_count(usage, "cached_tokens"), prompt)
        tokens["input"] = prompt - cached
        tokens["cache_read"] = cached
        tokens["output"] = _count(usage, "completion_tokens") + _count(usage, "thoughts_tokens")
        return tokens, prompt
    tokens["input"] = prompt
    tokens["cache_read"] = _count(usage, "cache_read_tokens")
    w5, w1 = _count(usage, "cache_write_5m_tokens"), _count(usage, "cache_write_1h_tokens")
    if w5 or w1:
        tokens["cache_write_5m"], tokens["cache_write_1h"] = w5, w1
    else:
        written = _count(usage, "cache_write_tokens")
        tokens["cache_write_1h" if usage.get("cache_ttl") == "1h" else "cache_write_5m"] = written
    tokens["output"] = _count(usage, "completion_tokens")
    size = tokens["input"] + tokens["cache_read"] + tokens["cache_write_5m"] + tokens["cache_write_1h"]
    return tokens, size


def call_cost(model: str, usage: dict | None, *, on: date | None = None,
              table: PriceTable | None = None) -> CallCost:
    """What one call of ``model`` cost, from its ``usage`` dict (``llm_clients`` shape)."""
    table = table or load_price_table()
    if not _has_counts(usage):
        return CallCost(model=model, cost_usd=None, reason="no_usage")
    provider = _provider_of(model, table)
    if provider is None:
        return CallCost(model=model, cost_usd=None, reason="unpriced")
    tokens, size = billed_tokens(provider, usage)
    rates = rates_for(model, on=on, prompt_tokens=size, table=table)
    if rates is None:
        return CallCost(model=model, cost_usd=None, reason="unpriced", tokens=tokens)
    cost = sum(tokens[k] * float(rates.get(k) or 0.0) for k in tokens) / _PER
    return CallCost(model=model, cost_usd=round(cost, 10), tokens=tokens)
