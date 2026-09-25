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


#: Where a row names its sample: a REST row's ``uid``, the graph's ``uuid``, the metadata ``UID``.
_UID_KEYS = ("uid", "uuid", "UID")


def _row_uid(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in _UID_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _graph_rows(bundle: dict) -> tuple[bool, str | None, int | None, bool, list] | None:
    """(ok, error, total, capped, rows) of a graph turn, whose rows are ``graph_result.data``;
    None for a REST turn."""
    graph = bundle.get("graph_result")
    if not isinstance(graph, dict) or not isinstance(graph.get("data"), list):
        return None
    rows = graph["data"]
    capped = bool(graph.get("truncated"))
    total = graph.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        total = None if capped else len(rows)
    ok = bool(graph.get("ok", True))
    return ok, None if ok else str(graph.get("error") or "NS turn failed"), total, capped, rows


def from_bundle(bundle: dict, *, session_id: str, turn_id: int) -> NSTurnContext:
    from chat_nextseek.artifacts import load_api_result_full

    api_full = load_api_result_full(bundle)
    graph = _graph_rows(bundle)
    # A graph turn keeps its rows in the bundle itself. Reading only the REST result, the digest
    # said rows=0 for every one of them and ``nextseek-recall`` found nothing (r6-1228). A plan
    # bundle stores a (possibly empty) graph list beside its REST result: the graph rows count
    # only when there are some, or when there is no REST result at all.
    if graph is not None and (graph[4] or not api_full):
        ok, error, total, capped, rows = graph
        row_count = len(rows)
    else:
        capped = False
        ok = bool(api_full.get("ok", True))
        error = None if ok else str(api_full.get("error") or "NS turn failed")
        total, row_count, rows = _total_and_rows(api_full)
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    reply_raw = str(bundle.get("terminal_reply") or "")
    truncated_reply = len(reply_raw) > _REPLY_CAP
    uids = [u for u in (_row_uid(r) for r in rows) if u is not None]
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
        error=error,
        result=NSResultSummary(
            endpoint=bundle.get("endpoint"),
            method=bundle.get("method"),
            total=total if isinstance(total, int) and not isinstance(total, bool) else None,
            row_count=row_count,
            truncated=capped or bool(isinstance(total, int) and not isinstance(total, bool)
                                     and row_count < total),
            columns=[str(k) for k in first.keys()],
            sample_uids=uids[:_SAMPLE_UIDS_CAP],
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
