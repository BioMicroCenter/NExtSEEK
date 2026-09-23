"""One tool-enabled model turn, with recovery, caching and a ledger entry.

``BedrockClient.chat_with_tools`` is the only tool-use surface in this package, and
until now the only caller was the pipeline agent, which called it bare inside a twelve
iteration loop: no retry, no provider fallback, no ledger entry, and no prompt cache.
One 503 anywhere in a build ended it, and the tokens the loop spent were invisible.

This module is that call done once, properly, so a second tool loop (the follow-up
agent) does not repeat the mistakes:

* **Recovery.** A 503 or a transport timeout moves once to the next provider in the
  same ``_FALLBACK_CHAINS`` the structured path uses, skipping any fallback client that
  cannot take tools (a timeout with nowhere to move recycles the socket pool and
  retries); a 429 backs off.
* **Caching on by default.** A tool loop re-sends its whole head on every iteration,
  so the tools plus the system prompt are the clearest possible case for a cache
  point. This is the opposite of a one-shot call, where the win depends on whether
  traffic clusters inside the TTL and so stays opt-in.
* **A ledger entry per call**, including cache hits, so a loop's cost is measurable.
"""
from __future__ import annotations

import time
from typing import Any

from .helpers import log_llm_call
from .llm_clients import (
    LLMError,
    LLMFatalError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from .schemas.schema_helper import (
    MAX_PROVIDER_SWITCHES,
    _catalog_provider,
    _get_fallback_agent_configs,
    _ledger_entry,
    _recycle_client_connections,
)


def _tool_capable(client) -> bool:
    return callable(getattr(client, "chat_with_tools", None))


def _ledger(config, entry: dict) -> None:
    """Write one ledger record, and never let bookkeeping fail a user's turn.

    ``config.LOG_DIR`` is present on a real ChatConfig but not on every object that
    reaches here, and a missing attribute must not turn an answered question into an
    error.
    """
    try:
        log_dir = getattr(config, "LOG_DIR", None)
        if log_dir:
            log_llm_call(log_dir, entry)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[TOOL_LOOP] ledger write failed: {exc!r}")


def call_tools(
    config,
    *,
    messages: list[dict],
    tools: list[dict],
    system: str,
    model_name: str,
    client,
    agent_label: str,
    tool_choice: str | dict | None = None,
    thinking_budget: int | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    cache_prompt: bool = True,
    retries: int = 2,
    rate_limit_sleep: float = 1.0,
) -> dict:
    """Run one tool-enabled turn and return the normalized Converse result.

    Returns ``{"stop_reason", "content", "usage", "metadata"}``. Raises
    ``LLMFatalError`` when every provider that can take tools has refused, so the
    caller reports one honest failure rather than looping on a dead provider.
    """
    target_client = client
    target_model = model_name
    target_budget = thinking_budget
    fallbacks: list[tuple] | None = None
    switches = 0

    def _switch(reason: str) -> bool:
        """The one provider move a call gets (``MAX_PROVIDER_SWITCHES``). Never raises."""
        nonlocal fallbacks, switches, target_client, target_model, target_budget
        if switches >= MAX_PROVIDER_SWITCHES:
            return False
        if fallbacks is None:
            fallbacks = [
                fb for fb in _get_fallback_agent_configs(
                    config, agent_label, _catalog_provider(target_client)
                )
                # A tool loop cannot fail over to a client with no tool surface:
                # the conversation so far is tool_use and tool_result blocks.
                if _tool_capable(fb[0])
            ]
        if not fallbacks:
            return False
        target_client, target_model, target_budget = fallbacks.pop(0)
        switches += 1
        print(
            f"[TOOL_LOOP][{agent_label}] {reason}: switching to fallback "
            f"provider='{getattr(target_client, 'provider', '?')}' model='{target_model}'"
        )
        return True

    max_attempts = retries + 1
    attempt = -1
    while attempt + 1 < max_attempts:
        attempt += 1
        t0 = time.perf_counter()
        try:
            result = target_client.chat_with_tools(
                messages=messages,
                tools=tools,
                system=system,
                model=target_model,
                max_tokens=max_tokens,
                temperature=temperature,
                tool_choice=tool_choice,
                cache_prompt=cache_prompt,
                thinking_budget=target_budget,
            )
        except LLMServiceUnavailableError as sue:
            print(
                f"[TOOL_LOOP][{agent_label}] 503 from "
                f"provider='{getattr(target_client, 'provider', None)}' model='{target_model}' "
                f"attempt {attempt + 1}/{max_attempts}: {sue}"
            )
            _ledger(config, _ledger_entry(
                agent_label, target_model, target_client, attempt,
                "service_unavailable", t0, thinking_budget=target_budget, err=sue,
            ))
            if _switch("provider unavailable"):
                max_attempts += 1
                continue
            raise LLMFatalError(
                f"All tool-capable providers exhausted — agent '{agent_label}': {sue}",
                agent=agent_label,
            ) from sue
        except LLMTimeoutError as te:
            _ledger(config, _ledger_entry(
                agent_label, target_model, target_client, attempt,
                "timeout", t0, thinking_budget=target_budget, err=te,
            ))
            _recycle_client_connections(target_client, agent_label)
            # Same trigger as the structured path: a timeout moves to the next
            # tool-capable provider, once. With nowhere to move, the old
            # same-provider retry on a fresh socket stands.
            if _switch("transport timeout"):
                max_attempts += 1
                continue
            if switches or attempt + 1 >= max_attempts:
                raise
            continue
        except LLMRateLimitError as rle:
            _ledger(config, _ledger_entry(
                agent_label, target_model, target_client, attempt,
                "throttle", t0, thinking_budget=target_budget, err=rle,
            ))
            if attempt + 1 >= max_attempts:
                raise LLMFatalError(
                    f"Rate limited (429) — agent '{agent_label}', model '{target_model}': {rle}",
                    agent=agent_label,
                ) from rle
            try:
                time.sleep(rate_limit_sleep)
            except Exception:
                pass
            continue
        except LLMError as le:
            _ledger(config, _ledger_entry(
                agent_label, target_model, target_client, attempt,
                "error", t0, thinking_budget=target_budget, err=le,
            ))
            raise

        _ledger(config, _ledger_entry(
            agent_label, target_model, target_client, attempt, "ok", t0,
            thinking_budget=target_budget, resp=_LedgerView(result),
        ))
        return result

    raise LLMFatalError(f"Tool call exhausted its attempts — agent '{agent_label}'", agent=agent_label)


class _LedgerView:
    """Adapt ``chat_with_tools``'s dict result to what ``_ledger_entry`` reads.

    ``_ledger_entry`` takes an ``LLMResponse``-shaped object (``.usage``,
    ``.metadata``); the tool surface returns a plain dict because its caller needs the
    content blocks. Rather than reshape either side, this presents the two attributes
    the ledger wants.
    """

    __slots__ = ("usage", "metadata")

    def __init__(self, result: dict[str, Any]):
        self.usage = result.get("usage") or {}
        self.metadata = result.get("metadata") or {}
