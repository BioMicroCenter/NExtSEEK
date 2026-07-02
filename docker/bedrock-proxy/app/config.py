"""Configuration for the OI-3 hardened Bedrock auth-proxy (T1).

The proxy holds the institutional ``AWS_BEARER_TOKEN_BEDROCK`` in ITS OWN env
and re-attaches it as ``Authorization: Bearer …`` on the way to Bedrock. The
upstream host is fixed at config-load time from ``AWS_REGION`` (no caller-
controlled upstream → no SSRF). Everything that a test needs to control —
token, region, allowed models, body cap, timeouts — lives on a frozen
``ProxyConfig`` so tests can build one explicitly and inject it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

# Allowed model ids. Per the vetted plan: exactly this one.
_DEFAULT_ALLOWED_MODELS: tuple[str, ...] = ("us.anthropic.claude-opus-4-8",)

# Default request-body cap: 10 MiB. Bedrock Anthropic payloads are well under
# this; anything larger is rejected with 413 before being read into memory.
_DEFAULT_MAX_BODY_BYTES: int = 10 * 1024 * 1024

# Granular timeouts (seconds) — NOT a single 600s blanket. Connect must be
# tight (fast-fail on a dead upstream → 504); read is generous to allow long
# streamed model turns; write covers uploading the request body.
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 600.0
_WRITE_TIMEOUT = 60.0
_POOL_TIMEOUT = 10.0


@dataclass(frozen=True)
class ProxyConfig:
    """Immutable proxy configuration.

    ``token`` is the only secret here. It is never logged (see proxy.py's
    redacting logger) and is attached only to the outbound Authorization
    header. An empty token is a misconfiguration: the relay returns 500.
    """

    region: str
    token: str
    allowed_models: tuple[str, ...] = _DEFAULT_ALLOWED_MODELS
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES
    connect_timeout: float = _CONNECT_TIMEOUT
    read_timeout: float = _READ_TIMEOUT
    write_timeout: float = _WRITE_TIMEOUT
    pool_timeout: float = _POOL_TIMEOUT

    @property
    def upstream_host(self) -> str:
        """The compile-time-fixed Bedrock host derived solely from the region."""
        return f"bedrock-runtime.{self.region}.amazonaws.com"

    @property
    def upstream_base_url(self) -> str:
        return f"https://{self.upstream_host}"

    @property
    def timeout(self) -> httpx.Timeout:
        """Split connect/read/write/pool timeouts (no single blanket value)."""
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )

    def __repr__(self) -> str:
        # NEVER render the token, even in a repr that might land in a log.
        return (
            f"ProxyConfig(region={self.region!r}, token=<redacted>, "
            f"allowed_models={self.allowed_models!r}, "
            f"max_body_bytes={self.max_body_bytes})"
        )

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        region = os.environ.get("AWS_REGION", "us-east-1")
        token = (os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip()
        return cls(region=region, token=token)
