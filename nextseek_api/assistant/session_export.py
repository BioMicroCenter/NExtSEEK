"""One chat session as the user sees it, and as one downloadable zip.

``turn_rows`` is the turn list the chat UI renders
(``GET /assistant/sessions/{sid}/?include=turns``), kept together with the
records each turn came from, so that anything exported from a session numbers
and filters its turns exactly as the screen does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from NessieAI.ns.debug_projection import bundle_debug_entries
from nextseek_api.assistant.excel_export import build_artifacts
from nextseek_api.assistant.models_api import Turn


@dataclass(frozen=True)
class TurnRow:
    """One turn as the chat UI shows it, with the raw records behind it."""

    payload: dict[str, Any]          # ``Turn(...).model_dump(mode="json")``
    entry: dict[str, Any] | None     # the chat_log entry; None for a legacy bundle-only turn
    bundle: dict[str, Any] | None    # the NS bundle the turn wrote, if any


def turn_rows(session) -> list[TurnRow]:
    """The turns of ``session`` in the order and with the filter the chat UI uses.

    Walks ``chat_log`` when there is one and ``results_history`` otherwise.
    Entries with no reply and no bundle are hidden (PD-6: unrelated and error
    turns), while legacy preview-only turns keep rendering.
    """
    history = session.results_history or []
    chat_log = (session.extra_state or {}).get("chat_log") or []
    bundles_by_id = {b.get("id"): b for b in history if isinstance(b, dict)}
    rows: list[TurnRow] = []
    if chat_log:
        for entry in chat_log:
            if not (entry or {}).get("user_query"):
                continue
            bid = entry.get("bundle_id")
            bundle = bundles_by_id.get(bid) if bid is not None else None
            if not (entry.get("assistant_reply")
                    or entry.get("assistant_reply_preview")
                    or bundle):
                # PD-6: hide ONLY true non-answer entries (unrelated/error,
                # F §12.3). Legacy preview-only turns keep rendering.
                continue
            # Prefer the full reply stored directly on the chat_log entry
            # (wizard turns don't produce bundles, so this is the only
            # full-text source for them). Fall back to the bundle's
            # terminal_reply for legacy entries written before
            # assistant_reply existed, then to the 280-char preview.
            reply = (
                entry.get("assistant_reply")
                or (bundle.get("terminal_reply") or bundle.get("reply") if bundle else None)
                or entry.get("assistant_reply_preview", "")
            ) or ""
            artifacts = entry.get("artifacts") or (build_artifacts(bundle) if bundle else None)
            payload = Turn(
                bundle_id=bid if bid is not None else 0,
                turn_id=entry.get("turn_id") if isinstance(entry.get("turn_id"), int) else None,
                user_query=entry.get("user_query", ""),
                reply=reply,
                mode=entry.get("mode", ""),
                ts=entry.get("ts"),
                artifacts=artifacts or None,
                cc_traces=entry.get("cc_traces"),
                debug_entries=bundle_debug_entries(bundle) or None,
            ).model_dump(mode="json")
            rows.append(TurnRow(payload=payload, entry=entry, bundle=bundle))
    else:
        for b in history:
            if not (b or {}).get("user_query"):
                continue
            payload = Turn(
                bundle_id=b.get("id", 0),
                user_query=b.get("user_query", ""),
                reply=b.get("terminal_reply") or b.get("reply") or "",
                mode=b.get("mode", ""),
                ts=b.get("ts"),
                artifacts=(build_artifacts(b) or None),
                debug_entries=bundle_debug_entries(b) or None,
            ).model_dump(mode="json")
            rows.append(TurnRow(payload=payload, entry=None, bundle=b))
    return rows
