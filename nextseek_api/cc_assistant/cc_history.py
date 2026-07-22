"""Compact conversation-history block for the stateless top-level router and the
Container-CC turn (#8: cross-route shared memory).

The top-level dmac router (``cc_router.decide``) and a fresh CC turn are both
context-blind: the router classifies each query on its own text, and a CC turn
only ever staged prior *CC* transcripts. So a follow-up like "counts of sex and
species of those monkeys" loses its referent when the previous turn was an NS
search, and the CC agent re-queries everything.

This builds one small, plain-text history block from the *unified* chat_log
(``extra_state['chat_log']`` — Taisha's turn-id work made it span BOTH NS and CC
turns), which two consumers inject:
  - the router, so it recognises a follow-up and routes it to CC (the route we
    want memory-style questions to land on); and
  - the CC turn's prompt, so "those" resolves even when the prior turn was NS.

Pure stdlib so it is unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _turn_line(turn: dict[str, Any]) -> str | None:
    """One ``- user asked: "..." -> <result>`` line, or None if unusable."""
    query = _clip(str(turn.get("user_query") or ""), 200)
    if not query:
        return None
    summary = ""
    rs = turn.get("result_summary")
    if isinstance(rs, dict):
        count = rs.get("count")
        if isinstance(count, int):
            summary = f"{count} result(s)"
            uids = rs.get("first_uids")
            if isinstance(uids, list) and uids:
                examples = ", ".join(str(u) for u in uids[:3])
                summary += f" (e.g. {examples})"
    if not summary:
        summary = _clip(str(turn.get("assistant_reply_preview") or ""), 160)
    line = f'- user asked: "{query}"'
    if summary:
        line += f" -> {summary}"
    return line


def build_conversation_history(
    chat_log: Any, *, max_turns: int = 6, max_chars: int = 1600
) -> str:
    """Format the last ``max_turns`` chat_log entries into a compact block.

    Returns "" when there is no usable prior turn (so callers can skip
    augmentation on turn 1). The block is capped at ``max_chars`` (keeping the
    most recent turns).
    """
    if not isinstance(chat_log, list) or not chat_log:
        return ""
    lines: list[str] = []
    for turn in chat_log[-max_turns:]:
        if not isinstance(turn, dict):
            continue
        line = _turn_line(turn)
        if line:
            lines.append(line)
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[-max_chars:]
    return block


def cc_prompt_with_history(query: str, history: str) -> str:
    """Prefix a CC turn's prompt with the conversation history so the agent can
    resolve references (e.g. "those monkeys") to earlier NS/CC results. Returns
    the raw query unchanged when there is no history (turn 1)."""
    if not history:
        return query
    return (
        f"{query}\n\n"
        "--- Earlier in this conversation (prior turns and their NExtSEEK "
        'results; use to resolve references like "those" / "them") ---\n'
        f"{history}"
    )
