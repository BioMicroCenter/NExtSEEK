from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from ..config import ChatConfig
from ..helpers import log_prompt, log_usage, log_llm_call, safe_parse_json
from ..llm_clients import (
    LLMAPIConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMServiceUnavailableError,
    LLMStructuredUnsupportedError,
    LLMFatalError,
    pydantic_to_tool_schema,
)

# Default timeout for LLM calls (5 minutes)
LLM_CALL_TIMEOUT_SECONDS = 300

# Default ceiling for the timeout RETRY, i.e. the attempt that follows
# `_recycle_client_connections`. A first attempt may legitimately be slow; a retry on a
# freshly dialled socket should not be, and until now it simply inherited the 300 s above,
# so one dead pooled socket could cost ten minutes before the call gave up.
#
# Measured on 2026-09-21 over the 2,638 successful calls in llm_calls.jsonl: p95 is at or
# under 17 s for every agent, and the two extreme successes in the whole ledger are entity
# at 167.3 s and graph_agent at 131.7 s. 180 s sits above both, so no call that has ever
# succeeded would be cut short, while the worst case for a stalled socket drops from
# 300 + 300 to 300 + 180.
#
# The same night gave the signature this exists for: memory_coder waited the full 300 s
# without the request ever being acknowledged, then the retry answered in 9.2 s on a new
# connection. The model was never slow; only the detection was.
#
# A caller that passes `timeout_retry_seconds` explicitly is untouched. `agents/parser.py`
# does, with 60 s against a deliberately short 35 s first attempt -- the opposite shape (a
# fast probe, then a patient retry), which is right for an agent whose p50 is 4.8 s.
#
# The one trap: this default is sized against the 300 s `timeout_seconds` above, so a caller
# that shortens `timeout_seconds` and leaves this alone gets a retry LONGER than its first
# attempt. Set both, as parser.py does. No caller in the tree does otherwise today.
TIMEOUT_RETRY_SECONDS = 180

# The raw response of every labelled call, one JSON line each, beside the ledger
# (llm_calls.jsonl) in LOG_DIR. The ledger says a call happened and how it ended; this
# file says what the model returned, which is the only evidence left when an output
# validates but carries nothing. It holds responses, not prompts: a prompt carries whole
# catalogs, several calls run per turn, and one file serves every turn of the process.
# Both a line and the file are capped; the file rolls over to `.1`, one generation kept.
RESPONSE_LOG_FILE = "llm_responses.jsonl"
RESPONSE_LOG_MAX_CHARS = 20_000
RESPONSE_LOG_MAX_BYTES = 64 * 1024 * 1024


class StructuredOutputError(Exception):
    def __init__(self, message: str, *, raw_output: str, errors: list[dict[str, Any]], model: Type[BaseModel]):
        super().__init__(message)
        self.raw_output = raw_output
        self.errors = errors
        self.model = model


# Fallback chains: (current_catalog_key, failed_provider) -> [ordered fallback profile keys]
_FALLBACK_CHAINS: dict[tuple[str, str], list[str]] = {
    ("default",      "gcp"):  ["anth:current", "gcp:lite", "anth:lite"],
    ("default",      "anth"): ["gcp:current",  "anth:lite", "gcp:lite"],
    ("gcp:current",  "gcp"):  ["anth:current", "gcp:lite",  "anth:lite"],
    ("gcp:lite",     "gcp"):  ["anth:lite",    "gcp:current", "anth:current"],
    ("anth:current", "anth"): ["gcp:current",  "anth:lite", "gcp:lite"],
    ("anth:lite",    "anth"): ["gcp:lite",     "anth:current", "gcp:current"],
}

# Two provider vocabularies exist and they are not the same.
#   * catalog vocabulary  — the `provider` field in agent_model_catalog.json and the keys
#     of config.LLM_CLIENTS: "gcp", "anth", "oai". _FALLBACK_CHAINS above is keyed on it.
#   * client vocabulary   — BaseLLMClient.provider on the concrete client classes:
#     "openai", "gcp", "anthropic", "bedrock". LLMError.provider surfaces it to logs.
# Only "gcp" coincides. Translate at the lookup site so a 503 from BedrockClient finds
# the ("default", "anth") chain instead of an empty list. Neither vocabulary is renamed:
# config.LLM_CLIENTS is keyed by the catalog one and the log lines report the client one.
_CLIENT_TO_CATALOG_PROVIDER: dict[str, str] = {
    "bedrock": "anth",
    "anthropic": "anth",
    "gcp": "gcp",
    "openai": "oai",
}


def _catalog_provider(client) -> str:
    """Translate a client's `provider` into the catalog vocabulary _FALLBACK_CHAINS uses.

    Unknown providers pass through unchanged: a client class that is not in the map yet
    must not be silently reclassified — it simply finds no chain and fails fast, exactly
    as it did before. See tests/test_llm_fallback_chain.py for the anti-drift lock.
    """
    raw = getattr(client, "provider", None) or ""
    return _CLIENT_TO_CATALOG_PROVIDER.get(raw, raw)


def _get_fallback_agent_configs(
    config: "ChatConfig",
    agent_label: str,
    failed_provider: str,
) -> list[tuple]:
    """
    Return an ordered list of (client, model_name, thinking_budget) tuples to try
    after a 503 from `failed_provider` for `agent_label`.
    Skips any fallback profile that lacks the required client credentials.
    """
    catalog_key = getattr(config, "_CATALOG_KEY", "default")
    fallback_profiles = _FALLBACK_CHAINS.get((catalog_key, failed_provider), [])

    results = []
    for profile in fallback_profiles:
        profile_catalog = config.AGENT_MODEL_CATALOG.get(profile, {})
        agent_cfg = profile_catalog.get(agent_label)
        if not agent_cfg:
            continue
        provider = agent_cfg.get("provider")
        model = agent_cfg.get("model") or config.LLM_MODEL
        thinking_level = agent_cfg.get("thinking_level")
        budget = config._THINKING_BUDGET_MAP.get(thinking_level) if thinking_level else None
        client = config.LLM_CLIENTS.get(provider) if provider else config.LLM_CLIENT
        if client is None:
            print(f"[FALLBACK] Skipping profile '{profile}' for agent '{agent_label}': provider '{provider}' client not available.")
            continue
        results.append((client, model, budget))
    return results


def _strip_code_fences(text: str) -> str:
    """
    Remove a surrounding markdown code fence, preserving the inner payload.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.split("\n", 1)
    if len(lines) > 1:
        return lines[1].rsplit("```", 1)[0].strip()

    stripped = stripped.strip("`").strip()
    if stripped.lower().startswith("json"):
        return stripped[4:].strip()
    return stripped


def _normalize_parsed_output(parsed: Any) -> Any:
    """
    Normalize common provider formatting quirks before Pydantic validation.
    In particular, some models wrap a single valid object in a top-level list.
    """
    if isinstance(parsed, list) and len(parsed) == 1:
        return parsed[0]
    return parsed


def empty_output_problem(value: BaseModel) -> str | None:
    """A ``result_check`` for a schema whose every field has a default.

    Such a schema validates ``{}``, and with extra keys ignored it also validates an
    object nested under a key it does not have and one passed as a string under such a
    key. Every one of those comes back as the defaults with ``model_fields_set`` empty:
    the output named none of the schema's fields, so it carries no answer, and a
    forced tool call on Bedrock can return exactly that. An output that names a field,
    even to say it is empty, is an answer and passes. Returns the reason for the repair
    turn, or None.
    """
    if getattr(value, "model_fields_set", None):
        return None
    name = type(value).__name__
    return (
        f"The output set none of the {name} fields: it was an empty object, or the "
        f"object was nested under a key {name} does not have. Return the {name} object "
        "itself with its keys at the top level; write a field that has nothing in it as "
        "an empty value rather than leaving it out."
    )


def _parse_model_output(raw_output: str, model: Type[BaseModel]) -> BaseModel:
    """
    Attempt to parse raw model text into a Pydantic model.
    Tries direct JSON parsing first, then falls back to safe JSON extraction when validation fails.
    Raises ValidationError when parsing cannot produce a valid instance.
    """
    normalized_output = _strip_code_fences(raw_output)
    try:
        return model.model_validate_json(normalized_output)
    except ValidationError as first_err:
        parsed = safe_parse_json(normalized_output)
        if parsed is None:
            raise first_err
        return model.model_validate(_normalize_parsed_output(parsed))


def _call_llm_with_timeout(
    client,
    model_name: str,
    temperature: float,
    messages: list[dict[str, str]],
    response_format: dict[str, Any] | None,
    timeout_seconds: float = LLM_CALL_TIMEOUT_SECONDS,
    thinking_budget: int | None = None,
    response_schema: dict | None = None,
    schema_name: str = "emit_result",
):
    """
    Execute an LLM call with a wall-clock timeout enforced via ThreadPoolExecutor.
    Returns the response on success or raises LLMTimeoutError when time is exceeded, preserving the calling signature.

    When the client can take a schema (`chat_structured`, currently Bedrock only) and
    one is supplied, the call goes out as a forced tool call instead of prompt-shaped
    JSON. `BedrockClient.chat` accepts `response_format` and never sends it, so that
    path had no output constraint at all. A model that rejects the schema-shaped
    request raises `LLMStructuredUnsupportedError` and the same call is retried plain,
    which is exactly the behaviour every call had before — the schema can only help.
    """
    def _do_call():
        if response_schema is not None and callable(getattr(client, "chat_structured", None)):
            system_text = "\n\n".join(
                str(m.get("content") or "") for m in messages if (m.get("role") or "").lower() == "system"
            )
            chat_messages = [m for m in messages if (m.get("role") or "").lower() != "system"]
            try:
                return client.chat_structured(
                    messages=chat_messages,
                    system=system_text or None,
                    model=model_name,
                    schema=response_schema,
                    schema_name=schema_name,
                    temperature=temperature,
                    thinking_budget=thinking_budget,
                )
            except LLMStructuredUnsupportedError as e:
                print(
                    f"[STRUCTURED_PARSE] model='{model_name}' rejected the schema-shaped "
                    f"request, retrying as plain text: {e}"
                )
        return client.chat(
            model=model_name,
            temperature=temperature,
            messages=messages,
            response_format=response_format,
            thinking_budget=thinking_budget,
        )

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_do_call)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError:
        # Don't block waiting for a stuck thread (common with some providers).
        try:
            future.cancel()
        except Exception:
            pass
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        raise LLMTimeoutError(f"LLM call timed out after {timeout_seconds} seconds")
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


def _structured_via(resp, client, response_format, thinking_budget) -> str | None:
    """How a response's structure was obtained, from what the call site holds.

      "tool_use"        a forced tool call, answered with the tool
      "tool_use_prose"  a forced tool call the model answered in text anyway: the
                        client hands the text back and still stamps its metadata
                        tool_use, so the returned blocks decide
      "json_mode"       a plain call with the provider's JSON mode on
      "prompt"          a plain call with JSON asked for in the prompt only
                        (BedrockClient.chat takes response_format and never sends it)
      None              a free-text call, which asked for no structure
    """
    meta = getattr(resp, "metadata", None) or {}
    via = meta.get("structured_via")
    if via == "tool_use":
        raw = getattr(resp, "raw", None)
        blocks = raw.get("content") if isinstance(raw, dict) else None
        if isinstance(blocks, list) and not any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks
        ):
            return "tool_use_prose"
        return "tool_use"
    if via:
        return str(via)
    if not (isinstance(response_format, dict) and response_format.get("type") == "json_object"):
        return None
    provider = getattr(client, "provider", None)
    if provider in ("gcp", "openai"):
        return "json_mode"
    if (
        provider == "anthropic"
        and thinking_budget is None
        and getattr(client, "_supports_response_format", None) is True
    ):
        return "json_mode"
    return "prompt"


def _reasoning_present(resp) -> bool | None:
    """Whether the response carried a reasoning (thinking) block; None when it cannot say.

    This is a block in the response, not whether the model thought: a provider that
    hides its reasoning reads as False or None.
    """
    meta = getattr(resp, "metadata", None) or {}
    count = meta.get("reasoning_blocks")
    if isinstance(count, int):
        return count > 0
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None)  # Anthropic Messages
    if isinstance(content, list):
        return any(getattr(b, "type", None) in ("thinking", "redacted_thinking") for b in content)
    candidates = getattr(raw, "candidates", None)  # Gemini, with thoughts included
    if isinstance(candidates, list) and candidates:
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
        return any(bool(getattr(p, "thought", False)) for p in parts)
    return None


def _ledger_entry(
    agent,
    model_name,
    client,
    attempt,
    outcome,
    t0,
    *,
    timeout_seconds=None,
    thinking_budget=None,
    resp=None,
    err=None,
    response_format=None,
    repair_turn=False,
    fallback_from=None,
    fallback_reason=None,
):
    """Build one LLM-ledger record (latency, provider metadata, outcome). Never raises.

    A record that has a response also says how its structure was obtained
    (``structured_via``), whether the request carried a repair turn after a rejected
    output (``repair_turn``), and whether the response held a reasoning block
    (``reasoning_present``).

    The first attempt after a provider move also carries ``fallback_from`` (the model
    that failed) and ``fallback_reason`` (``FALLBACK_REASONS``), so a turn's fallback
    calls can be listed and priced from the ledger alone. Every other record leaves
    both keys out.
    """
    entry: dict[str, Any] = {
        "agent": agent,
        "provider": getattr(client, "provider", None),
        "model": model_name,
        "attempt": attempt + 1,
        "outcome": outcome,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        "timeout_seconds": timeout_seconds,
        "thinking_budget": thinking_budget,
    }
    if fallback_from is not None:
        entry["fallback_from"] = fallback_from
    if fallback_reason is not None:
        entry["fallback_reason"] = fallback_reason
    try:
        if resp is not None:
            usage = getattr(resp, "usage", None) or {}
            entry["prompt_tokens"] = usage.get("prompt_tokens")
            entry["completion_tokens"] = usage.get("completion_tokens")
            meta = getattr(resp, "metadata", None) or {}
            entry["retry_attempts"] = meta.get("retry_attempts")
            entry["bedrock_latency_ms"] = meta.get("bedrock_latency_ms")
            entry["request_id"] = meta.get("request_id")
            entry["stop_reason"] = meta.get("stop_reason")
            entry["structured_via"] = _structured_via(resp, client, response_format, thinking_budget)
            entry["repair_turn"] = bool(repair_turn)
            entry["reasoning_present"] = _reasoning_present(resp)
        if err is not None:
            entry["error"] = f"{type(err).__name__}: {err}"
    except Exception:
        pass
    return entry


def _log_response(config, log_label: str, resp, text: str, attempt: int, msgs, extra) -> None:
    """Append one call's raw response to ``LOG_DIR/llm_responses.jsonl``. Never raises.

    ``log_prompt`` used to be handed ``config.LOG_DIR`` itself, a directory, so the open
    failed, the error was swallowed and no response was ever saved.
    """
    try:
        log_dir = getattr(config, "LOG_DIR", None)
        if not log_dir:
            return
        text = text or ""
        meta = getattr(resp, "metadata", None) or {}
        payload: dict[str, Any] = {
            "attempt": attempt,
            "model": getattr(resp, "model", None),
            "request_id": meta.get("request_id"),
            "response": text[:RESPONSE_LOG_MAX_CHARS],
            "response_chars": len(text),
            "messages_count": len(msgs or []),
            "messages_chars": sum(len(str((m or {}).get("content") or "")) for m in (msgs or [])),
        }
        if len(text) > RESPONSE_LOG_MAX_CHARS:
            payload["response_truncated"] = True
        if extra:
            payload.update(extra)
        log_prompt(
            os.path.join(log_dir, RESPONSE_LOG_FILE), log_label, payload,
            max_bytes=RESPONSE_LOG_MAX_BYTES,
        )
    except Exception as e:  # pragma: no cover - bookkeeping must not fail a turn
        print(f"[STRUCTURED_PARSE][{log_label}] response log skipped: {e!r}")


def _recycle_client_connections(client, label: str = "") -> None:
    """Ask a client to drop pooled TCP connections before a retry. Never raises."""
    reset = getattr(client, "reset_connections", None)
    if not callable(reset):
        return
    try:
        if reset():
            print(f"[STRUCTURED_PARSE][{label}] recycled pooled connections before retry")
    except Exception as e:
        print(f"[STRUCTURED_PARSE][{label}] connection recycle failed: {e!r}")


def _schema_tool_name(model: Type[BaseModel]) -> str:
    """A stable, Bedrock-legal tool name per schema.

    Bedrock caches a compiled grammar per schema for 24 hours, and the name is part of
    what the model sees, so it must not vary run to run. ``ParserPlan`` -> ``emit_parser_plan``.
    """
    import re as _re

    name = model.__name__.lstrip("_")
    snake = _re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    snake = _re.sub(r"[^a-z0-9_]", "_", snake).strip("_") or "result"
    return f"emit_{snake}"[:64]


# How many times one call may move to another provider. Operator rule (2026-09-23): a
# provider failure gets ONE move to the next provider in the chain, and when that also
# fails the caller gives the user the honest message. Before, a 503 walked the chain for
# as long as attempts remained, and a timeout never moved at all.
MAX_PROVIDER_SWITCHES = 1

# Why a call moved, as the ledger's ``fallback_reason`` and each ``model_fallback`` item
# record it: a timeout, an empty body, a 5xx, a 429 that survived the SDK's own retries,
# and a connection error.
FALLBACK_REASONS = ("timeout", "empty", "unavailable", "rate_limited", "connection")


class _EmptyCompletion(LLMServiceUnavailableError):
    """An empty body, raised inside the ladder so it takes the 5xx path, and recorded as
    ``empty`` rather than ``unavailable`` when the call moves on it."""


def _text_is_empty(resp) -> bool:
    """The free-text empty-body test: nothing but whitespace came back."""
    return not (getattr(resp, "content", None) or "").strip()


def _call_with_recovery(
    config: ChatConfig,
    *,
    base_messages: list[dict[str, str]],
    client,
    model_name: str,
    thinking_budget: int | None,
    response_format: dict[str, Any] | None,
    temperature: float,
    retries: int,
    timeout_seconds: float,
    timeout_retry_seconds: float | None,
    timeout_retries: int,
    rate_limit_sleep: float,
    agent_label: str | None,
    label: str,
    usage_label: str | None,
    on_response,
    response_schema: dict | None = None,
    schema_name: str = "emit_result",
    is_empty: Callable[[Any], bool] | None = None,
    chain_key: str | None = None,
) -> tuple[bool, Any]:
    """The provider-recovery ladder shared by every LLM call in the deterministic path.

    Runs the attempts and hands each successful response to
    ``on_response(resp, attempt, base_messages)``, which returns
    ``(done, value, next_messages)``. ``done=True`` returns ``(True, value)`` to the
    caller; ``done=False`` retries with ``next_messages`` (this is how the structured
    repair loop works). Returns ``(False, last_value)`` when the attempts run out, so
    the caller decides which error to raise.

    One trigger decides when a call moves to another provider. Five failures are
    fallback-eligible, and they are the same failure to the user: the provider gave no
    answer.

      * 5xx/overloaded (``LLMServiceUnavailableError``)
      * an empty body: ``is_empty(resp)`` is true (default: whitespace-only text)
      * a transport timeout (``LLMTimeoutError``)
      * a 429 (``LLMRateLimitError``): by the time one reaches here the SDK has already
        backed off and retried it (google-genai and botocore both do), so it moves like
        a 5xx (operator ruling 2026-09-25)
      * a connection error (``LLMAPIConnectionError``)

    On the first of them the call moves to the next provider in ``_FALLBACK_CHAINS``,
    once (``MAX_PROVIDER_SWITCHES``). When that provider fails too, the failure is
    final: the ``LLMTimeoutError`` itself for a timeout, which is what the parser maps
    to ``failure = transport_timeout``, and ``LLMFatalError`` with ``unavailable=True``
    for anything else. With no chain to move to, a 5xx or an empty body is that fatal at
    once, while a timeout keeps the old same-provider retry on a recycled socket, a 429
    the old backoff on the same provider, and a connection error propagates unchanged.
    Every such fatal names the move it made in ``model_fallback``.

    Budget: a provider move does not add a wait. A timeout's retry, same provider or
    next, runs on ``timeout_retry_seconds``, and there are still at most
    ``timeout_retries`` of them, so the parser's worst case stays 35 s + 60 s and the
    default stays 300 s + 180 s. The move only changes WHO the retry asks.

    Everything else is not eligible: a bare ``LLMError`` (a 400 validation error, say)
    is fatal at once and not ``unavailable``, and any other ``LLMError`` subclass
    propagates unchanged.

    ``agent_label`` is the name the ledger and the errors carry. ``chain_key`` is the
    catalog key the provider chain is looked up by, when it differs from that name
    (the graph agent's calls are ledgered as ``graph_agent`` but its chain is ``graph``).
    """
    chain_label = chain_key or agent_label
    target_client = client
    target_model_name = model_name
    target_thinking_budget = thinking_budget
    empty = is_empty or _text_is_empty

    chain: list[tuple] | None = None  # resolved on the first eligible failure
    switches = 0
    moves: list[dict] = []  # the move this call made, for a fatal's model_fallback
    pending_move: dict = {}  # fallback_from / fallback_reason for the next ledger record
    attempt_messages = base_messages
    timeout_attempts = 0
    last_value: Any = None
    # The first attempt runs on the tight budget. Once a timeout has told us the
    # connection was bad, the retry goes out on a fresh socket and gets more room.
    _timeout = timeout_seconds
    max_attempts = retries + 1
    attempt = -1

    def _switch_provider(reason: str, why: str) -> bool:
        """Move to the next provider in the chain, if this call still may. Never raises."""
        nonlocal chain, switches, target_client, target_model_name, target_thinking_budget, pending_move
        if switches >= MAX_PROVIDER_SWITCHES or not chain_label:
            return False
        if chain is None:
            chain = list(_get_fallback_agent_configs(config, chain_label, _catalog_provider(target_client)))
        if not chain:
            return False
        fb_client, fb_model, fb_budget = chain.pop(0)
        print(
            f"[STRUCTURED_PARSE][{label}] {why}: switching to fallback "
            f"provider='{getattr(fb_client, 'provider', '?')}' model='{fb_model}'"
        )
        moves.append({"agent": agent_label, "from": target_model_name, "to": fb_model, "reason": reason})
        pending_move = {"fallback_from": target_model_name, "fallback_reason": reason}
        target_client = fb_client
        target_model_name = fb_model
        target_thinking_budget = fb_budget
        switches += 1
        return True

    def _log(outcome: str, t0: float, **kw) -> None:
        """One ledger record for this attempt; the first one after a move names the move."""
        nonlocal pending_move
        move, pending_move = pending_move, {}
        log_llm_call(config.LOG_DIR, _ledger_entry(
            agent_label, target_model_name, target_client, attempt, outcome, t0, **kw, **move,
        ))

    while attempt + 1 < max_attempts:
        attempt += 1
        _t0 = time.perf_counter()
        try:
            resp = _call_llm_with_timeout(
                client=target_client,
                model_name=target_model_name,
                temperature=temperature,
                messages=attempt_messages,
                response_format=response_format,
                timeout_seconds=_timeout,
                thinking_budget=target_thinking_budget,
                response_schema=response_schema,
                schema_name=schema_name,
            )
            # An empty completion is a PROVIDER fault, not a schema error, and until
            # now it was treated as the latter: "" fails json parsing, so the repair
            # loop appended "your previous output did not validate" and asked the SAME
            # model again, twice, then gave up. Production turn 406 (bonniethiel,
            # 2026-08-28) is exactly that: us.anthropic.claude-opus-4-7 returned
            # completion=1 token on all three attempts and the user was told their
            # question could not be planned. Re-raising as 503 hands it to the provider
            # chain below, which is what the run needed: a different model.
            if empty(resp):
                _stop = (getattr(resp, "metadata", None) or {}).get("stop_reason")
                _log(
                    "empty_completion", _t0, timeout_seconds=timeout_seconds,
                    thinking_budget=target_thinking_budget, resp=resp,
                    response_format=response_format,
                    repair_turn=attempt_messages is not base_messages,
                )
                raise _EmptyCompletion(
                    f"empty completion (0 text tokens) from "
                    f"provider='{getattr(target_client, 'provider', None)}' "
                    f"model='{target_model_name}' stop_reason={_stop!r}"
                )
        except LLMServiceUnavailableError as sue:
            # Raw client vocabulary ("bedrock", "anthropic", "gcp", "openai") — this is
            # what an operator needs to see in the log during an outage. The chain
            # lookup needs the catalog vocabulary, which _switch_provider translates.
            failed_provider = getattr(target_client, "provider", None)
            print(
                f"[STRUCTURED_PARSE][{label}] 503 from provider='{failed_provider}' "
                f"model='{target_model_name}' attempt {attempt+1}/{max_attempts}: {sue}"
            )
            _log(
                "service_unavailable", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=sue,
            )
            empty_body = isinstance(sue, _EmptyCompletion)
            if _switch_provider("empty" if empty_body else "unavailable",
                                "empty body" if empty_body else "provider unavailable"):
                # The move gets an attempt of its own: it must never be the attempt
                # that ran out, which used to end a call as a parse error.
                max_attempts += 1
                continue
            # The one move is spent, or there is no chain — kill the run
            raise LLMFatalError(
                f"All provider fallbacks exhausted — agent '{agent_label}': {sue}",
                agent=agent_label,
                unavailable=True,
                model_fallback=moves,
            ) from sue
        except LLMTimeoutError as te:
            timeout_attempts += 1
            print(
                f"[STRUCTURED_PARSE][{label}] timeout on attempt {attempt+1}/{max_attempts} "
                f"(timeout retry {timeout_attempts}/{timeout_retries+1}) after {_timeout}s: {te}"
            )
            _log(
                "timeout", _t0, timeout_seconds=_timeout,
                thinking_budget=target_thinking_budget, err=te,
            )
            # A timeout here is usually a dead pooled socket rather than a slow model:
            # the request is never acknowledged at all. Whatever happens next, drop the
            # pool, so neither this call's retry nor the next agent on this client
            # draws the same dead connection (the 120.01s double-failure signature).
            _recycle_client_connections(target_client, label)
            if timeout_attempts > timeout_retries:
                raise
            if timeout_retry_seconds:
                _timeout = timeout_retry_seconds
            # The retry goes to the next provider when there is one: production task
            # 621 (2026-09-23) timed out on the parser at 35 s and again at 60 s on
            # the same provider, and nothing else was ever asked.
            if _switch_provider("timeout", "transport timeout"):
                max_attempts += 1
                continue
            if switches:
                # This call already moved once; the provider it moved to timed out.
                raise
            continue
        except LLMRateLimitError as rle:
            print(
                f"[STRUCTURED_PARSE][{label}] rate limit on attempt {attempt+1}/{max_attempts}: {rle}"
            )
            _log(
                "throttle", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=rle,
            )
            # A 429 here has already been backed off and retried by the SDK, so waiting
            # another second on the same provider rarely helps: it moves like a 5xx.
            if _switch_provider("rate_limited", "rate limited"):
                max_attempts += 1
                continue
            if switches:
                raise LLMFatalError(
                    f"All provider fallbacks exhausted: agent '{agent_label}': rate limited (429) "
                    f"on model '{target_model_name}': {rle}",
                    agent=agent_label,
                    unavailable=True,
                    model_fallback=moves,
                ) from rle
            # No chain: the old backoff on the same provider.
            if attempt + 1 >= max_attempts:
                raise LLMFatalError(
                    f"Rate limited (429) — agent '{agent_label}', model '{target_model_name}': {rle}",
                    agent=agent_label,
                    unavailable=True,
                    model_fallback=moves,
                ) from rle
            # brief backoff then retry
            try:
                time.sleep(rate_limit_sleep)
            except Exception:
                pass
            continue
        except LLMAPIConnectionError as ce:
            print(
                f"[STRUCTURED_PARSE][{label}] connection error on attempt {attempt+1}/{max_attempts}: {ce}"
            )
            _log(
                "error", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=ce,
            )
            _recycle_client_connections(target_client, label)
            if _switch_provider("connection", "connection error"):
                max_attempts += 1
                continue
            if switches:
                raise LLMFatalError(
                    f"All provider fallbacks exhausted: agent '{agent_label}': connection error "
                    f"on model '{target_model_name}': {ce}",
                    agent=agent_label,
                    unavailable=True,
                    model_fallback=moves,
                ) from ce
            # No chain: propagate unchanged, as before, for the callers that degrade on it.
            raise
        except LLMError as le:
            _log(
                "error", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=le,
            )
            # Bare LLMError only (subclasses are already handled above).
            # Unclassified errors are treated as unrecoverable — kill the run.
            if type(le) is not LLMError:
                raise
            raise LLMFatalError(
                f"Unrecoverable LLM error — agent '{agent_label}', model '{target_model_name}': {le}",
                agent=agent_label,
                model_fallback=moves,
            ) from le
        _log(
            "ok", _t0, timeout_seconds=timeout_seconds,
            thinking_budget=target_thinking_budget, resp=resp,
            response_format=response_format,
            repair_turn=attempt_messages is not base_messages,
        )
        if usage_label:
            log_usage(resp, usage_label)

        done, value, next_messages = on_response(resp, attempt, base_messages)
        last_value = value
        if done:
            return True, value
        attempt_messages = next_messages if next_messages is not None else attempt_messages

    return False, last_value


def call_llm_structured(
    config: ChatConfig,
    prompt: str,
    model: Type[BaseModel],
    *,
    system: str | None = None,
    retries: int = 2,
    model_name: str | None = None,
    messages: list[dict[str, str]] | None = None,
    temperature: float = 0,
    response_format: dict[str, Any] | None = None,
    log_label: str | None = None,
    log_payload_extra: dict[str, Any] | None = None,
    usage_label: str | None = None,
    rate_limit_sleep: float = 1.0,
    timeout_seconds: float = LLM_CALL_TIMEOUT_SECONDS,
    timeout_retry_seconds: float | None = TIMEOUT_RETRY_SECONDS,
    timeout_retries: int = 1,
    thinking_budget: int | None = None,
    client=None,
    agent_label: str | None = None,
    structured_via_tools: bool = True,
    result_check: Callable[[BaseModel], str | None] | None = None,
) -> BaseModel:
    """
    Call the LLM and parse into a structured Pydantic model with a repair loop.
    Includes timeout handling (default 300s) with one retry on timeout, and moves to the
    next provider on a timeout, a 5xx or an empty body (``_call_with_recovery``).

    ``structured_via_tools`` sends the schema to providers that can enforce a shape
    (a forced tool call on Bedrock) instead of asking for JSON in the prompt. It
    degrades to the prompt-shaped request on any provider that will not take it, so
    it is on by default; pass False to pin a call to the old behaviour.

    ``result_check`` is for a model whose fields all have defaults, where ``{}``
    validates. It gets the parsed result and returns None to accept it, or a reason,
    which is sent back through the repair turn exactly like a schema error. When no
    attempt passes, ``StructuredOutputError`` is raised as for any unparseable output.

    ``agent_label`` is the agent's catalog key, which the provider chain is looked up
    by; ``log_label`` is the name the ledger and the response log record. A call that
    passes only one of them uses it for both. A log label that is not a catalog key
    (``graph_agent``) finds no chain at all, so a call whose log label differs from its
    key must pass both.
    """
    base_messages: list[dict[str, str]] = []
    if messages is not None:
        base_messages = messages
    else:
        if system:
            base_messages.append({"role": "system", "content": system})
        base_messages.append({"role": "user", "content": prompt})

    rf = response_format if response_format is not None else {"type": "json_object"}
    _ledger_label = log_label or agent_label
    _chain_key = agent_label or log_label

    state: dict[str, Any] = {"raw_output": "", "errors": None}

    def _on_response(resp, attempt, msgs):
        raw_output = resp.content or ""
        state["raw_output"] = raw_output

        if log_label:
            _log_response(config, log_label, resp, raw_output, attempt, msgs, log_payload_extra)

        try:
            value = _parse_model_output(raw_output, model)
        except ValidationError as ve:
            state["errors"] = ve.errors()
        else:
            # A schema whose every field has a default validates `{}`, so "it parsed"
            # does not mean "it carried an answer". The caller says what an answer
            # must hold; a result that fails that goes through the same repair turn
            # as one that failed the schema.
            problem = result_check(value) if result_check is not None else None
            if not problem:
                return True, value, None
            state["errors"] = [{"type": "result_check", "msg": problem}]

        print(
            f"[STRUCTURED_PARSE][{model.__name__}] attempt {attempt+1}/{retries+1} "
            f"validation_errors={state['errors']} raw_output={raw_output!r}"
        )
        repair = msgs + [
            {"role": "assistant", "content": raw_output},
            {
                "role": "user",
                "content": (
                    f"Your previous output did not validate for schema {model.__name__}. "
                    f"Validation errors: {state['errors']}. "
                    "Re-output ONLY a corrected JSON object that satisfies the schema. "
                    "Do not wrap the object in a list or array. "
                    "Do not add commentary."
                ),
            },
        ]
        return False, None, repair

    def _empty_body(resp) -> bool:
        # A structured call expected content; a body that is nothing once whitespace and
        # a surrounding code fence are gone has none, so it goes to the next provider.
        # A body that parses, "{}" included, is NOT empty: an answer that carries nothing
        # gets the repair turn (result_check) exactly as before, which the empty-plan
        # guards pin (test_parser_empty_plan.py, test_structured_empty_output_guard.py).
        return not _strip_code_fences(getattr(resp, "content", None) or "").strip()

    schema = None
    if structured_via_tools:
        try:
            schema = pydantic_to_tool_schema(model)
        except Exception as e:  # a schema we cannot build is not a reason to fail the call
            print(f"[STRUCTURED_PARSE][{model.__name__}] could not build a tool schema: {e!r}")

    ok, value = _call_with_recovery(
        config,
        base_messages=base_messages,
        client=client or config.LLM_CLIENT,
        model_name=model_name or config.LLM_MODEL,
        thinking_budget=thinking_budget,
        response_format=rf,
        temperature=temperature,
        retries=retries,
        timeout_seconds=timeout_seconds,
        timeout_retry_seconds=timeout_retry_seconds,
        timeout_retries=timeout_retries,
        rate_limit_sleep=rate_limit_sleep,
        agent_label=_ledger_label,
        chain_key=_chain_key,
        label=model.__name__,
        usage_label=usage_label,
        on_response=_on_response,
        response_schema=schema,
        schema_name=_schema_tool_name(model),
        is_empty=_empty_body,
    )
    if ok:
        return value

    raise StructuredOutputError(
        f"Failed to parse structured output for {model.__name__}",
        raw_output=state["raw_output"],
        errors=state["errors"] or [],
        model=model,
    )


def call_llm_text(
    config: ChatConfig,
    *,
    messages: list[dict[str, str]],
    model_name: str | None = None,
    client=None,
    agent_label: str,
    temperature: float = 0,
    thinking_budget: int | None = None,
    retries: int = 2,
    timeout_seconds: float = LLM_CALL_TIMEOUT_SECONDS,
    timeout_retry_seconds: float | None = TIMEOUT_RETRY_SECONDS,
    timeout_retries: int = 1,
    rate_limit_sleep: float = 1.0,
    usage_label: str | None = None,
    log_label: str | None = None,
    log_payload_extra: dict[str, Any] | None = None,
) -> str:
    """Call the LLM for free text, with the same provider recovery as the structured path.

    This exists for the chatter, which writes prose and therefore cannot go through
    ``call_llm_structured``. Before this it called ``client.chat`` directly and had no
    retry, no provider fallback and no ledger entry, so a single 503 on the reply-writing
    step discarded an answer the engine had already computed. Returns the reply text; a
    5xx or an empty reply that survives the one provider move raises ``LLMFatalError``,
    and a timeout that survives it raises ``LLMTimeoutError``, exactly as for the
    structured agents; the caller decides what the user sees.
    """
    def _on_response(resp, attempt, msgs):
        text = resp.content or ""
        if log_label:
            _log_response(config, log_label, resp, text, attempt, msgs, log_payload_extra)
        return True, text, None

    ok, value = _call_with_recovery(
        config,
        base_messages=messages,
        client=client or config.LLM_CLIENT,
        model_name=model_name or config.LLM_MODEL,
        thinking_budget=thinking_budget,
        response_format=None,
        temperature=temperature,
        retries=retries,
        timeout_seconds=timeout_seconds,
        timeout_retry_seconds=timeout_retry_seconds,
        timeout_retries=timeout_retries,
        rate_limit_sleep=rate_limit_sleep,
        agent_label=agent_label,
        label=agent_label,
        usage_label=usage_label,
        on_response=_on_response,
    )
    return value if ok else ""
