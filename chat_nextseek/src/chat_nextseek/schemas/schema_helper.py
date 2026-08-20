from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from ..config import ChatConfig
from ..helpers import log_prompt, log_usage, log_llm_call, safe_parse_json
from ..llm_clients import LLMError, LLMRateLimitError, LLMTimeoutError, LLMServiceUnavailableError, LLMFatalError

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
):
    """
    Execute an LLM call with a wall-clock timeout enforced via ThreadPoolExecutor.
    Returns the response on success or raises LLMTimeoutError when time is exceeded, preserving the calling signature.
    """
    def _do_call():
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
) -> BaseModel:
    """
    Call the LLM and parse into a structured Pydantic model with a repair loop.
    Includes timeout handling (default 300s) with automatic retry on timeout.
    """
    base_messages: list[dict[str, str]] = []
    if messages is not None:
        base_messages = messages
    else:
        if system:
            base_messages.append({"role": "system", "content": system})
        base_messages.append({"role": "user", "content": prompt})

    target_client = client or config.LLM_CLIENT
    target_model_name = model_name or config.LLM_MODEL
    target_thinking_budget = thinking_budget
    rf = response_format if response_format is not None else {"type": "json_object"}

    # Pre-compute the fallback sequence once (empty list if no agent_label or no chain defined)
    _effective_agent_label = agent_label or log_label
    _fallback_iter: list[tuple] = []  # populated on first 503

    last_errors: list[dict[str, Any]] | None = None
    raw_output = ""
    attempt_messages = base_messages
    timeout_attempts = 0
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
                response_format=rf,
                timeout_seconds=_timeout,
                thinking_budget=target_thinking_budget,
            )
        except LLMServiceUnavailableError as sue:
            # Raw client vocabulary ("bedrock", "anthropic", "gcp", "openai") — this is
            # what an operator needs to see in the log during an outage. The chain
            # lookup below needs the catalog vocabulary, so keep the two separate.
            failed_provider = getattr(target_client, "provider", None)
            failed_catalog_provider = _catalog_provider(target_client)
            print(
                f"[STRUCTURED_PARSE][{model.__name__}] 503 from provider='{failed_provider}' "
                f"model='{target_model_name}' attempt {attempt+1}/{retries+1}: {sue}"
            )
            log_llm_call(config.LOG_DIR, _ledger_entry(
                _effective_agent_label, target_model_name, target_client, attempt,
                "service_unavailable", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=sue,
            ))
            # Build fallback list on first 503
            if not _fallback_iter and _effective_agent_label:
                _fallback_iter = _get_fallback_agent_configs(config, _effective_agent_label, failed_catalog_provider)
            if _fallback_iter:
                fb_client, fb_model, fb_budget = _fallback_iter.pop(0)
                print(
                    f"[STRUCTURED_PARSE][{model.__name__}] switching to fallback "
                    f"provider='{getattr(fb_client, 'provider', '?')}' model='{fb_model}'"
                )
                target_client = fb_client
                target_model_name = fb_model
                target_thinking_budget = fb_budget
                continue  # retry this attempt with new client/model
            # All fallback providers exhausted — kill the run
            raise LLMFatalError(
                f"All provider fallbacks exhausted — agent '{_effective_agent_label}': {sue}",
                agent=_effective_agent_label,
            ) from sue
        except LLMTimeoutError as te:
            timeout_attempts += 1
            print(
                f"[STRUCTURED_PARSE][{model.__name__}] timeout on attempt {attempt+1}/{retries+1} "
                f"(timeout retry {timeout_attempts}/{timeout_retries+1}) after {_timeout}s: {te}"
            )
            log_llm_call(config.LOG_DIR, _ledger_entry(
                _effective_agent_label, target_model_name, target_client, attempt,
                "timeout", _t0, timeout_seconds=_timeout,
                thinking_budget=target_thinking_budget, err=te,
            ))
            if timeout_attempts > timeout_retries:
                raise
            # A timeout here is usually a dead pooled socket rather than a slow model:
            # the request is never acknowledged at all. Retrying on the same pool can
            # draw another dead connection, which is exactly the 120.01s double-failure
            # signature in the logs, so force a fresh dial-out first.
            _recycle_client_connections(target_client, model.__name__)
            if timeout_retry_seconds:
                _timeout = timeout_retry_seconds
            continue
        except LLMRateLimitError as rle:
            print(
                f"[STRUCTURED_PARSE][{model.__name__}] rate limit on attempt {attempt+1}/{retries+1}: {rle}"
            )
            log_llm_call(config.LOG_DIR, _ledger_entry(
                _effective_agent_label, target_model_name, target_client, attempt,
                "throttle", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=rle,
            ))
            if attempt >= retries:
                raise LLMFatalError(
                    f"Rate limited (429) — agent '{_effective_agent_label}', model '{target_model_name}': {rle}",
                    agent=_effective_agent_label,
                ) from rle
            # brief backoff then retry
            try:
                time.sleep(rate_limit_sleep)
            except Exception:
                pass
            continue
        except LLMError as le:
            log_llm_call(config.LOG_DIR, _ledger_entry(
                _effective_agent_label, target_model_name, target_client, attempt,
                "error", _t0, timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget, err=le,
            ))
            # Bare LLMError only (subclasses are already handled above).
            # Unclassified errors are treated as unrecoverable — kill the run.
            if type(le) is not LLMError:
                raise
            raise LLMFatalError(
                f"Unrecoverable LLM error — agent '{_effective_agent_label}', model '{target_model_name}': {le}",
                agent=_effective_agent_label,
            ) from le
        log_llm_call(config.LOG_DIR, _ledger_entry(
            _effective_agent_label, target_model_name, target_client, attempt,
            "ok", _t0, timeout_seconds=timeout_seconds,
            thinking_budget=target_thinking_budget, resp=resp,
        ))
        if usage_label:
            log_usage(resp, usage_label)
        raw_output = resp.content or ""

        if log_label:
            payload = {"messages": attempt_messages, "response": raw_output, "attempt": attempt}
            if log_payload_extra:
                payload.update(log_payload_extra)
            log_prompt(config.LOG_DIR, log_label, payload)

        try:
            return _parse_model_output(raw_output, model)
        except ValidationError as ve:
            last_errors = ve.errors()
            print(
                f"[STRUCTURED_PARSE][{model.__name__}] attempt {attempt+1}/{retries+1} "
                f"validation_errors={last_errors} raw_output={raw_output!r}"
            )
            if attempt >= retries:
                break
            attempt_messages = base_messages + [
                {"role": "assistant", "content": raw_output},
                {
                    "role": "user",
                    "content": (
                        f"Your previous output did not validate for schema {model.__name__}. "
                        f"Validation errors: {last_errors}. "
                        "Re-output ONLY a corrected JSON object that satisfies the schema. "
                        "Do not wrap the object in a list or array. "
                        "Do not add commentary."
                    ),
                },
            ]
            continue

    raise StructuredOutputError(
        f"Failed to parse structured output for {model.__name__}",
        raw_output=raw_output,
        errors=last_errors or [],
        model=model,
    )
