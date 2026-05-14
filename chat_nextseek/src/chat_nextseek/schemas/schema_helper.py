from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from ..config import ChatConfig
from ..helpers import log_prompt, log_usage, safe_parse_json
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

    for attempt in range(retries + 1):
        try:
            resp = _call_llm_with_timeout(
                client=target_client,
                model_name=target_model_name,
                temperature=temperature,
                messages=attempt_messages,
                response_format=rf,
                timeout_seconds=timeout_seconds,
                thinking_budget=target_thinking_budget,
            )
        except LLMServiceUnavailableError as sue:
            failed_provider = getattr(target_client, "provider", None)
            print(
                f"[STRUCTURED_PARSE][{model.__name__}] 503 from provider='{failed_provider}' "
                f"model='{target_model_name}' attempt {attempt+1}/{retries+1}: {sue}"
            )
            # Build fallback list on first 503
            if not _fallback_iter and _effective_agent_label:
                _fallback_iter = _get_fallback_agent_configs(config, _effective_agent_label, failed_provider or "")
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
                f"(timeout retry {timeout_attempts}/{timeout_retries+1}): {te}"
            )
            if timeout_attempts > timeout_retries:
                raise
            # Retry the same attempt after timeout
            continue
        except LLMRateLimitError as rle:
            print(
                f"[STRUCTURED_PARSE][{model.__name__}] rate limit on attempt {attempt+1}/{retries+1}: {rle}"
            )
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
            # Bare LLMError only (subclasses are already handled above).
            # Unclassified errors are treated as unrecoverable — kill the run.
            if type(le) is not LLMError:
                raise
            raise LLMFatalError(
                f"Unrecoverable LLM error — agent '{_effective_agent_label}', model '{target_model_name}': {le}",
                agent=_effective_agent_label,
            ) from le
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
