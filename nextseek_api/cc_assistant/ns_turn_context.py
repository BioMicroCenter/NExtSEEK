"""Component A (spec-001 §4.A): deterministic NSTurnContext projection.

Pure bundle → typed descriptor; no LLM, no DB. orjson for serialization;
pydantic v2 for validation. Malformed bundles are skipped in build_contexts
(best-effort digest, §6)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

SCHEMA_VERSION = "nsctx/v1"
_REPLY_CAP = 2000
_SAMPLE_UIDS_CAP = 20


class NSResultSummary(BaseModel):
    endpoint: str | None = None
    method: str | None = None
    total: int | None = None
    row_count: int = 0
    truncated: bool = False
    columns: list[str] = Field(default_factory=list)
    sample_uids: list[str] = Field(default_factory=list)


class NSTurnContext(BaseModel):
    schema_version: str = SCHEMA_VERSION
    session_id: str
    turn_id: int
    bundle_id: int
    route: Literal["ns"] = "ns"
    ts: str
    mode: str
    user_query: str
    reply: str
    reply_truncated: bool = False
    ok: bool = True
    error: str | None = None
    result: NSResultSummary = Field(default_factory=NSResultSummary)
    filters: dict[str, Any] | None = None
    full_result_available: bool = False


NSTurnContextList = TypeAdapter(list[NSTurnContext])


def _total_and_rows(api_result_full: dict) -> tuple[int | None, int, list]:
    """(total, row_count, rows) via pure dict access — parity-tested against
    chat_nextseek helpers/tools/nextseek_api._extract_total_and_rows."""
    data = api_result_full.get("data") if isinstance(api_result_full, dict) else None
    for container in (data, api_result_full):
        if not isinstance(container, dict):
            continue
        total = (container.get("total") or container.get("total_samples")
                 or container.get("total_nodes"))
        rows = None
        for key in ("rows", "nodes", "data"):
            cand = container.get(key)
            if isinstance(cand, list):
                rows = cand
                break
        if rows is not None:
            return total, len(rows), rows
        if container is data:
            # mirror the real helper: a wrapped dict answers even without rows
            return total, 0, []
    return None, 0, []


def from_bundle(bundle: dict, *, session_id: str, turn_id: int) -> NSTurnContext:
    api_full = bundle.get("api_result_full") or {}
    ok = bool(api_full.get("ok", True))
    total, row_count, rows = _total_and_rows(api_full)
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    reply_raw = str(bundle.get("terminal_reply") or "")
    truncated_reply = len(reply_raw) > _REPLY_CAP
    return NSTurnContext(
        session_id=session_id,
        turn_id=turn_id,
        bundle_id=int(bundle.get("id") or 0),
        ts=str(bundle.get("timestamp") or ""),
        mode=str(bundle.get("mode") or ""),
        user_query=str(bundle.get("user_query") or ""),
        reply=reply_raw[:_REPLY_CAP],
        reply_truncated=truncated_reply,
        ok=ok,
        error=None if ok else str(api_full.get("error") or "NS turn failed"),
        result=NSResultSummary(
            endpoint=bundle.get("endpoint"),
            method=bundle.get("method"),
            total=total if isinstance(total, int) and not isinstance(total, bool) else None,
            row_count=row_count,
            truncated=bool(isinstance(total, int) and not isinstance(total, bool)
                             and row_count < total),
            columns=[str(k) for k in first.keys()],
            sample_uids=[str(r.get("uid")) for r in rows[:_SAMPLE_UIDS_CAP]
                         if isinstance(r, dict) and r.get("uid")],
        ),
        filters=(bundle.get("parser_plan") or {}).get("filters")
                if isinstance(bundle.get("parser_plan"), dict) else None,
        full_result_available=row_count > 0,
    )


def build_contexts(chat_log, results_history, *, session_id: str) -> list[NSTurnContext]:
    """One context per prior NS DATA turn: chat_log entries with an int
    bundle_id joined to their bundle (turn_id from the entry — §4.A row 6).
    CC, unrelated, error, and bundle-less entries contribute nothing.
    Malformed bundles are skipped (best-effort, §6)."""
    by_id = {b.get("id"): b for b in (results_history or []) if isinstance(b, dict)}
    out: list[NSTurnContext] = []
    for e in (chat_log or []):
        if not isinstance(e, dict) or e.get("mode") == "cc":
            continue
        bid, tid = e.get("bundle_id"), e.get("turn_id")
        if not isinstance(bid, int) or isinstance(bid, bool):
            continue
        if not isinstance(tid, int) or isinstance(tid, bool):
            continue
        bundle = by_id.get(bid)
        if bundle is None:
            continue
        try:
            out.append(from_bundle(bundle, session_id=session_id, turn_id=tid))
        except Exception:  # noqa: BLE001 - digest is best-effort (§6)
            continue
    return out
