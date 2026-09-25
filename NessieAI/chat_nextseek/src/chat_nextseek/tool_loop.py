"""One tool-enabled model turn, with recovery, caching and a ledger entry.

``BedrockClient.chat_with_tools`` is the only tool-use surface in this package, and
until now the only caller was the pipeline agent, which called it bare inside a twelve
iteration loop: no retry, no provider fallback, no ledger entry, and no prompt cache.
One 503 anywhere in a build ended it, and the tokens the loop spent were invisible.

This module is that call done once, properly, so a second tool loop (the follow-up
agent) does not repeat the mistakes:

* **Recovery.** A timeout, an empty turn, a 503, a 429 and a connection error move
  once to the next provider, the same trigger ``_call_with_recovery`` uses, skipping
  any fallback client that cannot take tools and any that is the model that just
  failed. For the follow-up and pipeline agents the catalog's ``_fallback`` block
  names that provider (Sonnet 4.6, operator ruling 2026-09-25): their profile chains
  lead back to the same Opus. When the provider it moved to fails too, the call ends
  in ``LLMFatalError`` with ``unavailable`` set, so the user is told the models were
  unavailable. A timeout with nowhere to move recycles the socket pool and retries once.
* **A wall clock.** Every call runs under ``timeout_seconds`` (the retry under
  ``timeout_retry_seconds``). boto3 alone waits out a 600 s read timeout and retries it,
  so a stalled Bedrock used to hold the turn for as long as the request watchdog let it.
* **Caching on by default.** A tool loop re-sends its whole head on every iteration,
  so the tools plus the system prompt are the clearest possible case for a cache
  point. This is the opposite of a one-shot call, where the win depends on whether
  traffic clusters inside the TTL and so stays opt-in.
* **A ledger entry per call**, including cache hits, so a loop's cost is measurable.
  The first call after a move also names it (``fallback_from``, ``fallback_reason``).
"""
from __future__ import annotations

import time
from typing import Any

from .helpers import log_llm_call
from .llm_clients import (
    LLMAPIConnectionError,
    LLMError,
    LLMFatalError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from .schemas.schema_helper import (
    MAX_PROVIDER_SWITCHES,
    _catalog_provider,
    _EmptyCompletion,
    _get_fallback_agent_configs,
    _ledger_entry,
    _recycle_client_connections,
    _run_with_wall_clock,
)

# The wall clock on one tool-loop call. Both loops run without extended thinking and
# are capped at the client's max_tokens (4096), so a call that answers at all answers
# well inside 120 s; the retry goes to the fallback model on a fresh socket and gets 60 s,
# the parser's retry window. Worst case per iteration: 120 + 60 s.
TOOL_CALL_TIMEOUT_SECONDS = 120
TOOL_CALL_TIMEOUT_RETRY_SECONDS = 60


def _tool_capable(client) -> bool:
    return callable(getattr(client, "chat_with_tools", None))


def _is_empty_turn(result: Any) -> bool:
    """A tool turn with no tool call and no text in it: the provider gave no answer."""
    content = (result or {}).get("content") if isinstance(result, dict) else None
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            return False
        if block.get("type") == "text" and (block.get("text") or "").strip():
            return False
    return True


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
    timeout_seconds: float = TOOL_CALL_TIMEOUT_SECONDS,
    timeout_retry_seconds: float | None = TOOL_CALL_TIMEOUT_RETRY_SECONDS,
    timeout_retries: int = 1,
) -> dict:
    """Run one tool-enabled turn and return the normalized Converse result.

    Returns ``{"stop_reason", "content", "usage", "metadata"}``. Raises
    ``LLMFatalError`` (``unavailable=True``) when the provider it moved to failed too,
    or when there was nowhere to move, so the caller reports one honest failure rather
    than looping on a dead provider. A bare ``LLMError`` propagates unchanged.
    """
    target_client = client
    target_model = model_name
    target_budget = thinking_budget
    fallbacks: list[tuple] | None = None
    switches = 0
    moves: list[dict] = []
    pending_move: dict = {}
    timeout_attempts = 0
    _timeout = timeout_seconds

    def _switch(reason: str, why: str) -> bool:
        """The one provider move a call gets (``MAX_PROVIDER_SWITCHES``). Never raises."""
        nonlocal fallbacks, switches, target_client, target_model, target_budget, pending_move
        if switches >= MAX_PROVIDER_SWITCHES:
            return False
        if fallbacks is None:
            fallbacks = [
                fb for fb in _get_fallback_agent_configs(
                    config, agent_label, _catalog_provider(target_client), failed_model=target_model,
                )
                # A tool loop cannot fail over to a client with no tool surface:
                # the conversation so far is tool_use and tool_result blocks.
                if _tool_capable(fb[0])
            ]
        if not fallbacks:
            return False
        moves.append({"agent": agent_label, "from": target_model, "to": fallbacks[0][1], "reason": reason})
        pending_move = {"fallback_from": target_model, "fallback_reason": reason}
        target_client, target_model, target_budget = fallbacks.pop(0)
        switches += 1
        print(
            f"[TOOL_LOOP][{agent_label}] {why}: switching to fallback "
            f"provider='{getattr(target_client, 'provider', '?')}' model='{target_model}'"
        )
        return True

    def _log(outcome: str, t0: float, **kw) -> None:
        nonlocal pending_move
        move, pending_move = pending_move, {}
        _ledger(config, _ledger_entry(
            agent_label, target_model, target_client, attempt, outcome, t0,
            timeout_seconds=_timeout, thinking_budget=target_budget, **kw, **move,
        ))

    def _unavailable(what: str, cause: BaseException) -> LLMFatalError:
        return LLMFatalError(
            f"All tool-capable providers exhausted: agent '{agent_label}': {what}: {cause}",
            agent=agent_label, unavailable=True, model_fallback=moves,
        )

    max_attempts = retries + 1
    attempt = -1
    while attempt + 1 < max_attempts:
        attempt += 1
        t0 = time.perf_counter()
        try:
            call_client, call_model, call_budget = target_client, target_model, target_budget
            result = _run_with_wall_clock(
                lambda: call_client.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    system=system,
                    model=call_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tool_choice=tool_choice,
                    cache_prompt=cache_prompt,
                    thinking_budget=call_budget,
                ),
                _timeout,
            )
            if _is_empty_turn(result):
                _log("empty_completion", t0, resp=_LedgerView(result))
                raise _EmptyCompletion(
                    f"empty tool turn from provider='{getattr(target_client, 'provider', None)}' "
                    f"model='{target_model}' stop_reason={(result or {}).get('stop_reason')!r}"
                )
        except LLMServiceUnavailableError as sue:
            print(
                f"[TOOL_LOOP][{agent_label}] 503 from "
                f"provider='{getattr(target_client, 'provider', None)}' model='{target_model}' "
                f"attempt {attempt + 1}/{max_attempts}: {sue}"
            )
            _log("service_unavailable", t0, err=sue)
            empty_turn = isinstance(sue, _EmptyCompletion)
            if _switch("empty" if empty_turn else "unavailable",
                       "empty turn" if empty_turn else "provider unavailable"):
                max_attempts += 1
                continue
            raise _unavailable("empty turn" if empty_turn else "provider unavailable", sue) from sue
        except LLMTimeoutError as te:
            timeout_attempts += 1
            _log("timeout", t0, err=te)
            _recycle_client_connections(target_client, agent_label)
            if timeout_attempts > timeout_retries:
                raise _unavailable("timeout", te) from te
            if timeout_retry_seconds:
                _timeout = timeout_retry_seconds
            # Same trigger as the structured path: a timeout moves to the next
            # tool-capable provider, once. With nowhere to move, the old
            # same-provider retry on a fresh socket stands.
            if _switch("timeout", "transport timeout"):
                max_attempts += 1
                continue
            if switches:
                raise _unavailable("timeout", te) from te
            continue
        except LLMRateLimitError as rle:
            _log("throttle", t0, err=rle)
            if _switch("rate_limited", "rate limited"):
                max_attempts += 1
                continue
            if switches or attempt + 1 >= max_attempts:
                raise LLMFatalError(
                    f"Rate limited (429): agent '{agent_label}', model '{target_model}': {rle}",
                    agent=agent_label, unavailable=True, model_fallback=moves,
                ) from rle
            try:
                time.sleep(rate_limit_sleep)
            except Exception:
                pass
            continue
        except LLMAPIConnectionError as ce:
            _log("error", t0, err=ce)
            _recycle_client_connections(target_client, agent_label)
            if _switch("connection", "connection error"):
                max_attempts += 1
                continue
            raise _unavailable("connection error", ce) from ce
        except LLMError as le:
            _log("error", t0, err=le)
            raise

        _log("ok", t0, resp=_LedgerView(result))
        return result

    raise LLMFatalError(
        f"Tool call exhausted its attempts: agent '{agent_label}'",
        agent=agent_label, unavailable=True, model_fallback=moves,
    )


class _LedgerView:
    """Adapt ``chat_with_tools``'s dict result to what ``_ledger_entry`` reads.

    ``_ledger_entry`` takes an ``LLMResponse``-shaped object (``.usage``,
    ``.metadata``); the tool surface returns a plain dict because its caller needs the
    content blocks. Rather than reshape either side, this presents the two attributes
    the ledger wants.
    """

    __slots__ = ("usage", "metadata")

    def __init__(self, result: dict[str, Any]):
        result = result if isinstance(result, dict) else {}
        self.usage = result.get("usage") or {}
        self.metadata = result.get("metadata") or {}
