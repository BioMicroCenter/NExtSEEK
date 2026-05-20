"""Backward-compatible forwarder. Real implementation in chat_nextseek.pipeline.tools.

This forwarder is ephemeral — scheduled for removal in Phase 5 once internal
consumers (pipeline.agent, orchestrator, tests) import from
chat_nextseek.pipeline.tools directly.

The ``sys.modules`` alias below ensures that ``unittest.mock.patch(
"chat_nextseek.pipeline_tools.X")`` continues to affect call sites that
reference ``chat_nextseek.pipeline.tools.X`` and vice versa. Without it,
existing tests that patch private names at the old path would break.
"""
import sys as _sys

from .pipeline import tools as _tools

# Explicit re-exports — dead at runtime after the sys.modules swap below,
# but kept for IDEs and static analyzers that read source textually.
from .pipeline.tools import (  # noqa: F401
    GROUPBY_TOOL_SCHEMAS,
    list_metadata_fields,
    field_distribution_by_sample_type,
    list_distinct_values,
    dispatch_groupby_tool_call,
)

# Alias this module to the real one so the two import paths share state.
_sys.modules[__name__] = _tools
