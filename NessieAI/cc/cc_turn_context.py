"""Component (issue #9): deterministic CCTurnContext projection.

Mirror of ns_turn_context for Container-CC turns. A CC turn has no NS row bundle,
so it projects the chat_log entry itself (user_query + reply summary) into a
minimal descriptor for the within-chat digest. Pure — no LLM, no DB; malformed or
unanswered entries are skipped (best-effort digest)."""
from __future__ import annotations


from pydantic import BaseModel, Field, TypeAdapter

SCHEMA_VERSION = "ccctx/v1"
_REPLY_CAP = 2000


class CCTurnContext(BaseModel):
    schema_version: str = SCHEMA_VERSION
    turn_id: int
    route: str = "cc"
    ts: str = ""
    user_query: str
    reply: str = ""
    reply_truncated: bool = False
    status: str = "completed"


CCTurnContextList = TypeAdapter(list[CCTurnContext])


def _is_answered_cc(e) -> bool:
    return (isinstance(e, dict) and e.get("mode") == "cc"
            and e.get("status") == "completed"
            and isinstance(e.get("turn_id"), int) and not isinstance(e.get("turn_id"), bool))


def build_cc_contexts(chat_log) -> list[CCTurnContext]:
    """One context per prior ANSWERED CC turn (mode=='cc', status=='completed'), plus each crashed turn."""
    out: list[CCTurnContext] = []
    from chat_nextseek.chat_memory import _failed_view, _is_failed

    for e in (chat_log or []):
        if _is_failed(e) and isinstance(e.get("turn_id"), int) and not isinstance(e.get("turn_id"), bool):
            # a crashed turn (any route): its question plus the fixed marker, as recent_turns shows it
            e = {**e, "assistant_reply": _failed_view(e)["assistant_reply_preview"]}
            route = str(e.get("mode") or "unknown")
        elif not _is_answered_cc(e):
            continue
        else:
            route = "cc"
        reply_raw = str(e.get("assistant_reply") or "")
        try:
            out.append(CCTurnContext(
                turn_id=int(e["turn_id"]), route=route,
                ts=str(e.get("ts") or ""),
                user_query=str(e.get("user_query") or ""),
                reply=reply_raw[:_REPLY_CAP],
                reply_truncated=len(reply_raw) > _REPLY_CAP,
                status=str(e.get("status") or "completed"),
            ))
        except Exception:  # noqa: BLE001 - digest is best-effort
            continue
    return out
