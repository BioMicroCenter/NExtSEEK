from __future__ import annotations

"""
Lightweight provider adapters so agents can call OpenAI, GCP Gemini, or Anthropic
without changing prompts or core logic.
"""

import dataclasses
import inspect
from typing import Any, List

__all__ = [
    "LLMError",
    "LLMRateLimitError",
    "LLMAPIConnectionError",
    "LLMTimeoutError",
    "LLMServiceUnavailableError",
    "LLMFatalError",
    "LLMResponse",
    "BaseLLMClient",
    "OpenAIClient",
    "GeminiClient",
    "AnthropicClient",
    "BedrockClient",
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
    """
    def __init__(self, message: str, *, agent: str | None = None):
        super().__init__(message)
        self.agent = agent


class LLMServiceUnavailableError(LLMError):
    """Raised on 5xx / service temporarily unavailable or overloaded — triggers provider fallback.
    Covers: 500 Internal Server Error, 502 Bad Gateway, 503 Unavailable, 504 Gateway Timeout,
    and provider-specific equivalents (ModelNotReadyException, InternalServerException, etc.)
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


class GeminiClient(BaseLLMClient):
    provider = "gcp"

    def __init__(self, api_key: str):
        """Create a Gemini client bound to the supplied API key."""
        import google.genai as genai

        self.client = genai.Client(api_key=api_key)

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
            msg = str(e)
            etype = type(e).__name__
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
            "config": Config(read_timeout=read_timeout, connect_timeout=10),
        }
        if bearer_token:
            kwargs["aws_session_token"] = bearer_token
        self.client = boto3.client(**kwargs)
        self.max_output_tokens = max_output_tokens

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
            raise LLMError(str(e)) from e
        except Exception as e:
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
            u = resp.get("usage", {})
            usage = {
                "prompt_tokens": u.get("inputTokens"),
                "completion_tokens": u.get("outputTokens"),
                "total_tokens": u.get("totalTokens"),
            }
        except Exception:
            pass

        metadata = None
        try:
            rmeta = resp.get("ResponseMetadata") or {}
            metadata = {
                "request_id": rmeta.get("RequestId"),
                "http_status": rmeta.get("HTTPStatusCode"),
                "retry_attempts": rmeta.get("RetryAttempts"),
                "bedrock_latency_ms": (resp.get("metrics") or {}).get("latencyMs"),
                "stop_reason": resp.get("stopReason"),
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
        tool_specs = [
            {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": {"json": tool["input_schema"]},
                }
            }
            for tool in tools
        ]

        # Opus 4.7 / Mythos forbid temperature/top_p/top_k.
        is_adaptive_only = "opus-4-7" in model or "mythos" in model
        inference_config: dict[str, Any] = {"maxTokens": max_tokens or self.max_output_tokens}
        if not is_adaptive_only:
            inference_config["temperature"] = temperature
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": converse_messages,
            "system": [{"text": system}] if system else [],
            "inferenceConfig": inference_config,
        }
        if tool_specs:
            kwargs["toolConfig"] = {"tools": tool_specs}

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
            raise   # unknown ClientError propagates

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

        return {"stop_reason": stop_reason, "content": normalized}


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
