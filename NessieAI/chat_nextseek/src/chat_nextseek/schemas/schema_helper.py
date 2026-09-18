from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from ..config import ChatConfig
from ..helpers import log_prompt, log_usage, log_llm_call, safe_parse_json
from ..llm_clients import (
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
):
    """Build one LLM-ledger record (latency, provider metadata, outcome). Never raises."""
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
        if err is not None:
            entry["error"] = f"{type(err).__name__}: {err}"
    except Exception:
        pass
    return entry


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
) -> tuple[bool, Any]:
    """The provider-recovery ladder shared by every LLM call in the deterministic path.

    Runs up to ``retries + 1`` attempts and hands each successful response to
    ``on_response(resp, attempt, base_messages)``, which returns
    ``(done, value, next_messages)``. ``done=True`` returns ``(True, value)`` to the
    caller; ``done=False`` retries with ``next_messages`` (this is how the structured
    repair loop works). Returns ``(False, last_value)`` when the attempts run out, so
    the caller decides which error to raise.

    The ladder itself handles four provider conditions, and the reason it is factored
    out is that until now only ``call_llm_structured`` had it: the chatter called
    ``client.chat`` bare and one 503 ended the turn with "Internal pipeline error"
    (production turns 463/464).

      * 5xx/overloaded  -> walk ``_FALLBACK_CHAINS`` to another provider, then fatal
      * empty completion -> re-raised as 5xx, see the comment at the raise site
      * transport timeout -> recycle the socket pool, retry once on a longer budget
      * 429              -> short backoff, retry, then fatal
    """
    target_client = client
    target_model_name = model_name
    target_thinking_budget = thinking_budget

    _fallback_iter: list[tuple] = []  # populated on first 503
    attempt_messages = base_messages
    timeout_attempts = 0
    last_value: Any = None
    # The first attempt runs on the tight budget. Once a timeout has told us the
    # connection was bad, the retry goes out on a fresh socket and gets more room.
    _timeout = timeout_seconds

    for attempt in range(retries + 1):
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
            if not (resp.content or "").strip():
                _stop = (getattr(resp, "metadata", None) or {}).get("stop_reason")
                log_llm_call(config.LOG_DIR, _ledger_entry(
                    agent_label, target_model_name, target_client, attempt,
                    "empty_completion", _t0, timeout_seconds=timeout_seconds,
                    thinking_budget=target_thinking_budget, resp=resp,
                ))
                raise LLMServiceUnavailableError(
                    f"empty completion (0 text tokens) from "
                    f"provider='{getattr(target_client, 'provider', None)}' "
                    f"model='{target_model_name}' stop_reason={_stop!r}"
                )
        except LLMServiceUnavailableError as sue:
            # Raw client vocabulary ("bedrock", "anthropic", "gcp", "openai") — this is
            # what an operator needs to see in the log during an outage. The chain
            # lookup below needs the catalog vocabulary, so keep the two separate.
            failed_provider = getattr(target_client, "provider", None)
            failed_catalog_provider = _catalog_provider(target_client)
            print(
                f"[STRUCTURED_PARSE][{label}] 503 from provider='{failed_provider}' "
                f"model='{target_model_name}' attempt {attempt+1}/{retries+1}: {sue}"
            )
            log_llm_call(config.LOG_DIR, _ledger_entry(
                agent_label, target_model_name, target_client, attempt,
                "service_unavailable", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=sue,
            ))
            # Build fallback list on first 503
            if not _fallback_iter and agent_label:
                _fallback_iter = _get_fallback_agent_configs(config, agent_label, failed_catalog_provider)
            if _fallback_iter:
                fb_client, fb_model, fb_budget = _fallback_iter.pop(0)
                print(
                    f"[STRUCTURED_PARSE][{label}] switching to fallback "
                    f"provider='{getattr(fb_client, 'provider', '?')}' model='{fb_model}'"
                )
                target_client = fb_client
                target_model_name = fb_model
                target_thinking_budget = fb_budget
                continue  # retry this attempt with new client/model
            # All fallback providers exhausted — kill the run
            raise LLMFatalError(
                f"All provider fallbacks exhausted — agent '{agent_label}': {sue}",
                agent=agent_label,
            ) from sue
        except LLMTimeoutError as te:
            timeout_attempts += 1
            print(
                f"[STRUCTURED_PARSE][{label}] timeout on attempt {attempt+1}/{retries+1} "
                f"(timeout retry {timeout_attempts}/{timeout_retries+1}) after {_timeout}s: {te}"
            )
            log_llm_call(config.LOG_DIR, _ledger_entry(
                agent_label, target_model_name, target_client, attempt,
                "timeout", _t0, timeout_seconds=_timeout,
                thinking_budget=target_thinking_budget, err=te,
            ))
            if timeout_attempts > timeout_retries:
                raise
            # A timeout here is usually a dead pooled socket rather than a slow model:
            # the request is never acknowledged at all. Retrying on the same pool can
            # draw another dead connection, which is exactly the 120.01s double-failure
            # signature in the logs, so force a fresh dial-out first.
            _recycle_client_connections(target_client, label)
            if timeout_retry_seconds:
                _timeout = timeout_retry_seconds
            continue
        except LLMRateLimitError as rle:
            print(
                f"[STRUCTURED_PARSE][{label}] rate limit on attempt {attempt+1}/{retries+1}: {rle}"
            )
            log_llm_call(config.LOG_DIR, _ledger_entry(
                agent_label, target_model_name, target_client, attempt,
                "throttle", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=rle,
            ))
            if attempt >= retries:
                raise LLMFatalError(
                    f"Rate limited (429) — agent '{agent_label}', model '{target_model_name}': {rle}",
                    agent=agent_label,
                ) from rle
            # brief backoff then retry
            try:
                time.sleep(rate_limit_sleep)
            except Exception:
                pass
            continue
        except LLMError as le:
            log_llm_call(config.LOG_DIR, _ledger_entry(
                agent_label, target_model_name, target_client, attempt,
                "error", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=le,
            ))
            # Bare LLMError only (subclasses are already handled above).
            # Unclassified errors are treated as unrecoverable — kill the run.
            if type(le) is not LLMError:
                raise
            raise LLMFatalError(
                f"Unrecoverable LLM error — agent '{agent_label}', model '{target_model_name}': {le}",
                agent=agent_label,
            ) from le
        log_llm_call(config.LOG_DIR, _ledger_entry(
            agent_label, target_model_name, target_client, attempt,
            "ok", _t0, timeout_seconds=timeout_seconds,
            thinking_budget=target_thinking_budget, resp=resp,
        ))
        if usage_label:
            log_usage(resp, usage_label)

        done, value, next_messages = on_response(resp, attempt, base_messages)
        last_value = value
        if done:
            return True, value
        if attempt >= retries:
            break
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
    timeout_retry_seconds: float | None = None,
    timeout_retries: int = 1,
    thinking_budget: int | None = None,
    client=None,
    agent_label: str | None = None,
    structured_via_tools: bool = True,
    result_check: Callable[[BaseModel], str | None] | None = None,
) -> BaseModel:
    """
    Call the LLM and parse into a structured Pydantic model with a repair loop.
    Includes timeout handling (default 300s) with automatic retry on timeout.

    ``structured_via_tools`` sends the schema to providers that can enforce a shape
    (a forced tool call on Bedrock) instead of asking for JSON in the prompt. It
    degrades to the prompt-shaped request on any provider that will not take it, so
    it is on by default; pass False to pin a call to the old behaviour.

    ``result_check`` is for a model whose fields all have defaults, where ``{}``
    validates. It gets the parsed result and returns None to accept it, or a reason,
    which is sent back through the repair turn exactly like a schema error. When no
    attempt passes, ``StructuredOutputError`` is raised as for any unparseable output.
    """
    base_messages: list[dict[str, str]] = []
    if messages is not None:
        base_messages = messages
    else:
        if system:
            base_messages.append({"role": "system", "content": system})
        base_messages.append({"role": "user", "content": prompt})

    rf = response_format if response_format is not None else {"type": "json_object"}
    _effective_agent_label = agent_label or log_label

    state: dict[str, Any] = {"raw_output": "", "errors": None}

    def _on_response(resp, attempt, msgs):
        raw_output = resp.content or ""
        state["raw_output"] = raw_output

        if log_label:
            payload = {"messages": msgs, "response": raw_output, "attempt": attempt}
            if log_payload_extra:
                payload.update(log_payload_extra)
            log_prompt(config.LOG_DIR, log_label, payload)

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
        agent_label=_effective_agent_label,
        label=model.__name__,
        usage_label=usage_label,
        on_response=_on_response,
        response_schema=schema,
        schema_name=_schema_tool_name(model),
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
    timeout_retry_seconds: float | None = None,
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
    provider failure that survives the whole chain raises ``LLMFatalError`` exactly as it
    does for the structured agents, and the caller decides what the user sees.
    """
    def _on_response(resp, attempt, msgs):
        text = resp.content or ""
        if log_label:
            payload = {"messages": msgs, "response": text, "attempt": attempt}
            if log_payload_extra:
                payload.update(log_payload_extra)
            log_prompt(config.LOG_DIR, log_label, payload)
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
