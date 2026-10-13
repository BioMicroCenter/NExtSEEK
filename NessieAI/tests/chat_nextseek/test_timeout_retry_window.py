"""A timeout recycles the client's connections before the next attempt, and the next attempt's window.

The diagnosis recorded in `_call_with_recovery` is that a timeout is usually a dead pooled socket rather than a slow
model: the request is never acknowledged at all. 2026-09-21 gave a clean instance of it: memory_coder waited the full
300 s with no response, then the retry answered in 9.2 s on a fresh connection. The model was never slow; only the
detection was.

Since 2026-09-28 both windows come from one per-agent table (`call_budgets.CALL_BUDGETS`, pinned in
test_call_budgets.py): the first try, sized to a healthy call's tail so a stall is detected in seconds, and the moved
call, which goes to the next provider in the chain (test_provider_fallback_trigger.py) or, with no chain, to the same
provider on a fresh socket. These tests pin what stays true across that change: a caller's own window is never
overwritten, the default row is the old 300 s and 180 s, and the socket is recycled before the window changes.
"""
from __future__ import annotations

import inspect

import pytest

from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.call_budgets import DEFAULT_BUDGET, budget_for


def test_an_agent_the_table_does_not_name_keeps_300_then_180():
    assert (schema_helper.LLM_CALL_TIMEOUT_SECONDS, schema_helper.TIMEOUT_RETRY_SECONDS) == (300, 180)
    assert budget_for("report_coder") == DEFAULT_BUDGET


@pytest.mark.parametrize("fn", [schema_helper.call_llm_structured, schema_helper.call_llm_text])
def test_both_entry_points_take_their_windows_from_the_table_unless_given(fn):
    """None means "the agent's row", never "inherit the first try": that is how the retry once got 300 s."""
    params = inspect.signature(fn).parameters
    assert params["timeout_seconds"].default is None
    assert params["timeout_retry_seconds"].default is None


def test_the_parsers_window_is_the_tables_not_a_literal():
    """parser.py used to pass 35 and 60 itself; the table holds them now (50 and 60 since run 2), in one place."""
    from pathlib import Path

    src = Path(schema_helper.__file__).resolve().parents[1] / "agents" / "parser.py"
    text = src.read_text(encoding="utf-8")
    assert "timeout_seconds=" not in text
    assert "timeout_retry_seconds=" not in text
    assert (budget_for("parser").first_try_s, budget_for("parser").moved_s) == (50, 60)


def test_recycling_the_connection_is_what_the_retry_depends_on():
    """The retry is only worth sending because it runs on a fresh socket.

    If `_recycle_client_connections` ever stopped being called before the window changes, the next attempt would
    just fail again on the same dead connection.
    """
    src = inspect.getsource(schema_helper._call_with_recovery)
    timeout_branch = src[src.index("except LLMTimeoutError"):]
    recycle_at = timeout_branch.index("_recycle_client_connections")
    narrow_at = timeout_branch.index("timeout_retry_seconds")
    assert recycle_at < narrow_at, "the socket is recycled before the window changes"
