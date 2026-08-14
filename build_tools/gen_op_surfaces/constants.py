"""Shared constants for deterministic surface generation."""
from __future__ import annotations

EXIT_NO_CHANGE = 0
EXIT_ERROR = 1
EXIT_CHANGES_WRITTEN = 2

CANONICAL_CAPABILITIES_REL = (
    "chat_nextseek/src/chat_nextseek/context/capabilities.md"
)
BAKED_CAPABILITIES_REL = (
    "docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md"
)

COMMAND_OPS_BEGIN = "<!-- BEGIN PLAN005-GEN:command-ops -->"
COMMAND_OPS_END = "<!-- END PLAN005-GEN:command-ops -->"

MARKER_PREFIX = "<!-- BEGIN PLAN005-GEN:"
MARKER_SUFFIX = "-->"
