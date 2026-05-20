"""Backward-compatible forwarder. Real implementation in chat_nextseek.pipeline.builder_tools.

This forwarder is ephemeral — scheduled for removal in Phase 5 once internal
consumers (pipeline.agent, wizard, agents, tests) import from
chat_nextseek.pipeline.builder_tools directly.

The ``sys.modules`` alias below ensures that ``unittest.mock.patch(
"chat_nextseek.builder_tools.X")`` continues to affect call sites that
reference ``chat_nextseek.pipeline.builder_tools.X`` and vice versa. Without
it, existing tests (e.g. tests/test_builder_tools.py and
tests/test_wizard_agent_builder.py) that patch names at the old path would break.
"""
import sys as _sys

from .pipeline import builder_tools as _builder_tools

# Explicit re-exports — dead at runtime after the sys.modules swap below,
# but kept for IDEs and static analyzers that read source textually.
from .pipeline.builder_tools import (  # noqa: F401
    BUILDER_TOOL_SCHEMAS,
    dispatch_tool_call,
)

# Alias this module to the real one so the two import paths share state.
_sys.modules[__name__] = _builder_tools
