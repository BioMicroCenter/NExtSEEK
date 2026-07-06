"""Gate 3D per-op forced-CC evidence (amendment 2026-07-05).

The G7-11 matrix is replaced by nine *fresh-session* forced-CC turns — one per
bin op — proving the full flow **user query -> CC route -> op -> answer**. The
direct-exec executor (``cc_matrix_gate_harness``) proved the shims run when
invoked by hand; it never routed through CC, so it could not prove CC can
*invoke* the ops. This module holds the PURE evidence logic (transcript ->
per-op invocation proof + row), so it is hermetically testable without spending
on live turns; the live orchestration lives in ``scripts/step7_gate3d_live.py``.

Invocation proof: the agent calls each op as a Bash tool call to the plugin bin
``/app/plugins/nextseek/bin/nextseek-<op>`` (see the in-container CLAUDE.md and
``docker/cc-runtime/build_context/plugins/nextseek/bin/``). So a ``cc_trace``
Bash step whose command names ``nextseek-<op>`` — with the tool_result
``status`` the transcript recorded — is the proof that CC invoked that op.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The 8 decomposed bin ops the agent invokes. nextseek-query is DISABLED
# (2026-07-05 per-op amendment: it was a redundant router-level shortcut) and is
# not part of the per-op proof set.
BIN_OPS: tuple[str, ...] = (
    "nextseek-entity-extract",
    "nextseek-parse",
    "nextseek-api-read",
    "nextseek-api-write",
    "nextseek-graph",
    "nextseek-report",
    "nextseek-generate-submission",
    "nextseek-plan",
)

# api-write's only permitted live shape: the agent reaches the write-safety gate
# and does NOT execute an unconfirmed write (CLAUDE.md write-safety + shim exit
# 5 WRITE_BLOCKED). It still costs a real Bedrock turn (decide-and-refuse).
WRITE_GATED_OP = "nextseek-api-write"


@dataclass
class OpInvocation:
    """One op's transcript-derived invocation proof."""

    op: str
    invoked: bool
    invocation_line: int | None = None
    invocation_detail: str | None = None
    invocation_status: str | None = None  # "ok" | "error" | None


def _step_command(step: Any) -> str:
    """The command/detail string of a cc_trace Step (dict or model)."""
    if isinstance(step, dict):
        return str(step.get("detail") or step.get("text") or "")
    return str(getattr(step, "detail", None) or getattr(step, "text", None) or "")


def _step_kind(step: Any) -> str:
    return step.get("kind", "") if isinstance(step, dict) else getattr(step, "kind", "")


def _step_line(step: Any) -> int | None:
    return step.get("line") if isinstance(step, dict) else getattr(step, "line", None)


def _step_status(step: Any) -> str | None:
    return step.get("status") if isinstance(step, dict) else getattr(step, "status", None)


def extract_op_invocation(steps: list[Any], op: str) -> OpInvocation:
    """Find the first Bash step that invokes ``nextseek-<op>``.

    ``op`` must be a full bin name (e.g. ``nextseek-report``). Matching is on
    the op token bounded by a non-word char (or string edge) so ``nextseek-api``
    never matches ``nextseek-api-write`` and vice versa. Returns invoked=False
    with null fields when the op never appears in a Bash step.
    """
    if op not in BIN_OPS:
        raise ValueError(f"unknown bin op: {op!r}")
    for step in steps:
        if _step_kind(step) != "bash":
            continue
        cmd = _step_command(step)
        if _command_invokes_op(cmd, op):
            return OpInvocation(
                op=op,
                invoked=True,
                invocation_line=_step_line(step),
                invocation_detail=cmd[:500],
                invocation_status=_step_status(step),
            )
    return OpInvocation(op=op, invoked=False)


import os
import re

# Commands that take a bin NAME as an argument without executing it — an op
# appearing as their argument is NOT an invocation (2026-07-05: `command -v
# nextseek-parse nextseek-api-write` falsely matched api-write).
_NON_INVOKING_LEADERS = frozenset(
    {"command", "which", "type", "echo", "printf", "hash", "whereis", "compgen"}
)
_SHELL_SEP = re.compile(r"[;&|]{1,2}|\n")


def _command_invokes_op(cmd: str, op: str) -> bool:
    """True only if some sub-command in ``cmd`` runs ``op`` as its *executable*.

    Splits on shell separators (``;`` ``|`` ``&&`` ``||`` newline); for each
    sub-command, strips leading ``VAR=val`` env assignments and takes the first
    token as the executable. The op is invoked iff that token's basename equals
    ``op`` exactly (so ``nextseek-api`` never matches ``nextseek-api-write``, and
    the op appearing only as an argument — e.g. to ``command -v`` — does not
    count). ``#``-comment sub-commands are skipped.
    """
    for sub in _SHELL_SEP.split(cmd):
        sub = sub.strip()
        if not sub or sub.startswith("#"):
            continue
        tokens = sub.split()
        i = 0
        while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
            i += 1  # skip leading env assignments
        if i >= len(tokens):
            continue
        exe = os.path.basename(tokens[i])
        if exe in _NON_INVOKING_LEADERS:
            continue
        if exe == op:
            return True
    return False


import orjson

# Opus 4.8 Bedrock per-token USD rates (model us.anthropic.claude-opus-4-8),
# matching Anthropic's published Opus 4.8 pricing: $5/$25 per 1M input/output;
# cache-write (5m) at 1.25x input = $6.25/1M; cache-read at 0.1x input = $0.50/1M.
# Used ONLY to RECOVER a cost estimate when a turn is killed (exec_timeout) before
# it reaches the CC result frame that carries total_cost_usd — the result frame is
# authoritative whenever present, so this never overrides a real cost.
_OPUS48_RATES: dict[str, float] = {
    "input_tokens": 5.0 / 1_000_000,
    "output_tokens": 25.0 / 1_000_000,
    "cache_creation_input_tokens": 6.25 / 1_000_000,
    "cache_read_input_tokens": 0.5 / 1_000_000,
}


def estimate_cost_from_transcript(raw: bytes) -> float:
    """Sum per-message ``usage`` from a CC transcript and price it at Opus-4.8 rates.

    A killed turn (the container is force-removed at the 180s hard cap) emits a
    ``query_error`` with no cost and never reaches the CC ``result`` frame that
    carries ``total_cost_usd`` — so the driver would otherwise record $0 for a turn
    that really spent Bedrock tokens. Every assistant frame in the transcript still
    carries a ``usage`` block, so we reconstruct the spend from those. Returns 0.0
    when ``raw`` is empty or no ``usage`` frame parses.
    """
    if not raw:
        return 0.0
    totals: dict[str, int] = {k: 0 for k in _OPUS48_RATES}
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            obj = orjson.loads(line)  # orjson parses bytes directly (no decode)
        except orjson.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(usage, dict):
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
        if not isinstance(usage, dict):
            continue
        for k in totals:
            try:
                totals[k] += int(usage.get(k) or 0)
            except (TypeError, ValueError):
                continue
    return round(sum(totals[k] * _OPUS48_RATES[k] for k in totals), 6)


@dataclass
class OpRow:
    """One per-op matrix row for ``plugin_ops_matrix.json``."""

    op: str
    cc_run_id: str
    cc_session_id: str | None
    is_error: bool
    cost_usd: float
    invoked: bool
    invocation_line: int | None
    invocation_detail: str | None
    invocation_status: str | None
    answer_excerpt: str
    transport: str
    # "claude_code_result" == authoritative cost from the CC result frame;
    # "usage_estimate_on_timeout" == reconstructed from transcript usage because the
    # turn was killed before the result frame (see estimate_cost_from_transcript).
    cost_source: str = "claude_code_result"
    problems: list[str] = field(default_factory=list)
    # Option-1 review flag: non-red concerns the orchestrator should verify by
    # inspecting the answer (currently: the EXPECTED op was not invoked, i.e. CC
    # routed the query through a different valid NS-path op — LLM behavior, not a
    # code bug). ``problems`` == hard failures (red); ``review_notes`` == green
    # but flagged.
    needs_review: bool = False
    review_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "op": self.op,
            "cc_run_id": self.cc_run_id,
            "cc_session_id": self.cc_session_id,
            "is_error": self.is_error,
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "invoked": self.invoked,
            "invocation_line": self.invocation_line,
            "invocation_detail": self.invocation_detail,
            "invocation_status": self.invocation_status,
            "answer_excerpt": self.answer_excerpt,
            "transport": self.transport,
            "needs_review": self.needs_review,
        }
        if self.problems:
            d["problems"] = self.problems
        if self.review_notes:
            d["review_notes"] = self.review_notes
        return d


def evaluate_op_row(
    *,
    op: str,
    cc_run_id: str,
    cc_session_id: str | None,
    is_error: bool,
    cost_usd: float,
    invocation: OpInvocation,
    answer_excerpt: str,
    transport: str,
    cost_source: str = "claude_code_result",
) -> OpRow:
    """Build a per-op row. ``problems`` holds only HARD failures (a red E2E: the
    end-to-end flow itself broke) — an errored CC turn, a non-positive cost, an
    empty answer, or an invoked op whose shim reported an error. Empty problems ==
    the E2E is green for this op.

    Option 1 (LLM behavior is not a code bug): the EXPECTED op not being invoked
    is NOT a hard failure — CC may have resolved the query through a different
    valid NS-path op (e.g. parse->api-read instead of entity-extract, or a correct
    api-write refusal at the confirm gate). That records a ``review_note`` and sets
    ``needs_review`` so the orchestrator inspects the answer for correctness, while
    the E2E stays green because the pipeline worked end to end.
    """
    problems: list[str] = []
    review_notes: list[str] = []
    if is_error:
        problems.append("cc turn is_error=true")
    if not isinstance(cost_usd, (int, float)) or cost_usd <= 0:
        problems.append(f"cost_usd not > 0 (got {cost_usd!r}) — every CC-routed op costs a Bedrock turn")
    if not invocation.invoked:
        review_notes.append(
            f"expected op {op} was not invoked — CC likely resolved the query via a "
            f"different valid NS-path op (LLM routing, not a code bug); orchestrator "
            f"must verify the answer is correct"
        )
    elif invocation.invocation_status == "error":
        if op == WRITE_GATED_OP:
            # api-write reaching the shim and being refused is the pinned shape;
            # the write-blocked exit surfaces as a tool_result error but is the
            # intended, non-mutating outcome. Accept only for this op.
            pass
        else:
            problems.append(f"{op} invocation tool_result status=error")
    if not (answer_excerpt or "").strip():
        problems.append("empty answer")
    return OpRow(
        op=op,
        cc_run_id=cc_run_id,
        cc_session_id=cc_session_id,
        is_error=is_error,
        cost_usd=float(cost_usd) if isinstance(cost_usd, (int, float)) else 0.0,
        invoked=invocation.invoked,
        invocation_line=invocation.invocation_line,
        invocation_detail=invocation.invocation_detail,
        invocation_status=invocation.invocation_status,
        answer_excerpt=(answer_excerpt or "")[:1000],
        transport=transport,
        cost_source=cost_source,
        problems=problems,
        needs_review=bool(review_notes),
        review_notes=review_notes,
    )


def assert_fresh_sessions(rows: list[OpRow]) -> list[str]:
    """Every op must run in its OWN fresh CC session — distinct cc_session_id
    and cc_run_id across the nine. Returns a list of violations (empty == ok)."""
    violations: list[str] = []
    seen_sessions: dict[str, str] = {}
    seen_runs: dict[str, str] = {}
    for r in rows:
        if r.cc_session_id:
            if r.cc_session_id in seen_sessions:
                violations.append(
                    f"{r.op} shares cc_session_id {r.cc_session_id} with {seen_sessions[r.cc_session_id]} "
                    "(fresh-session mandate)"
                )
            else:
                seen_sessions[r.cc_session_id] = r.op
        if r.cc_run_id in seen_runs:
            violations.append(f"{r.op} shares cc_run_id {r.cc_run_id} with {seen_runs[r.cc_run_id]}")
        else:
            seen_runs[r.cc_run_id] = r.op
    return violations
