from __future__ import annotations

"""
Lightweight provider adapters so agents can call OpenAI, GCP Gemini, or Anthropic
without changing prompts or core logic.
"""

import dataclasses
import inspect
import re
from typing import Any, List

__all__ = [
    "LLMError",
    "LLMRateLimitError",
    "LLMAPIConnectionError",
    "LLMTimeoutError",
    "LLMServiceUnavailableError",
    "LLMModelUnusableError",
    "LLMStructuredUnsupportedError",
    "LLMFatalError",
    "LLMResponse",
    "BaseLLMClient",
    "OpenAIClient",
    "GeminiClient",
    "AnthropicClient",
    "BedrockClient",
    "pydantic_to_tool_schema",
    "build_llm_client",
]


class LLMError(Exception):
    """Base error for LLM providers."""


class LLMRateLimitError(LLMError):
    """Raised on provider rate limiting."""


class LLMAPIConnectionError(LLMError):
    """Raised on transport/connection failures."""


class LLMTimeoutError(LLMError):
    """Raised when LLM call exceeds timeout."""


class LLMFatalError(BaseException):
    """Unrecoverable LLM error — kills the run immediately and surfaces the message to the user.
    Inherits from BaseException (not Exception) so it bypasses all bare 'except Exception'
    handlers in agent code and propagates straight to the orchestrator.
    Set by: rate limits (429) after all retries, and unclassified bare LLMError.

    ``unavailable`` is True when the call ended because the models did not answer (a 5xx,
    an empty body, a 429, a timeout or a connection error, on the one provider move as
    well when there was one), and False for anything else (a bare 400). The orchestrator
    tells the user a different thing for each. ``model_fallback`` lists the provider
    move the call made before it gave up, as ``{"agent", "from", "to", "reason"}``
    items; it is empty when no second model was tried.
    """
    def __init__(
        self,
        message: str,
        *,
        agent: str | None = None,
        unavailable: bool = False,
        model_fallback: list[dict] | None = None,
    ):
        super().__init__(message)
        self.agent = agent
        self.unavailable = bool(unavailable)
        self.model_fallback = list(model_fallback or [])


class LLMStructuredUnsupportedError(LLMError):
    """The model rejected a schema-constrained request, but would accept a plain one.

    Raised when Bedrock returns a ValidationException naming toolConfig/toolChoice or
    the thinking/tool-choice combination. It is NOT a provider outage and must not
    trip the fallback chain: the caller retries the same model without the schema and
    falls back to prompt-shaped JSON, which is what every call did before.
    """


class LLMServiceUnavailableError(LLMError):
    """Raised on 5xx / service temporarily unavailable or overloaded — triggers provider fallback.
    Covers: 500 Internal Server Error, 502 Bad Gateway, 503 Unavailable, 504 Gateway Timeout,
    and provider-specific equivalents (ModelNotReadyException, InternalServerException, etc.)
    """


class LLMModelUnusableError(LLMServiceUnavailableError):
    """The provider refused the model itself, so the request never ran.

    Bedrock's ``ResourceNotFoundException`` (a retired model), ``AccessDeniedException``
    (a model this account may not use) and a ``ValidationException`` about the model id
    (invalid, not supported, not enabled). Another model may well answer the same request,
    so the recovery ladder moves on it once, like a 5xx, and records the reason
    ``model_unusable`` (operator ruling 2026-09-25, F1). Before, the forced tool call let
    these leave the ladder as a raw ``ClientError`` and the plain call made them a bare
    ``LLMError``: neither moved.
    """


@dataclasses.dataclass
class LLMResponse:
    content: str
    raw: Any
    usage: dict | None
    model: str
    provider: str
    metadata: dict | None = None

class BaseLLMClient:
    provider: str = "unknown"

    def chat(
        self,
        *,
        messages: List[dict],
        model: str,
        temperature: float = 0,
        response_format: dict | None = None,
        thinking_budget: int | None = None,
    ) -> LLMResponse:  # pragma: no cover - implemented by subclasses
        """Send a chat request and return normalized content, raw response, and usage metadata."""
        raise NotImplementedError


class OpenAIClient(BaseLLMClient):
    provider = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        """Create an OpenAI-compatible client, normalizing custom base URLs when provided."""
        from openai import OpenAI

        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        # Determine base URL from explicit arg or env, then normalize to include scheme.
        # This guards against envs like OPENAI_BASE_URL=api.domain.com (missing https://).
        env_base = None
        try:
            import os

            env_base = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        except Exception:
            pass

        cleaned_base_url = None
        candidate_base = base_url or env_base
        if candidate_base:
            candidate = candidate_base.strip()
            if candidate and "://" not in candidate:
                candidate = "https://" + candidate
            if candidate:
                cleaned_base_url = candidate
                kwargs["base_url"] = cleaned_base_url
        # keep a copy so we can surface it in connection errors
        self.base_url = cleaned_base_url or "default"
        self.client = OpenAI(**kwargs)

    def chat(
        self,
        *,
        messages: List[dict],
        model: str,
        temperature: float = 0,
        response_format: dict | None = None,
        thinking_budget: int | None = None,
    ) -> LLMResponse:
        """Call the OpenAI chat-completions API and map provider exceptions to local error types."""
        from openai import APIConnectionError, RateLimitError

        try:
            resp = self.client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=messages,
                response_format=response_format,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except APIConnectionError as e:
            detail_parts = [str(e)]
            cause = getattr(e, "__cause__", None)
            if cause:
                detail_parts.append(f"cause={cause}")
            detail_parts.append(f"base_url={self.base_url}")
            detail_parts.append(f"model={model}")
            raise LLMAPIConnectionError("; ".join(detail_parts)) from e
        except Exception as e:
            msg = str(e)
            status = getattr(e, "status_code", None)
            if status in (500, 502, 503, 504) or any(code in msg for code in ("500", "502", "503", "504")):
                raise LLMServiceUnavailableError(msg) from e
            raise LLMError(msg) from e

        usage = None
        try:
            usage = resp.usage
        except Exception:
            pass
        content = resp.choices[0].message.content if resp and resp.choices else ""
        return LLMResponse(
            content=content or "",
            raw=resp,
            usage=usage,
            model=model,
            provider=self.provider,
        )


def _is_gemini_rate_limit(exc: BaseException, msg: str) -> bool:
    """A 429 from google-genai: ``APIError.code`` when the SDK set one, else the status text."""
    if getattr(exc, "code", None) == 429:
        return True
    head = msg.lstrip()
    return head.startswith("429") or "RESOURCE_EXHAUSTED" in msg.upper()


def _gemini_transport_error(exc: BaseException) -> LLMError | None:
    """The typed error for an httpx transport failure under google-genai, or None.

    google-genai re-raises httpx's own exceptions (its retry policy reraises), and they
    used to fall through to a bare ``LLMError``, which the ladder treats as an
    unrecoverable 400: a Gemini stall or a dropped connection ended the turn without a
    move. A timeout (``httpx.TimeoutException`` and its subclasses) is a timeout; a
    network error or a server that hung up without a response is a connection error.
    An HTTP error response is never an httpx transport error, so a real 4xx is left to
    the handling below.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - google-genai depends on httpx
        return None
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeoutError(f"Gemini {type(exc).__name__}: {exc}")
    if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return LLMAPIConnectionError(f"Gemini {type(exc).__name__}: {exc}")
    return None


def _bedrock_transport_error(exc: BaseException) -> LLMError | None:
    """The typed error for a botocore transport failure, or None when it is not one.

    boto3 raises these outside ``ClientError`` (no HTTP response ever came back), so
    before, ``chat`` wrapped them into a bare ``LLMError``, which the ladder treats as an
    unrecoverable 400, and ``chat_with_tools`` let them escape untyped. A read or
    connect timeout is a timeout and a closed or refused connection is a connection
    error, both of which move to the next provider (operator ruling 2026-09-25).
    """
    try:
        from botocore.exceptions import (
            ConnectionError as BotoConnectionError,
            ConnectTimeoutError,
            HTTPClientError,
            ReadTimeoutError,
        )
    except ImportError:  # pragma: no cover - boto3 is a dependency of this client
        return None
    if isinstance(exc, (ReadTimeoutError, ConnectTimeoutError)):
        return LLMTimeoutError(f"Bedrock {type(exc).__name__}: {exc}")
    if isinstance(exc, (BotoConnectionError, HTTPClientError)):
        return LLMAPIConnectionError(f"Bedrock {type(exc).__name__}: {exc}")
    return None


class GeminiClient(BaseLLMClient):
    provider = "gcp"

    def __init__(self, api_key: str):
        """Create a Gemini client bound to the supplied API key.

        `google-genai` ships a retry policy (408/429/500/502/503/504, 5 attempts,
        exponential backoff with jitter) but leaves `retry_options` unset, and
        `retry_args(None)` then returns `stop_after_attempt(1)` — its own docstring
        calls that the "never retry" strategy. So every 503 from Gemini reached the
        caller on the first try. That is production turn 463 (2026-09-04): the graph
        query had already succeeded, the chatter drew
        `503 UNAVAILABLE ... experiencing high demand`, nothing retried, and the user
        saw "Internal pipeline error". boto3 retries the same class of failure five
        times without being asked; this makes the two providers behave alike.
        """
        import google.genai as genai
        from google.genai.types import HttpOptions, HttpRetryOptions

        self.client = genai.Client(
            api_key=api_key,
            http_options=HttpOptions(retry_options=HttpRetryOptions()),
        )

    def _convert_messages(self, messages: List[dict]) -> tuple[list[dict], str | None]:
        """Split system text from chat messages and reshape the remainder for Gemini."""
        contents: list[dict] = []
        system_parts: list[str] = []
        for msg in messages:
            role = (msg.get("role") or "").lower()
            content = msg.get("content") or ""
            if role == "system":
                system_parts.append(str(content))
                continue
            role_mapped = "model" if role == "assistant" else "user"
            contents.append({"role": role_mapped, "parts": [{"text": str(content)}]})
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return contents, system_instruction

    def chat(
        self,
        *,
        messages: List[dict],
        model: str,
        temperature: float = 0,
        response_format: dict | None = None,
        thinking_budget: int | None = None,
    ) -> LLMResponse:
        """Call Gemini `generate_content` and normalize the result into `LLMResponse`."""

        contents, system_instruction = self._convert_messages(messages)
        if not contents:
            # Previously fell through to an IndexError on contents[0], which the
            # generic handler below reclassified as an opaque LLMError.
            raise LLMError(
                f"Gemini request for model={model} has no user/assistant content to send "
                f"({len(messages)} message(s), all system)"
            )

        generation_config: dict[str, Any] = {"system_instruction": system_instruction, "temperature": temperature}
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            generation_config["response_mime_type"] = "application/json"
        if thinking_budget is not None:
            generation_config["thinking_config"] = {"thinking_budget": thinking_budget}

        try:
            resp = self.client.models.generate_content(
                model = model,
                contents = contents,
                config = generation_config
            )
        except Exception as e:
            # A transport failure first: no HTTP response came back, so nothing below
            # (a status code, a 4xx) applies, and a timeout's text can hold "504".
            transport = _gemini_transport_error(e)
            if transport is not None:
                raise transport from e
            msg = str(e)
            etype = type(e).__name__
            # A 429 that reaches here has survived the SDK's own retries (HttpRetryOptions
            # above), and used to fall through to a bare LLMError that ended the turn. It
            # is checked first, by its code: a quota message can quote a limit such as
            # 500, which the status-code scan below would read as a 5xx.
            if _is_gemini_rate_limit(e, msg):
                raise LLMRateLimitError(msg) from e
            # Typed exceptions from google-api-core / google-genai
            _GCP_TRANSIENT = ("ServiceUnavailable", "InternalServerError", "BadGateway", "GatewayTimeout", "DeadlineExceeded")
            if any(t in etype for t in _GCP_TRANSIENT):
                raise LLMServiceUnavailableError(msg) from e
            # Fallback: HTTP status codes in the error message
            if any(code in msg for code in ("500", "502", "503", "504")) or "UNAVAILABLE" in msg.upper():
                raise LLMServiceUnavailableError(msg) from e
            # 404 model-not-found / deprecated: treat as recoverable so the provider chain takes over
            if "404" in msg or "NOT_FOUND" in msg.upper() or "no longer available" in msg.lower():
                raise LLMServiceUnavailableError(msg) from e
            raise LLMError(msg) from e

        text = ""
        try:
            text = resp.text or ""
        except Exception:
            pass

        usage = None
        try:
            meta = resp.usage_metadata
            if meta:
                prompt = getattr(meta, "prompt_token_count", None)
                completion = getattr(meta, "candidates_token_count", None)
                total = getattr(meta, "total_token_count", None)
                usage = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total if total is not None else None,
                    # Priced by chat_nextseek.model_prices: thinking is billed as output
                    # and is NOT inside candidates_token_count; the cached part of the
                    # prompt is inside prompt_token_count and billed at the cache rate.
                    "thoughts_tokens": getattr(meta, "thoughts_token_count", None),
                    "cached_tokens": getattr(meta, "cached_content_token_count", None),
                }
        except Exception:
            pass

        # Mirror BedrockClient's stop_reason so truncation is visible to callers.
        # A completion that stops at a fixed token count every attempt is a cap,
        # not a model choice, and without this the two are indistinguishable.
        metadata = None
        try:
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                finish_reason = getattr(candidates[0], "finish_reason", None)
                metadata = {
                    "finish_reason": getattr(finish_reason, "name", None) or (
                        str(finish_reason) if finish_reason is not None else None
                    ),
                }
        except Exception:
            pass

        return LLMResponse(
            content=text or "",
            raw=resp,
            usage=usage,
            model=model,
            provider=self.provider,
            metadata=metadata,
        )


class AnthropicClient(BaseLLMClient):
    provider = "anthropic"

    def __init__(self, api_key: str, *, max_output_tokens: int = 4096):
        """Create an Anthropic SDK client and record output-token defaults."""
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.max_output_tokens = max_output_tokens
        self._supports_response_format: bool | None = None
        self._warned_response_format_unsupported = False

    def _response_format_supported(self) -> bool:
        """
        Detect whether the installed anthropic SDK supports response_format.
        Falls back to False if the signature check fails.
        """
        if self._supports_response_format is not None:
            return self._supports_response_format
        try:
            sig = inspect.signature(self.client.messages.create)
            self._supports_response_format = "response_format" in sig.parameters
        except Exception:
            self._supports_response_format = False
        return self._supports_response_format

    def _convert_messages(self, messages: List[dict]) -> tuple[str | None, list[dict]]:
        """Extract system text and convert remaining messages into Anthropic block format."""
        system_parts: list[str] = []
        converted: list[dict] = []
        for msg in messages:
            role = (msg.get("role") or "").lower()
            content = msg.get("content") or ""
            if role == "system":
                system_parts.append(str(content))
                continue
            if role not in ("user", "assistant"):
                continue
            converted.append({"role": role, "content": [{"type": "text", "text": str(content)}]})
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, converted

    def chat(
        self,
        *,
        messages: List[dict],
        model: str,
        temperature: float = 0,
        response_format: dict | None = None,
        thinking_budget: int | None = None,
    ) -> LLMResponse:
        """Call Anthropic Messages API with optional thinking mode and normalized error handling."""
        from anthropic import APIConnectionError, RateLimitError

        system_text, converted = self._convert_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": converted,
            "max_tokens": self.max_output_tokens,
        }
        if system_text:
            kwargs["system"] = system_text
        if thinking_budget is not None:
            # Extended thinking requires temperature=1 per Anthropic docs.
            # max_tokens must cover thinking tokens + text output tokens.
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            kwargs["temperature"] = 1
            kwargs["max_tokens"] = max(self.max_output_tokens, thinking_budget + 2048)
        use_json_format = (
            isinstance(response_format, dict)
            and response_format.get("type") == "json_object"
            and self._response_format_supported()
            and thinking_budget is None  # JSON mode not compatible with extended thinking
        )
        if use_json_format:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self.client.messages.create(**kwargs)
        except TypeError as e:
            # Older Anthropics SDKs do not recognize response_format; retry without it.
            if "response_format" in kwargs and "response_format" in str(e):
                self._supports_response_format = False
                kwargs.pop("response_format", None)
                if not self._warned_response_format_unsupported:
                    print("[LLM][Anthropic] response_format unsupported; retrying without JSON enforcement.")
                    self._warned_response_format_unsupported = True
                resp = self.client.messages.create(**kwargs)
            else:
                raise
        except RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except APIConnectionError as e:
            raise LLMAPIConnectionError(str(e)) from e
        except Exception as e:
            msg = str(e)
            status = getattr(e, "status_code", None)
            if status in (500, 502, 503, 504) or any(code in msg for code in ("500", "502", "503", "504")):
                raise LLMServiceUnavailableError(msg) from e
            raise LLMError(msg) from e

        text = ""
        try:
            parts = getattr(resp, "content", None) or []
            text_parts = []
            for p in parts:
                # Skip thinking blocks; only collect text blocks
                if getattr(p, "type", None) == "text":
                    text_parts.append(getattr(p, "text", ""))
            text = "".join(text_parts)
        except Exception:
            pass

        usage = None
        try:
            usage_raw = getattr(resp, "usage", None)
            if usage_raw:
                prompt = getattr(usage_raw, "input_tokens", None)
                completion = getattr(usage_raw, "output_tokens", None)
                total = None
                if prompt is not None and completion is not None:
                    total = prompt + completion
                usage = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                }
        except Exception:
            pass

        return LLMResponse(
            content=text or "",
            raw=resp,
            usage=usage,
            model=model,
            provider=self.provider,
        )


def _is_schema_rejection(message: str) -> bool:
    """True when a Bedrock ValidationException is about the schema, not the content.

    A model that will not take `toolConfig`, a forced `toolChoice`, or `strict` will
    happily take the same request without them, so the caller retries plain rather
    than failing over to another provider or killing the turn. Matched on the message
    because Bedrock returns one ValidationException code for all of them.
    """
    lowered = (message or "").lower()
    return any(
        token in lowered
        for token in ("toolchoice", "toolconfig", "tool_choice", "strict", "outputconfig", "output_config")
    )


# The Bedrock error codes that refuse the model itself rather than the request.
_MODEL_UNUSABLE_CODES = ("ResourceNotFoundException", "AccessDeniedException")

# What a ValidationException says when the model id is the problem: "The provided model
# identifier is invalid.", "Invocation of model ID ... with on-demand throughput isn't
# supported.", "This action doesn't support the model that you provided.", or a model id
# that is not supported, enabled or found.
_MODEL_ID_REJECTION = re.compile(
    r"model identifier"
    r"|on-demand throughput"
    r"|does(?:n't| not) support the model"
    r"|model id\b.{0,160}?\b(?:invalid|(?:is )?not (?:supported|enabled|found|available)"
    r"|isn't (?:supported|enabled|available)|does(?:n't| not) exist)",
    re.IGNORECASE | re.DOTALL,
)


def _is_model_id_rejection(message: str) -> bool:
    """True when a Bedrock ValidationException is about the model id, not the request.

    Bedrock uses one ValidationException code for a bad model id, a schema it will not
    take (``_is_schema_rejection``, checked first) and a malformed request, so the text
    tells them apart. "The model returned the following errors" is the model's own
    validation of the request: the model id worked, so it never counts.
    """
    text = (message or "").replace("’", "'")
    if "the model returned the following errors" in text.lower():
        return False
    return bool(_MODEL_ID_REJECTION.search(text))


def _bedrock_model_unusable(code: str, message: str) -> bool:
    """True when a Bedrock ClientError refused the model itself (``LLMModelUnusableError``)."""
    if code in _MODEL_UNUSABLE_CODES:
        return True
    return code == "ValidationException" and _is_model_id_rejection(message)


def _converse_usage(resp: dict, cache_ttl: str | None = None) -> dict | None:
    """Token counts from a Converse response, including the cache fields.

    `inputTokens` counts only tokens that were NEITHER read from nor written to the
    cache, so a caller that ignores the cache fields under-reports the real prompt by
    exactly the cached part. Total input is
    `inputTokens + cacheReadInputTokens + cacheWriteInputTokens`.

    A cache write is billed at the rate of its TTL. `cacheDetails`, when Bedrock sends
    it, splits the writes by TTL; `cache_ttl` is the TTL the request's cache points
    asked for, recorded so a write Bedrock does not split can still be priced.
    """
    try:
        u = resp.get("usage") or {}
        usage = {
            "prompt_tokens": u.get("inputTokens"),
            "completion_tokens": u.get("outputTokens"),
            "total_tokens": u.get("totalTokens"),
        }
        if u.get("cacheReadInputTokens") is not None:
            usage["cache_read_tokens"] = u.get("cacheReadInputTokens")
        if u.get("cacheWriteInputTokens") is not None:
            usage["cache_write_tokens"] = u.get("cacheWriteInputTokens")
        details = u.get("cacheDetails")
        if isinstance(details, list) and details:
            for ttl in ("5m", "1h"):
                usage[f"cache_write_{ttl}_tokens"] = sum(
                    int(d.get("inputTokens") or 0)
                    for d in details if isinstance(d, dict) and d.get("ttl") == ttl
                )
        if cache_ttl:
            usage["cache_ttl"] = cache_ttl
        return usage
    except Exception:
        return None


def _converse_metadata(resp: dict) -> dict | None:
    """Request id, http status, retry count, server latency and stop reason.

    `stop_reason` is the field that would have named production turn 406's cause; the
    enum now includes `malformed_model_output` and `model_context_window_exceeded`.

    `reasoning_blocks` counts the response's reasoning (thinking) blocks. Both `chat`
    and `chat_with_tools` drop those blocks from what they return, so this count is the
    only place a caller can see that the model reasoned before it answered.
    """
    try:
        rmeta = resp.get("ResponseMetadata") or {}
        blocks = ((resp.get("output") or {}).get("message") or {}).get("content") or []
        return {
            "request_id": rmeta.get("RequestId"),
            "http_status": rmeta.get("HTTPStatusCode"),
            "retry_attempts": rmeta.get("RetryAttempts"),
            "bedrock_latency_ms": (resp.get("metrics") or {}).get("latencyMs"),
            "stop_reason": resp.get("stopReason"),
            "reasoning_blocks": sum(
                1 for b in blocks if isinstance(b, dict) and "reasoningContent" in b
            ),
        }
    except Exception:
        return None


def _normalize_tool_choice(choice: str | dict) -> dict:
    """Accept "auto" | "any" | "<tool name>" | a raw Converse toolChoice dict.

    Converse spells the three options `{"auto": {}}`, `{"any": {}}` and
    `{"tool": {"name": ...}}`. Forcing a named tool is how a call gets schema-shaped
    output on a model with no structured-output support: the model answers by filling
    the tool's input schema, and a forced call cannot come back as an empty text
    block, which is the failure in production turn 406.
    """
    if isinstance(choice, dict):
        return choice
    if choice in ("auto", "any"):
        return {choice: {}}
    return {"tool": {"name": choice}}


def pydantic_to_tool_schema(model: Any) -> dict:
    """Turn a Pydantic model into a tool input schema Bedrock will accept.

    Pydantic emits `$defs`/`$ref`, which Converse allows (internal references only),
    and omits `additionalProperties`. Closing every object that declares properties
    makes the shape unambiguous for the model and is also the precondition for
    `strict: true` later; objects with no declared properties (a free-form `dict`
    field such as `ParserPlan.metadata`) are left open, because closing those would
    forbid the very content they exist to carry.
    """
    schema = model.model_json_schema()

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and node.get("properties"):
                node.setdefault("additionalProperties", False)
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)

    close(schema)
    return schema


class BedrockClient(BaseLLMClient):
    """
    AWS Bedrock Converse API client via boto3.
    Supports any Bedrock model: Claude, DeepSeek, Nova, Llama, Mistral, etc.

    boto3 reads AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY from env automatically.
    AWS_BEARER_TOKEN_BEDROCK is passed as aws_session_token (STS/SSO session token).
    Region: set AWS_REGION (defaults to us-east-1).
    """

    provider = "bedrock"

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        bearer_token: str | None = None,
        max_output_tokens: int = 4096,
        read_timeout: int = 600,
    ):
        """Create a Bedrock Converse client with region, token, and timeout configuration."""
        import boto3
        from botocore.config import Config

        kwargs: dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": region,
            # tcp_keepalive lets the kernel eventually notice a peer that has gone
            # away. It is only half the defence: Linux waits net.ipv4.tcp_keepalive_time
            # (7200s by default) before the first probe, so reset_connections() below is
            # what actually rescues a request that drew a dead socket.
            # retries: botocore's default is "legacy" mode, which already covers
            # 500/502/503/504 and the throttling family from _retry.json's
            # __default__ block (bedrock-runtime has no service override), for 5
            # attempts total. "standard" is the documented successor: the same
            # conditions plus a retry quota that stops a wide outage becoming a retry
            # storm. max_attempts counts RETRIES, so 4 keeps the same 5 total attempts
            # as the legacy default (botocore normalises it to total_max_attempts=5).
            # Naming it also stops the behaviour drifting if botocore moves its default.
            "config": Config(
                read_timeout=read_timeout,
                connect_timeout=10,
                tcp_keepalive=True,
                retries={"max_attempts": 4, "mode": "standard"},
            ),
        }
        if bearer_token:
            kwargs["aws_session_token"] = bearer_token
        self._client_kwargs = kwargs
        self.client = boto3.client(**kwargs)
        self.max_output_tokens = max_output_tokens

    def reset_connections(self) -> bool:
        """
        Drop every pooled TCP connection so the next call dials out fresh.

        ChatConfig is built once at Django import, so each gunicorn worker keeps one
        boto3 client — and one urllib3 connection pool — for the whole process
        lifetime. bedrock-runtime rotates its endpoint IPs, so a pooled socket
        eventually points at an address AWS no longer serves. Nothing sends a RST, so
        the socket stays ESTABLISHED and urllib3 hands it out again; the request then
        sits in the kernel send queue unacknowledged until the caller gives up.

        Measured on prod 2026-08-20: 43,832 bytes pinned in tx_q for a full 60s with
        rx_q flat at 0, while 5 of the 8 pooled sockets pointed at IPs absent from the
        endpoint's DNS rotation.

        Returns True if the pool was cleared. Never raises.
        """
        import boto3

        try:
            # botocore >= 1.28: clears the urllib3 pool manager. The client stays
            # usable and re-resolves DNS when it opens the next connection.
            self.client.close()
            return True
        except Exception:
            pass
        try:
            self.client = boto3.client(**self._client_kwargs)
            return True
        except Exception:
            return False

    def _convert_messages(self, messages: List[dict]) -> tuple[list[dict], list[dict]]:
        """Split system messages out; convert the rest to Bedrock Converse format."""
        system_parts: list[str] = []
        converted: list[dict] = []
        for msg in messages:
            role = (msg.get("role") or "").lower()
            content = msg.get("content") or ""
            if role == "system":
                system_parts.append(str(content))
                continue
            if role not in ("user", "assistant"):
                continue
            text = str(content)
            if not text.strip():
                # Bedrock rejects blank text blocks (ValidationException). An empty
                # turn can arise from the structured-output repair loop appending a
                # prior empty response; substitute a placeholder so the block is valid.
                text = "(no content)"
            converted.append({"role": role, "content": [{"text": text}]})
        system_blocks = [{"text": "\n\n".join(system_parts)}] if system_parts else []
        return system_blocks, converted

    def chat(
        self,
        *,
        messages: List[dict],
        model: str,
        temperature: float = 0,
        response_format: dict | None = None,
        thinking_budget: int | None = None,
    ) -> LLMResponse:
        """Call Bedrock Converse API and normalize throttling, transport, and usage metadata."""
        from botocore.exceptions import ClientError

        system_blocks, converted = self._convert_messages(messages)
        # Opus 4.7 / Mythos: adaptive thinking only; temperature/top_p/top_k forbidden.
        is_adaptive_only = "opus-4-7" in model or "mythos" in model
        inference_config: dict[str, Any] = {"maxTokens": self.max_output_tokens}
        if not is_adaptive_only:
            # Extended thinking on older models requires temperature=1.
            inference_config["temperature"] = 1 if thinking_budget is not None else temperature
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": converted,
            "inferenceConfig": inference_config,
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if thinking_budget is not None:
            if is_adaptive_only:
                _BUDGET_TO_EFFORT = {4000: "low", 8000: "medium", 16000: "high"}
                effort = _BUDGET_TO_EFFORT.get(thinking_budget, "high")
                kwargs["additionalModelRequestFields"] = {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": effort},
                }
                # Adaptive thinking tokens count against maxTokens. Without headroom
                # the model can spend the entire budget thinking and return empty text
                # (stop_reason=max_tokens) — which then breaks structured parsing. Leave
                # room for the actual output on top of the thinking budget.
                kwargs["inferenceConfig"]["maxTokens"] = max(
                    self.max_output_tokens, thinking_budget + 4096
                )
            else:
                kwargs["additionalModelRequestFields"] = {
                    "thinking": {"type": "enabled", "budget_tokens": thinking_budget}
                }
                # max_tokens must cover thinking tokens + text output tokens
                kwargs["inferenceConfig"]["maxTokens"] = max(self.max_output_tokens, thinking_budget + 2048)

        try:
            resp = self.client.converse(**kwargs)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ThrottlingException":
                raise LLMRateLimitError(str(e)) from e
            if code in ("EndpointResolutionError", "ConnectTimeoutError"):
                raise LLMAPIConnectionError(str(e)) from e
            if code in (
                "ServiceUnavailableException",
                "ModelNotReadyException",
                "ModelTimeoutException",
                "InternalServerException",
                "ModelErrorException",
            ):
                raise LLMServiceUnavailableError(str(e)) from e
            if _bedrock_model_unusable(code, str(e)):
                raise LLMModelUnusableError(str(e)) from e
            raise LLMError(str(e)) from e
        except Exception as e:
            transport = _bedrock_transport_error(e)
            if transport is not None:
                raise transport from e
            msg = str(e)
            if any(code in msg for code in ("500", "502", "503", "504")) or "service unavailable" in msg.lower():
                raise LLMServiceUnavailableError(msg) from e
            raise LLMError(msg) from e

        text = ""
        try:
            content_blocks = resp["output"]["message"]["content"]
            # Skip thinking blocks (have "reasoningContent" key); only collect text blocks
            text = "".join(b.get("text", "") for b in content_blocks if "text" in b)
        except Exception:
            pass

        usage = None
        try:
            usage = _converse_usage(resp)
        except Exception:
            pass

        metadata = _converse_metadata(resp)

        return LLMResponse(
            content=text or "",
            raw=resp,
            usage=usage,
            model=model,
            provider=self.provider,
            metadata=metadata,
        )

    @staticmethod
    def _anthropic_block_to_converse(block: dict) -> dict:
        """Translate an anthropic-native content block to Bedrock Converse shape.

        Pass-through if the block is already in Converse shape (has ``text``,
        ``toolUse``, ``toolResult``, or other Converse-native keys without the
        anthropic ``type`` discriminator).
        """
        if not isinstance(block, dict):
            return block
        # Already Bedrock-shaped — pass through.
        if "type" not in block:
            return block
        btype = block["type"]
        if btype == "text":
            return {"text": block.get("text", "")}
        if btype == "tool_use":
            return {
                "toolUse": {
                    "toolUseId": block["id"],
                    "name": block["name"],
                    "input": block.get("input", {}),
                }
            }
        if btype == "tool_result":
            result_content = block.get("content")
            if isinstance(result_content, str):
                wrapped = [{"text": result_content}]
            elif isinstance(result_content, list):
                # Tool result blocks can have nested content already in Converse shape.
                wrapped = result_content
            else:
                wrapped = [{"text": str(result_content)}]
            return {
                "toolResult": {
                    "toolUseId": block["tool_use_id"],
                    "content": wrapped,
                }
            }
        # Unknown anthropic-style block — pass through raw (caller's problem).
        return block

    def chat_with_tools(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        system: str,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict | None = None,
        strict_tools: bool = False,
        cache_prompt: bool = False,
        cache_ttl: str = "1h",
        thinking_budget: int | None = None,
    ) -> dict:
        """Invoke Bedrock Converse with tool-use enabled; return a normalized response.

        The caller drives the tool-use loop:
        - Inspect ``resp["stop_reason"]``. If ``"tool_use"``, iterate
          ``resp["content"]`` for ``{"type": "tool_use", ...}`` blocks, execute
          them, append the assistant message (raw) and tool_result blocks to
          ``messages``, and call again until ``stop_reason != "tool_use"``.

        Args:
            messages: A list of ``{"role": "user"|"assistant",
                                   "content": str | list[content blocks]}`` dicts.
                User/assistant text is wrapped to Converse's
                ``[{"text": "..."}]`` content-block shape.  Already-block-shaped
                content (e.g. a list of toolResult blocks) is passed through.
            tools: Anthropic-style tool dicts (``{"name", "description",
                "input_schema"}``).  Translated into Bedrock toolConfig shape
                internally.
            system: System prompt string.
            model: Bedrock model ID.
            max_tokens: Override for max output tokens (defaults to
                ``self.max_output_tokens``).
            temperature: Sampling temperature (default 0.0 for deterministic
                tool-use).

        Returns:
            ``{"stop_reason": str, "content": [content blocks]}`` where content
            blocks are anthropic-style: ``{"type": "text", "text": "..."}`` or
            ``{"type": "tool_use", "id": str, "name": str, "input": dict}``.
        """
        from botocore.exceptions import ClientError

        # Translate messages: wrap plain-string content into [{"text": ...}] blocks,
        # and translate anthropic-native content blocks to Bedrock Converse shape.
        converse_messages: list[dict] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                converse_messages.append({"role": msg["role"], "content": [{"text": content}]})
            elif isinstance(content, list):
                translated = [self._anthropic_block_to_converse(b) for b in content]
                converse_messages.append({"role": msg["role"], "content": translated})
            else:
                raise ValueError(f"Unsupported message content type: {type(content).__name__}")

        # Translate tools: anthropic-style -> Bedrock toolConfig shape.
        tool_specs: list[dict] = []
        for tool in tools:
            spec: dict[str, Any] = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": {"json": tool["input_schema"]},
            }
            if strict_tools:
                # Bedrock validates the schema against its JSON Schema Draft 2020-12
                # subset and compiles a grammar, so the model's tool input is
                # guaranteed to match. Only some models accept it (Claude Sonnet 4.5,
                # Haiku 4.5, Opus 4.5, Opus 4.6 at the time of writing; NOT Opus 4.7),
                # hence opt-in rather than always on.
                spec["strict"] = True
            tool_specs.append({"toolSpec": spec})

        # A cachePoint after the tool definitions and after the system prompt caches
        # the static head of the request. Checkpoints are processed tools -> system ->
        # messages and the minimum is cumulative across all three, so putting them at
        # the end of the two stable sections is what a growing tool conversation wants:
        # every later iteration of the loop re-sends this same head.
        system_blocks: list[dict] = [{"text": system}] if system else []
        if cache_prompt:
            cache_block = {"cachePoint": {"type": "default", "ttl": cache_ttl}}
            if tool_specs:
                tool_specs.append(dict(cache_block))
            if system_blocks:
                system_blocks.append(dict(cache_block))

        # Opus 4.7 / Mythos forbid temperature/top_p/top_k.
        is_adaptive_only = "opus-4-7" in model or "mythos" in model
        inference_config: dict[str, Any] = {"maxTokens": max_tokens or self.max_output_tokens}
        if not is_adaptive_only:
            inference_config["temperature"] = temperature
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": converse_messages,
            "system": system_blocks,
            "inferenceConfig": inference_config,
        }
        if thinking_budget is not None:
            # Same translation `chat` does. Adaptive thinking auto-enables interleaved
            # thinking, so a tool loop keeps reasoning between calls; the headroom on
            # maxTokens is what stops the model spending the whole budget thinking and
            # returning nothing.
            if is_adaptive_only:
                _BUDGET_TO_EFFORT = {4000: "low", 8000: "medium", 16000: "high"}
                kwargs["additionalModelRequestFields"] = {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": _BUDGET_TO_EFFORT.get(thinking_budget, "high")},
                }
                inference_config["maxTokens"] = max(
                    max_tokens or self.max_output_tokens, thinking_budget + 4096
                )
            else:
                kwargs["additionalModelRequestFields"] = {
                    "thinking": {"type": "enabled", "budget_tokens": thinking_budget}
                }
                inference_config["maxTokens"] = max(
                    max_tokens or self.max_output_tokens, thinking_budget + 2048
                )
        if tool_specs:
            tool_config: dict[str, Any] = {"tools": tool_specs}
            if tool_choice is not None:
                tool_config["toolChoice"] = _normalize_tool_choice(tool_choice)
            kwargs["toolConfig"] = tool_config

        try:
            resp = self.client.converse(**kwargs)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ThrottlingException":
                raise LLMRateLimitError(str(e)) from e
            if code in ("EndpointResolutionError", "ConnectTimeoutError"):
                raise LLMAPIConnectionError(str(e)) from e
            if code in (
                "ServiceUnavailableException",
                "ModelNotReadyException",
                "ModelTimeoutException",
                "InternalServerException",
                "ModelErrorException",
            ):
                raise LLMServiceUnavailableError(str(e)) from e
            if code == "ValidationException" and _is_schema_rejection(str(e)):
                # The model will not take this request WITH a schema but would take it
                # without one. Distinct from a 503 on purpose: failing over to another
                # provider would be the wrong move, and so would killing the turn.
                raise LLMStructuredUnsupportedError(str(e)) from e
            if _bedrock_model_unusable(code, str(e)):
                # The model id is refused (unknown, retired, or not enabled for this
                # account): another model may answer, so the ladder moves on it.
                raise LLMModelUnusableError(str(e)) from e
            raise   # unknown ClientError propagates
        except Exception as e:
            transport = _bedrock_transport_error(e)
            if transport is not None:
                raise transport from e
            raise

        # Normalize the Converse response to anthropic-style content blocks.
        stop_reason = resp.get("stopReason", "end_turn")
        raw_content = resp.get("output", {}).get("message", {}).get("content", [])
        normalized: list[dict] = []
        for block in raw_content:
            if "text" in block:
                normalized.append({"type": "text", "text": block["text"]})
            elif "toolUse" in block:
                tu = block["toolUse"]
                normalized.append({
                    "type": "tool_use",
                    "id": tu["toolUseId"],
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                })
            # Other block types (image, document, etc.) ignored for now.

        # usage and metadata used to be dropped here, so the pipeline agent — the one
        # place that already ran a tool loop — spent tokens no ledger ever saw, and a
        # cache hit was unmeasurable. Additive keys: callers reading stop_reason and
        # content are unaffected.
        return {
            "stop_reason": stop_reason,
            "content": normalized,
            "usage": _converse_usage(resp, cache_ttl=cache_ttl if cache_prompt else None),
            "metadata": _converse_metadata(resp),
        }

    def chat_structured(
        self,
        *,
        messages: list[dict],
        system: str | None,
        model: str,
        schema: dict,
        schema_name: str = "emit_result",
        schema_description: str = "",
        max_tokens: int | None = None,
        temperature: float = 0.0,
        thinking_budget: int | None = None,
        strict: bool = False,
        cache_prompt: bool = False,
        cache_ttl: str = "1h",
    ) -> "LLMResponse":
        """Get schema-shaped JSON by forcing one tool call, and return it as text.

        Every Bedrock structured call in this codebase used to be unconstrained text:
        `BedrockClient.chat` accepts `response_format` and never sends it, so the JSON
        was prompt discipline plus a Pydantic repair loop. Forcing a named tool makes
        the model answer by filling the schema instead, and a forced tool call cannot
        come back as an empty text block — which is precisely how production turn 406
        failed three times in a row.

        `.content` is the tool input serialised, so callers parse it exactly as they
        parsed the free-text reply and nothing downstream changes. `strict=True` adds
        Bedrock's grammar-level guarantee but only some models accept it (Claude Sonnet
        4.5/4.6, Haiku 4.5, Opus 4.5/4.6; NOT Opus 4.7), so it stays opt-in.
        """
        import json as _json

        result = self.chat_with_tools(
            messages=messages,
            tools=[{
                "name": schema_name,
                "description": schema_description or f"Return the result as {schema_name}.",
                "input_schema": schema,
            }],
            system=system or "",
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_choice=schema_name,
            strict_tools=strict,
            cache_prompt=cache_prompt,
            cache_ttl=cache_ttl,
            thinking_budget=thinking_budget,
        )

        payload = ""
        for block in result.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == schema_name:
                payload = _json.dumps(block.get("input", {}))
                break
        if not payload:
            # The model answered in prose despite the forced tool. Hand the text back
            # so the ordinary parse path gets its chance rather than failing here.
            payload = "".join(
                b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"
            )

        metadata = dict(result.get("metadata") or {})
        metadata["structured_via"] = "tool_use"
        return LLMResponse(
            content=payload,
            raw=result,
            usage=result.get("usage"),
            model=model,
            provider=self.provider,
            metadata=metadata,
        )


def build_llm_client(
    mode: str,
    *,
    gcp_api_key: str | None = None,
    anthropic_api_key: str | None = None,
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    aws_bearer_token: str | None = None,
    aws_region: str | None = None,
) -> BaseLLMClient:
    """
    Factory to construct the appropriate LLM client based on mode (oai|gcp|anth|bedrock).
    Validates required API keys per provider and defaults to OpenAI-compatible when unspecified.
    """
    mode = (mode or "").lower()
    if mode in ("gcp", "mixed") or mode.startswith("gcp:"):
        if not gcp_api_key:
            raise RuntimeError("GCP mode selected but GCP_API_KEY is not configured.")
        return GeminiClient(api_key=gcp_api_key)
    if mode == "anth" or mode.startswith("anth:") or mode.startswith("aws:"):
        return BedrockClient(
            region=aws_region or "us-east-1",
            bearer_token=aws_bearer_token,
        )
    # Default to OpenAI-compatible
    return OpenAIClient(api_key=openai_api_key, base_url=openai_base_url)
