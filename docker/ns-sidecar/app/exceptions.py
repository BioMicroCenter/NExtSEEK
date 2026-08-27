"""Sidecar exception classes shared between ops.py and ns_client.py.

Kept in a separate module to avoid circular imports: ns_client imports these
and ops imports ns_client, so they cannot both live only in ops.py.
"""
from __future__ import annotations


class OpValidationError(ValueError):
    """→ VALIDATION / exit 3."""


class WriteBlockedError(RuntimeError):
    """→ WRITE_BLOCKED / exit 5."""


class AuthFailedError(RuntimeError):
    """→ AUTH_FAILED / exit 8. Raised on HTTP 401 or non-participant 403."""


class TransportError(RuntimeError):
    """→ TRANSPORT_ERROR / exit 7. Raised on httpx.HTTPError (connect/read timeout)."""


class AgentFailedError(RuntimeError):
    """→ AGENT_FAILED / exit 4. Raised on CONFIG_ERROR, CONFIG_MISSING, and unexpected errors."""
