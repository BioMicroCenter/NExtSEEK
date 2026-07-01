"""Step 3 — per-turn activity trace (SPEC-3 §6, enriched).

ONE CCTrace == ONE chat turn. Assembled from THREE sources (§6.1), never conflated:
  1. the persisted .jsonl records -> ordered `steps` (kind/tool/detail/line/status) + tools tally,
  2. the headless `result` frame -> num_turns / duration_ms / cost_usd (passed as ``result_meta``),
  3. the §5 scratch diff -> authoritative files_created/modified + per-step `action`.
Reuses cc_summary.classify_tool_use (one shared classifier with 1c memory) and the
ParsedTranscript counts. jsonl validated with an ordered Union (_Other last).
"""
from __future__ import annotations

import os
from typing import Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from . import cc_summary

SCHEMA_VERSION = "3/trace-v1"


class Step(BaseModel):
    line: int
    kind: Literal["bash", "write", "edit", "read", "skill", "tool", "text"]
    tool: str | None = None
    detail: str | None = None
    text: str | None = None
    action: Literal["created", "modified"] | None = None
    status: Literal["ok", "error"] | None = None


class CCTrace(BaseModel):
    schema_version: str = SCHEMA_VERSION
    cc_session_id: str
    ts: str
    transcript_line_count: int = 0
    turn_count: int = 0
    num_turns: int | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    steps: list[Step] = Field(default_factory=list)
    tools_used: dict[str, int] = Field(default_factory=dict)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


# --- jsonl record union (§6.3): ordered, _Other LAST + total (optional type) ---
class _Assistant(BaseModel):
    type: Literal["assistant"]
    message: dict


class _User(BaseModel):
    type: Literal["user"]
    message: dict | None = None


class _Other(BaseModel):
    type: str | None = None   # optional so blank / {"_type":"unparsed"} lines still validate


RECORDS = TypeAdapter(list[Union[_Assistant, _User, _Other]])


def _content_blocks(message) -> list[dict]:
    content = (message or {}).get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def extract_trace(parsed, *, cc_session_id, ts, files_created, files_modified,
                  result_meta=None) -> CCTrace:
    """Build a CCTrace from a ParsedTranscript + the §5 diff lists + headless meta."""
    validated = RECORDS.validate_python([dict(r) for r in parsed.records])
    created_base = {os.path.basename(p) for p in files_created}
    modified_base = {os.path.basename(p) for p in files_modified}

    steps: list[Step] = []
    tools: dict[str, int] = {}
    by_id: dict[str, Step] = {}
    for idx, rec in enumerate(validated, start=1):
        if isinstance(rec, _Assistant):
            for block in _content_blocks(rec.message):
                btype = block.get("type")
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        steps.append(Step(line=idx, kind="text", text=txt))
                elif btype == "tool_use":
                    kind, tool, detail = cc_summary.classify_tool_use(block)
                    action = None
                    if kind in ("write", "edit") and detail:
                        base = os.path.basename(detail)
                        if base in created_base:
                            action = "created"
                        elif base in modified_base:
                            action = "modified"
                    step = Step(line=idx, kind=kind, tool=tool, detail=detail, action=action)
                    steps.append(step)
                    tools[tool] = tools.get(tool, 0) + 1
                    bid = block.get("id")
                    if bid:
                        by_id[bid] = step
        elif isinstance(rec, _User) and rec.message:
            for block in _content_blocks(rec.message):
                if block.get("type") == "tool_result":
                    step = by_id.get(block.get("tool_use_id"))
                    if step is not None:
                        step.status = "error" if block.get("is_error") else "ok"

    meta = result_meta or {}
    return CCTrace(
        cc_session_id=cc_session_id, ts=ts,
        transcript_line_count=parsed.line_count, turn_count=parsed.turn_count,
        num_turns=meta.get("num_turns"), duration_ms=meta.get("duration_ms"),
        cost_usd=meta.get("cost_usd"),
        steps=steps, tools_used=tools,
        files_created=list(files_created), files_modified=list(files_modified),
    )
