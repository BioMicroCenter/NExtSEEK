"""How long each NS agent's model call may take: one table, per agent (operator ruling 2026-09-28, F3 and F5.1).

Two wall clocks per agent, looked up by the catalog key the provider chain uses (``graph``, not ``graph_agent``):

* ``first_try_s``: the primary model's call. For the Gemini agents it is sized to the measured tail of a healthy
  call, so a stalled model is given up on in seconds rather than the 300 s every agent used to wait.
* ``moved_s``: the one call on the fallback model after the primary failed, whatever the failure was (a timeout, a
  5xx, a 429, an empty body, a dropped connection or a refused model). Before, it got the retry window after a
  timeout and the first try's budget after anything else. With no fallback to move to (a profile with no chain), it
  is also the same-provider retry after a timeout.

``timeout_marks_model`` False means a timeout in this call does not mark the model failed for the rest of the turn
(``call_scope``): the parser's first try is a speed preference, not a stall test, and a thinking Opus may miss it without
being down. A 5xx, a 429, a refusal or a connection error still marks.

``op_move_reserve`` False means that inside a Container-CC op the first try is not cut to leave the move its
reserve (``call_scope.MOVE_RESERVE_S``): it gets its own budget or what is left of the op, whichever is less, as
before 2026-09-28. For the report writer, whose moved call could not redo
the work in 20 s (a 4k-token graph answer takes 30 to 50 s, a report longer): cutting them only broke healthy calls
late in an op (operator ruling on review finding 1, option A, 2026-09-28). A turn of its own has no deadline, so this
changes nothing there; a nested NS turn started by a Container-CC turn has one (``orchestrator._limit_turn``) but is
not an op. Round 6 (operator, 2026-10-07): the graph agent and its repair keep the 20 s reserve inside an op only
(``move_reserve_only_in_op``); ops of 90 s still give their first try 50 s or more. Under a deadline that is not an
op (a nested NS turn) the graph agent's first try gets what is left, as before round 6.

An explicit ``timeout_seconds`` or ``timeout_retry_seconds`` from a caller still wins over this table. An agent the
table does not name gets ``DEFAULT_BUDGET`` in the recovery ladder (report_coder and the plan-mode agents) and
``TOOL_LOOP_DEFAULT_BUDGET`` in a tool loop.

Evidence (laptop ledger, 3,018 successful calls 2026-09-11 to 09-22; the entity batch of 2026-09-16; the dev box's
step timings of 2026-09-14). Every Gemini call over 20 s that returned an answer of normal length was a stall: normal
output at 4x or more its neighbours' time, and a retry answers in about 5 s. The graph agent's long answers are real
work, at up to 11.7 s per 1,000 output tokens (4,351 tokens took 51.0 s). The largest genuine success per agent, which
each first try must clear, is pinned in ``NessieAI/tests/chat_nextseek/test_call_budgets.py``. The fallback of every
Gemini agent is Sonnet 5.5, which no local ledger has measured; Opus 4.7 on the same Bedrock path writes 1,000 tokens
in 7.6 to 9.5 s, so the longest graph answer needs about 41 s there, and 90 s is about twice that.

The model switch (Gemini 3.8 Flash, Opus 5.5 with thinking) must re-check the rows it changes: this is the one place
to edit. Run 2 (2026-09-28) raises one value: the parsers' first try, 35 -> 50 s (operator, on the switch report's
public-figure estimate that a thinking Opus 5.5 at effort medium would miss 35 s on roughly 1 in 10 to 1 in 4 calls).
2026-09-30 raises one more: the entity's first try, 20 -> 30 s (operator: "medium with 30"). The entity step now runs
Gemini 3.8 Flash at medium thinking. An entity-only test of that day (84 questions) ran medium at 30 s with 0
timeouts (p50 4.9 s, p95 12.5 s, max 27.0 s, one call over 20 s), where low at 20 s changed the extraction on 26 of
84 questions, and on the dev box 7 of 95 entity calls hit the 20 s limit on 29 Sep. The moved call keeps 90 s.
Every other value is the one the operator approved, sized on Gemini 3.5 Flash and on Opus 4.7 without thinking, so
run 2's own timings (ledger ``elapsed_ms``, moves with reason ``timeout``) are what re-checks them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallBudget:
    """The wall clocks, in seconds, of one agent's model call."""

    first_try_s: float
    moved_s: float
    timeout_marks_model: bool = True
    op_move_reserve: bool = True
    #: True: the move reserve is taken only inside a Container-CC op (``call_scope.CallScope.is_op``); under a deadline
    #: that is not an op (a nested NS turn) the first try gets what is left. Round 6, the graph agent only.
    move_reserve_only_in_op: bool = False
    #: Inside a Container-CC op only (``call_scope.CallScope.is_op``): the first try's ceiling. Round 6 (operator,
    #: 2026-10-07): no healthy Opus 5.5 parser call of 420 finished between 28.1 and 50 s, so inside an op a parser
    #: call past 20 s is a hang and moves to Gemini 3.1 Pro (6 to 12 s). None: the first try keeps ``first_try_s``.
    op_first_try_s: float | None = None
    #: Round 7 (T4, operator 2026-10-08), inside an op, first try only: the window is
    #: clamp(op_speed_k x median of this agent's last 20 first tries on its primary model on this box (the LLM
    #: ledger), op_speed_floor_s, first_try_s). Under 20 rows the constant stands. Evidence: .claude/work/2026-10-08-cc-slow/analysis/t4_windows.py.
    op_speed_k: float | None = None
    op_speed_floor_s: float = 20


#: Unlisted agents in the recovery ladder: the budgets every agent had before this table.
DEFAULT_BUDGET = CallBudget(first_try_s=300, moved_s=180)

#: Unlisted agents in a tool loop (tool_loop.call_tools): its own defaults before this table.
TOOL_LOOP_DEFAULT_BUDGET = CallBudget(first_try_s=120, moved_s=120)

CALL_BUDGETS: dict[str, CallBudget] = {
    # Gemini 3.8 Flash primaries (sized on Gemini 3.5 Flash), Sonnet 5.5 fallback. The entity's first try was 20 s;
    # at medium thinking it gets 30 s (operator, 2026-09-30).
    "entity": CallBudget(first_try_s=30, moved_s=90),
    "api": CallBudget(first_try_s=30, moved_s=90),
    "chatter": CallBudget(first_try_s=30, moved_s=90),
    "reporter": CallBudget(first_try_s=30, moved_s=90),
    "seqera_agent": CallBudget(first_try_s=30, moved_s=90),
    "system": CallBudget(first_try_s=45, moved_s=90),
    "memory_coder": CallBudget(first_try_s=45, moved_s=90),
    "graph": CallBudget(first_try_s=60, moved_s=90, move_reserve_only_in_op=True, op_speed_k=5),
    # Sonnet 5.5 primary (the legacy memory agent), Gemini 3.8 Flash fallback.
    "memory": CallBudget(first_try_s=60, moved_s=90),
    # Opus primaries, Gemini 3.1 Pro fallback. The first try was 35 s (ruling 9, 2026-09-25); run 2's always-thinking
    # Opus 5.5 gets 50 s (operator, 2026-09-28). The move keeps 60 s, and a timeout here still marks nothing (D3).
    "parser": CallBudget(first_try_s=50, moved_s=60, timeout_marks_model=False, op_first_try_s=20,
                           op_speed_k=4),
    "multi_parser": CallBudget(first_try_s=50, moved_s=60, timeout_marks_model=False, op_first_try_s=20,
                           op_speed_k=4),
    "report_writer": CallBudget(first_try_s=240, moved_s=180, op_move_reserve=False),
    # The tool loops, per step. Opus 5.5 primary (sized on Opus 4.7 without thinking), Sonnet 5.5 fallback (the
    # catalog's _fallback block).
    "followup": CallBudget(first_try_s=60, moved_s=60),
    "pipeline_agent": CallBudget(first_try_s=120, moved_s=120),
}


def budget_for(agent: str | None, *, default: CallBudget = DEFAULT_BUDGET) -> CallBudget:
    """The budget of the agent whose catalog key is ``agent``; ``default`` when the table does not name it."""
    return CALL_BUDGETS.get(agent or "", default)
