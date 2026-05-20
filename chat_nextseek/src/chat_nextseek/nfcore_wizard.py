"""Backward-compatible forwarder. Real implementation in chat_nextseek.pipeline.wizard.

This forwarder is ephemeral — scheduled for removal in Phase 5 once internal
consumers (orchestrator, agents, tests) import from
chat_nextseek.pipeline.wizard directly. Note that pipeline.wizard itself is
deprecated (predecessor of pipeline.agent); when its tests migrate to
pipeline.agent the underlying module and this forwarder go away together.

The ``sys.modules`` alias below ensures that ``unittest.mock.patch(
"chat_nextseek.nfcore_wizard.X")`` continues to affect call sites that
reference ``chat_nextseek.pipeline.wizard.X`` and vice versa. Without
it, existing tests (e.g. tests/test_nfcore_wizard_*.py and
tests/test_orchestrator_wizard_emit.py) that patch names at the old path would break.
"""
import sys as _sys

from .pipeline import wizard as _wizard

# Explicit re-exports — dead at runtime after the sys.modules swap below,
# but kept for IDEs and static analyzers that read source textually.
from .pipeline.wizard import (  # noqa: F401
    CANCEL_TOKENS,
    MAX_OFF_TOPIC_STAYS,
    STEP_BUILDER,
    STEP_CONFIRM,
    STEP_DONE,
    STEP_PIPELINE,
    STEPS_ORDERED,
    WIZARD_KEY,
    build_execution_params,
    clear,
    format_available_pipelines,
    handle_turn,
    is_active,
    snapshot_for_chat_log,
    start,
)

# Alias this module to the real one so the two import paths share state.
_sys.modules[__name__] = _wizard
