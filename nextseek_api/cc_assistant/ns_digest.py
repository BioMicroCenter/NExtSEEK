"""Component B (spec-001 §4.B): deterministic NSTurnContext digest renderer.

Pure string building — no LLM, no DB, no clock. Merged into per-turn CLAUDE.md
beside cross-session memory (see compose_turn_claude_md)."""
from __future__ import annotations

from nextseek_api.cc_assistant.cc_turn_context import CCTurnContext
from nextseek_api.cc_assistant.ns_turn_context import NSTurnContext

DIGEST_MAX_TURNS = 10
DIGEST_HEADER = "## Prior NExtSEEK results in this chat"


def compose_turn_claude_md(digest_md: str, memory_md: str) -> str:
    """Merge within-chat digest (first) with cross-session memory (second)."""
    return "\n\n".join(p for p in (digest_md, memory_md) if p)


def _render_turn(ctx: NSTurnContext) -> list[str]:
    lines = [
        f"- turn {ctx.turn_id} (bundle {ctx.bundle_id}): {ctx.user_query}",
        f"  endpoint: {ctx.result.endpoint or ''}",
        f"  total={ctx.result.total}, rows={ctx.result.row_count}",
    ]
    if ctx.result.columns:
        lines.append(f"  columns: {', '.join(ctx.result.columns)}")
    if ctx.result.sample_uids:
        lines.append(f"  sample UIDs: {', '.join(ctx.result.sample_uids)}")
    if ctx.full_result_available:
        lines.append(f"  recall with `nextseek-recall --turn {ctx.turn_id}`")
    else:
        lines.append("  (no raw rows available)")
    return lines


def render_digest(contexts: list[NSTurnContext]) -> str:
    """Render a compact markdown digest; empty string when no contexts."""
    if not contexts:
        return ""

    capped = len(contexts) > DIGEST_MAX_TURNS
    visible = contexts[-DIGEST_MAX_TURNS:] if capped else contexts

    lines = [DIGEST_HEADER, ""]
    if capped:
        lines.append(f"(showing the {DIGEST_MAX_TURNS} most recent NS turns)")
        lines.append("")
    for ctx in visible:
        lines.extend(_render_turn(ctx))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


CC_DIGEST_HEADER = "## Prior Container-CC turns in this chat"
_CC_REPLY_LINE_CAP = 200


def _render_cc_turn(ctx: CCTurnContext) -> list[str]:
    lines = [f"- turn {ctx.turn_id} (CC): {ctx.user_query}"]
    reply = " ".join(ctx.reply.split())
    if reply:
        if len(reply) > _CC_REPLY_LINE_CAP:
            reply = reply[:_CC_REPLY_LINE_CAP] + "…"
        lines.append(f"  answer: {reply}")
    return lines


def render_cc_digest(contexts: list[CCTurnContext]) -> str:
    """Render prior Container-CC turns as context lines; empty string when none.
    CC turns carry no NS row bundle, so there is no `nextseek-recall` affordance."""
    if not contexts:
        return ""
    capped = len(contexts) > DIGEST_MAX_TURNS
    visible = contexts[-DIGEST_MAX_TURNS:] if capped else contexts
    lines = [CC_DIGEST_HEADER, ""]
    if capped:
        lines.append(f"(showing the {DIGEST_MAX_TURNS} most recent CC turns)")
        lines.append("")
    for ctx in visible:
        lines.extend(_render_cc_turn(ctx))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def render_within_chat_digest(ns_contexts, cc_contexts) -> str:
    """Combined within-chat digest: the NS-results section (with recall) followed
    by the CC-turns section. Either may be empty."""
    return "\n\n".join(p for p in (render_digest(ns_contexts), render_cc_digest(cc_contexts)) if p)
