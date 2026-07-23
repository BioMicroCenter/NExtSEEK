"""Component F (spec-001 §12.4): deterministic router-history builder.

Pure projection of extra_state["chat_log"] tail into typed HistoryTurn[].
No LLM, no DB access, no logging of message text (R-03-adjacent). Caps are
byte-based, UTF-8-boundary-safe (F-7). Malformed entries are skipped — routing
must never crash on bad history (§12.7)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

RouterChoice = Literal["nextseek_query", "container_cc", "unrelated"]

USER_MSG_CAP = 512
REPLY_CAP = 1024
ERROR_CAP = 256
UID_CAP = 64
MAX_HISTORY_TURNS = 5
SAMPLE_UIDS_MAX = 3
_ELLIPSIS = "…"


def truncate_utf8(s: str, cap: int) -> str:
    """Byte-cap `s` without splitting a codepoint; append … only when cut."""
    raw = s.encode("utf-8")
    if len(raw) <= cap:
        return s
    cut = raw[:cap]
    while cut:
        try:
            return cut.decode("utf-8") + _ELLIPSIS
        except UnicodeDecodeError:
            cut = cut[:-1]
    return _ELLIPSIS


class HistoryTurn(BaseModel):
    position: int
    user_message: str
    assistant_reply: Optional[str] = None
    router_choice: Optional[RouterChoice] = None
    status: Literal["completed", "error"] = "completed"
    error: Optional[str] = None
    result_count: Optional[int] = None
    sample_uids: list[str] = Field(default_factory=list)

    @field_validator("assistant_reply", "error")
    @classmethod
    def _never_empty_string(cls, v):
        if v == "":
            raise ValueError("must be None, never the empty string (F-4)")
        return v


def _derive_router_choice(entry: dict) -> Optional[RouterChoice]:
    rc = entry.get("router_choice")
    if rc in ("nextseek_query", "container_cc", "unrelated"):
        return rc
    # Legacy derivation (§12.2): mode=="cc" → container_cc, else nextseek_query.
    return "container_cc" if entry.get("mode") == "cc" else "nextseek_query"


def _one(entry: Any) -> Optional[HistoryTurn]:
    if not isinstance(entry, dict):
        return None
    tid = entry.get("turn_id")
    if not isinstance(tid, int) or isinstance(tid, bool):
        return None
    reply = entry.get("assistant_reply") or None      # "" (legacy NS) → None
    err = entry.get("error") or None
    status = entry.get("status")
    if status not in ("completed", "error"):
        status = "completed"                          # legacy assumption (§12.2)
    rs = entry.get("result_summary") or {}
    count = rs.get("count") if isinstance(rs, dict) else None
    uids = rs.get("first_uids") if isinstance(rs, dict) else None
    sample = [truncate_utf8(str(u), UID_CAP)
              for u in (uids or [])[:SAMPLE_UIDS_MAX]]
    try:
        return HistoryTurn(
            position=tid,
            user_message=truncate_utf8(str(entry.get("user_query") or ""), USER_MSG_CAP),
            assistant_reply=truncate_utf8(reply, REPLY_CAP) if reply else None,
            router_choice=_derive_router_choice(entry),
            status=status,
            error=truncate_utf8(err, ERROR_CAP) if err else None,
            result_count=count if isinstance(count, int) and not isinstance(count, bool) else None,
            sample_uids=sample,
        )
    except Exception:
        return None


def build_history(chat_log: Any, *, limit: int = MAX_HISTORY_TURNS) -> list[HistoryTurn]:
    """Last `limit` chat_log entries (ALL kinds — answered, unrelated, error),
    oldest→newest. Read BEFORE anything is appended for the current turn."""
    if limit <= 0:
        return []
    if not isinstance(chat_log, list):
        return []
    turns = [t for t in (_one(e) for e in chat_log) if t is not None]
    return turns[-limit:]
