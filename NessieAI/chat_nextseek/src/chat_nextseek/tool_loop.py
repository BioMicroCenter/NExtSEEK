"""One tool-enabled model turn, with recovery, caching and a ledger entry.

``BedrockClient.chat_with_tools`` is the only tool-use surface in this package, and
until now the only caller was the pipeline agent, which called it bare inside a twelve
iteration loop: no retry, no provider fallback, no ledger entry, and no prompt cache.
One 503 anywhere in a build ended it, and the tokens the loop spent were invisible.

This module is that call done once, properly, so a second tool loop (the follow-up
agent) does not repeat the mistakes:

* **Recovery.** A timeout, an empty turn, a 503, a 429, a connection error and a model
  the provider refused (``model_unusable``) move once to the next provider, the same
  trigger ``_call_with_recovery`` uses, skipping
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
  traffic clusters inside the TTL and so stays opt-in. The call after a provider move
  goes out without one: the fallback model has never been sent a cache point on a
  production path, a rejection would be a bare ValidationException that does not
  move, and one call's cache is worth nothing.
* **A ledger entry per call**, including cache hits, so a loop's cost is measurable.
  The first call after a move also names it (``fallback_from``, ``fallback_reason``).
  A call that ended in an exception nothing above types (a raw ``ClientError``) still
  gets its entry, and still propagates unchanged.
"""
from __future__ import annotations

import time
from typing import Any

from . import turn_spend
from .helpers import log_llm_call
from .llm_clients import (
    LLMAPIConnectionError,
    LLMError,
    LLMFatalError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from .schemas.call_budgets import TOOL_LOOP_DEFAULT_BUDGET, budget_for
from .schemas.schema_helper import (
    MAX_PROVIDER_SWITCHES,  # noqa: F401 (re-exported: the one-move rule both surfaces follow)
    _EmptyCompletion,
    _Failover,
    _ledger_entry,
    _recycle_client_connections,
    _run_with_wall_clock,
    _unavailable_kind,
    failure_reason,
)

# The wall clock on one tool-loop call comes from the agent's row of ``call_budgets.CALL_BUDGETS``
# (operator ruling 2026-09-28): the follow-up 60 s and 60 s for the move (41 Opus 4.7 calls, 2026-09-22:
# max 9.8 s, answers up to 259 tokens), the pipeline agent 120 s and 120 s (a write_samplesheet call can be
# several thousand tokens, and the move regenerates the whole output). A loop the table does not name keeps
# these, the defaults every loop had before.
TOOL_CALL_TIMEOUT_SECONDS = TOOL_LOOP_DEFAULT_BUDGET.first_try_s
TOOL_CALL_TIMEOUT_RETRY_SECONDS = TOOL_LOOP_DEFAULT_BUDGET.moved_s


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
    timeout_seconds: float | None = None,
    timeout_retry_seconds: float | None = None,
    timeout_retries: int = 1,
) -> dict:
    """Run one tool-enabled turn and return the normalized Converse result.

    Returns ``{"stop_reason", "content", "usage", "metadata"}``. Raises
    ``LLMFatalError`` (``unavailable=True``) when the provider it moved to failed too,
    or when there was nowhere to move, so the caller reports one honest failure rather
    than looping on a dead provider. A bare ``LLMError`` propagates unchanged.

    ``timeout_seconds`` (the first try) and ``timeout_retry_seconds`` (the moved call, whatever the
    failure was, and a timeout's same-provider retry) default to the agent's row of
    ``call_budgets.CALL_BUDGETS``.
    """
    _budget = budget_for(agent_label, default=TOOL_LOOP_DEFAULT_BUDGET)
    if timeout_seconds is None:
        timeout_seconds = _budget.first_try_s
    if timeout_retry_seconds is None:
        timeout_retry_seconds = _budget.moved_s
    fo = _Failover(
        config, agent_label=agent_label, chain_label=agent_label, client=client, model=model_name,
        thinking_budget=thinking_budget, first_s=timeout_seconds, moved_s=timeout_retry_seconds,
        log_prefix=f"[TOOL_LOOP][{agent_label}]",
        # A tool loop cannot fail over to a client with no tool surface: the conversation so
        # far is tool_use and tool_result blocks.
        accept=_tool_capable,
        timeout_marks=_budget.timeout_marks_model,
    )
    # A model that failed earlier in this turn (call_scope) is not asked again: a stalled
    # Opus used to cost its first try on every step of a twelve-step build.
    fo.begin()
    attempt_recorded = False  # whether this attempt has its ledger record yet
    timeout_attempts = 0
    _timeout = fo.window

    def _log(outcome: str, t0: float, **kw) -> None:
        nonlocal attempt_recorded
        attempt_recorded = True
        entry = _ledger_entry(
            agent_label, fo.model, fo.client, attempt, outcome, t0,
            timeout_seconds=_timeout, thinking_budget=fo.budget, deadline_capped=fo.capped,
            **kw, **fo.take_pending(),
        )
        _ledger(config, entry)
        # This turn's cost collector prices the usage, or counts an abandoned attempt.
        turn_spend.record_call(entry, resp=kw.get("resp"), err=kw.get("err"))

    def _unavailable(what: str, cause: BaseException) -> LLMFatalError:
        return fo.fatal(f"All tool-capable providers exhausted: agent '{agent_label}': {what}: {cause}",
                        reason=failure_reason(cause))

    max_attempts = retries + 1
    attempt = -1
    while attempt + 1 < max_attempts:
        attempt += 1
        fo.attempt = attempt
        _timeout = fo.attempt_window()  # an op's deadline, if any, cuts it; past the deadline no call starts
        t0 = time.perf_counter()
        attempt_recorded = False
        try:
            call_client, call_model, call_budget = fo.client, fo.model, fo.budget
            # No cache point once the call has moved (see the module docstring).
            call_cache = cache_prompt and not fo.switches
            result = _run_with_wall_clock(
                lambda: call_client.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    system=system,
                    model=call_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tool_choice=tool_choice,
                    cache_prompt=call_cache,
                    thinking_budget=call_budget,
                ),
                _timeout,
            )
            if _is_empty_turn(result):
                _log("empty_completion", t0, resp=_LedgerView(result))
                raise _EmptyCompletion(
                    f"empty tool turn from provider='{getattr(fo.client, 'provider', None)}' "
                    f"model='{fo.model}' stop_reason={(result or {}).get('stop_reason')!r}"
                )
        except LLMServiceUnavailableError as sue:
            outcome, reason, why = _unavailable_kind(sue)
            why = "empty turn" if isinstance(sue, _EmptyCompletion) else why
            status_word = "model refused" if reason == "model_unusable" else "503"
            print(
                f"[TOOL_LOOP][{agent_label}] {status_word} from "
                f"provider='{getattr(fo.client, 'provider', None)}' model='{fo.model}' "
                f"attempt {attempt + 1}/{max_attempts}: {sue}"
            )
            _log(outcome, t0, err=sue)
            fo.mark(reason)
            if fo.move(reason, why):
                max_attempts += 1
                continue
            raise _unavailable(why, sue) from sue
        except LLMTimeoutError as te:
            timeout_attempts += 1
            _log("timeout", t0, err=te)
            fo.mark("timeout")
            _recycle_client_connections(fo.client, agent_label)
            if timeout_attempts > timeout_retries or (fo.switches and fo.capped):
                if fo.capped:
                    # The op's deadline cut this window: it ran out of time, not a model.
                    raise fo.deadline_fatal(te) from te
                raise _unavailable("timeout", te) from te
            if timeout_retry_seconds:
                fo.window = timeout_retry_seconds
            # Same trigger as the structured path: a timeout moves to the next
            # tool-capable provider, once. With nowhere to move, the old
            # same-provider retry on a fresh socket stands.
            if fo.move("timeout", "transport timeout"):
                max_attempts += 1
                continue
            if fo.switches:
                raise _unavailable("timeout", te) from te
            continue
        except LLMRateLimitError as rle:
            _log("throttle", t0, err=rle)
            fo.mark("rate_limited")
            if fo.move("rate_limited", "rate limited"):
                max_attempts += 1
                continue
            if fo.switches or attempt + 1 >= max_attempts:
                raise fo.fatal(
                    f"Rate limited (429): agent '{agent_label}', model '{fo.model}': {rle}",
                    reason="rate_limited",
                ) from rle
            try:
                time.sleep(rate_limit_sleep)
            except Exception:
                pass
            continue
        except LLMAPIConnectionError as ce:
            _log("error", t0, err=ce)
            fo.mark("connection")
            _recycle_client_connections(fo.client, agent_label)
            if fo.move("connection", "connection error"):
                max_attempts += 1
                continue
            raise _unavailable("connection error", ce) from ce
        except LLMError as le:
            _log("error", t0, err=le)
            raise
        except BaseException as unrecorded:
            # Anything else (a raw ClientError the client did not type, a bug in a client)
            # propagates unchanged and never moves, but the attempt still gets its one
            # ledger record, and the turn's cost collector counts it as unobserved (F1).
            if not attempt_recorded:
                _log("error", t0, err=unrecorded)
            raise

        _log("ok", t0, resp=_LedgerView(result))
        return result

    raise fo.fatal(f"Tool call exhausted its attempts: agent '{agent_label}'", reason=None)


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
