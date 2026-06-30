"""Django-free read-path helpers for Step 1c cross-session memory: which sessions
to remember (window) / summarize now (sync target), and how to render them.

Import-light (stdlib only) so the hermetic suite can import it without Django."""
from __future__ import annotations

from dataclasses import dataclass

_CONF_ORDER = {"high": 0, "medium": 1, "low": 2}
_CATEGORY_ORDER = ["decision", "preference", "todo", "artifact", "context", "fact"]


@dataclass(frozen=True)
class SessionMeta:
    session_id: str
    updated_at: float          # epoch seconds (Django side converts; helper stays pure)
    fingerprint: dict | None   # extra_state["summary_fingerprint"] or None
    summary: dict | None       # extra_state["summary"] (a SessionSummary dict) or None
    transcript_path: str | None
    changed: bool              # precomputed: transcript fingerprint != stored


def select_window(sessions: list[SessionMeta], current_id: str,
                  window_size: int) -> list[SessionMeta]:
    """The `window_size` most-recent sessions by updated_at, excluding current_id."""
    others = [s for s in sessions if s.session_id != current_id]
    others.sort(key=lambda s: s.updated_at, reverse=True)
    return others[:window_size]


def select_sync_target(sessions: list[SessionMeta], current_id: str) -> SessionMeta | None:
    """The single most-recent CHANGED non-current session (sync-summarize this turn)."""
    candidates = [s for s in sessions if s.session_id != current_id and s.changed]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.updated_at)


def _flat_items(window: list[SessionMeta]) -> list[dict]:
    """Flatten items across the window, tagging each with its session recency rank."""
    flat: list[dict] = []
    for rank, meta in enumerate(window):  # window is already most-recent-first
        summ = meta.summary or {}
        for it in summ.get("items", []) or []:
            flat.append({**it, "_recency": rank, "_session": meta.session_id})
    return flat


def render_memory(window: list[SessionMeta], *, fresh_session: bool,
                  transcripts_mount: str) -> str:
    """Deterministic user-tier memory markdown + raw-transcript pointer block.

    Returns "" when fresh_session or there is nothing to render (caller then skips
    the memory mounts). Output is a function of the inputs only (no clock/random)."""
    if fresh_session:
        return ""
    items = _flat_items(window)
    if not window or not items:
        return ""

    lines: list[str] = [
        "# Cross-session memory (auto-generated — do not edit)",
        "",
        "Durable takeaways distilled from your recent sessions. This composes with "
        "the project instructions; it does not replace them.",
        "",
    ]
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        by_cat.setdefault(str(it.get("category", "fact")).lower(), []).append(it)

    for cat in _CATEGORY_ORDER:
        group = by_cat.get(cat)
        if not group:
            continue
        group.sort(key=lambda i: (_CONF_ORDER.get(str(i.get("confidence", "low")).lower(), 3),
                                  i.get("_recency", 999)))
        lines.append(f"## {cat.capitalize()}")
        for it in group:
            stmt = str(it.get("statement", "")).strip()
            evs = it.get("evidence", []) or []
            unverified = any(not ev.get("verified", False) for ev in evs)
            flag = "  _(unverified)_" if unverified else ""
            lines.append(f"- {stmt}{flag}")
        lines.append("")

    lines.append("## Recent session transcripts (read-only, on-demand)")
    lines.append(
        f"Full transcripts of these sessions are mounted read-only under "
        f"`{transcripts_mount}`. Read one only if you need detail beyond the "
        f"takeaways above.")
    lines.append("")
    for meta in window:
        gist = str((meta.summary or {}).get("gist", "")).strip() or "(no summary)"
        lines.append(f"- `{transcripts_mount}/{meta.session_id}.jsonl` — {gist}")
    lines.append("")
    return "\n".join(lines)
