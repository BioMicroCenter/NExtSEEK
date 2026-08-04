"""Native write gate for the assistant granular ops.

Port of the dmac sidecar's ``sidecar/app/write_gate.py``. It is the
safety-critical control for the ``api-write`` op and MUST preserve the sidecar
semantics exactly:

* ``api-write`` is **confirmation-only**: it executes only when
  ``confirmed_write`` is the boolean ``True`` (strict identity — the string
  ``"true"`` or the integer ``1`` do NOT confirm). The endpoint/method are not
  re-checked against the allowlist; confirmation is the gate, mirroring the
  container write-safety policy ("any POST/PUT/PATCH/DELETE is a write; confirm
  before executing").
* ``api-read`` is **allowlist-gated**: the ``(endpoint, METHOD)`` pair must be
  present in the canonical read-safe allowlist (``read_safe_endpoints.json``).
* every other (read-class) op — entity / parse / graph / report /
  generate-submission — always passes.
* an unknown op label is a programming error and is default-denied.

A blocked call raises :class:`WriteBlockedError`; the viewset maps that to the
canonical ``WRITE_BLOCKED`` error code (HTTP 403).
"""
from __future__ import annotations

import json
import os
from typing import Callable

# The granular sidecar ops (mirrors dmac _ws_contract.SIDECAR_OPS).
# ``run-ls`` and ``build-upload-xlsx`` are read/render-only and their handlers do
# not currently call the gate — but ``build_gate`` DEFAULT-DENIES an unrecognised
# op label, so omitting them here turns the first call that does consult the gate
# into an unexplained WRITE_BLOCKED. Listing them makes the read classification
# explicit rather than incidental.
SIDECAR_OPS = frozenset(
    {"entity", "parse", "api-read", "api-write", "graph", "report", "generate-submission",
     "run-ls", "build-upload-xlsx"}
)

# Read-class ops: every sidecar op that is neither api-read nor api-write.
READ_CLASS_OPS = frozenset(SIDECAR_OPS) - {"api-read", "api-write"}


class WriteBlockedError(RuntimeError):
    """A write was attempted without confirmation, or a read against a
    non-allowlisted endpoint. Maps to the canonical WRITE_BLOCKED error code."""


class AllowlistMissingError(RuntimeError):
    """The read-safe allowlist file is missing or malformed. Maps to CONFIG_ERROR."""


def default_allowlist_path() -> str:
    """Path to the bundled canonical read-safe allowlist shipped with this app."""
    return os.path.join(os.path.dirname(__file__), "read_safe_endpoints.json")


def load_allowlist_from_entries(entries: list) -> set[tuple[str, str]]:
    """Build the ``(endpoint, METHOD)`` allowlist set from a list of
    ``{"endpoint": ..., "methods": [...]}`` dicts (uppercasing methods)."""
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise AllowlistMissingError("read_safe_endpoints entries malformed")
    allow: set[tuple[str, str]] = set()
    for entry in entries:
        ep = entry.get("endpoint")
        for m in entry.get("methods", []):
            allow.add((ep, m.upper()))
    return allow


def load_allowlist(path: str | None = None) -> set[tuple[str, str]]:
    """Load and parse the read-safe allowlist JSON from ``path`` (defaulting to
    the bundled file)."""
    path = path or default_allowlist_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AllowlistMissingError(
            f"read_safe_endpoints.json unusable at {path}: {type(exc).__name__}"
        ) from exc
    return load_allowlist_from_entries(data)


def build_gate(
    allowlist: set[tuple[str, str]],
) -> Callable[[str, str | None, str | None, object], None]:
    """Return ``gate(op, endpoint, method, confirmed_write) -> None`` that raises
    :class:`WriteBlockedError` when a call is not permitted."""

    def gate(op: str, endpoint: str | None, method: str | None, confirmed_write: object) -> None:
        if op == "api-write":
            if confirmed_write is not True:  # strict: only boolean True confirms
                raise WriteBlockedError(
                    "api-write requires confirmed_write=true (server-side gate)"
                )
            return None
        if op == "api-read":
            if (endpoint, (method or "").upper()) not in allowlist:
                raise WriteBlockedError(
                    f"endpoint {endpoint!r} method {method!r} not in read_safe_endpoints.json"
                )
            return None
        if op in READ_CLASS_OPS:
            return None
        # Default-deny: any op label that is not a known sidecar op is a bug.
        raise WriteBlockedError(f"unknown op for write gate: {op!r}")

    return gate
