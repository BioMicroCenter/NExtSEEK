"""Backward-compatible forwarder. Real implementation in chat_nextseek.pipeline.agent.

This forwarder is ephemeral — scheduled for removal in Phase 5 once internal
consumers (orchestrator, tests) import from chat_nextseek.pipeline.agent directly.

We replace this module in sys.modules with the real pipeline.agent module so
that ``chat_nextseek.pipeline_agent`` and ``chat_nextseek.pipeline.agent`` refer
to the SAME module object. This preserves ``unittest.mock.patch`` semantics for
existing tests that target ``chat_nextseek.pipeline_agent.<name>`` (including
private names like ``_pipeline_directive_parse``) — patches via either dotted
path mutate the same underlying module.
"""
import sys as _sys

from .pipeline import agent as _agent

# Explicit re-exports (kept for IDE / static checkers; the sys.modules alias
# below is what actually makes attribute lookup work uniformly).
from .pipeline.agent import (  # noqa: F401
    handle_turn,
    start,
    is_active,
    clear,
    snapshot_for_chat_log,
    PIPELINE_AGENT_KEY,
)

# Alias this module to the real one so the two import paths share state.
_sys.modules[__name__] = _agent
