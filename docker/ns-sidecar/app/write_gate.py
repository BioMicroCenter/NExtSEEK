"""Simplified write gate (T16, A-5). No local allowlist — NExtSEEK enforces api-read.
The sidecar's only local check: api-write requires confirmed_write is True (strict bool).
All other ops are allowed through without inspection."""
from __future__ import annotations

from typing import Callable

from sidecar.app.exceptions import WriteBlockedError


def build_gate() -> Callable[[str, str | None, str | None, object], None]:
    """Return a gate that only enforces api-write's confirmed_write pre-check.

    The api-read allowlist gate has been retired to NExtSEEK (DD-A5-4); the sidecar
    no longer builds the api-read request body so it cannot know the endpoint pre-call.
    """
    def gate(op: str, endpoint: str | None, method: str | None, confirmed_write: object) -> None:
        if op == "api-write":
            if confirmed_write is not True:  # strict: only boolean True confirms (§8)
                raise WriteBlockedError("api-write requires confirmed_write=true (server-side L2)")
            return
        # All other ops: allow (NExtSEEK enforces its own gates server-side).
        return None

    return gate
