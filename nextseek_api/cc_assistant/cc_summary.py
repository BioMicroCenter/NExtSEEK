"""Django-free transcript distillation helpers for Step 1c cross-session memory.

Import-light (orjson + pydantic + the vendored BAML types) so the hermetic test
suite can import it without a configured Django. The Django/DB touch (reading and
writing ChatSession.extra_state) lives in the service layer; the BAML network
call is lazily/guardedly imported here exactly as the router does.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from dataclasses import dataclass as _dataclass

import orjson

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1c/v1"
_TRUNC_SUFFIX = "…[truncated]"


@dataclass(frozen=True)
class ParsedTranscript:
    raw_lines: tuple[bytes, ...]   # 1-based via index+1; original bytes for grounding
    records: tuple[dict, ...]      # one per raw line (unparsed -> {"_type":"unparsed"})

    @property
    def line_count(self) -> int:
        return len(self.raw_lines)

    @property
    def turn_count(self) -> int:
        return sum(1 for r in self.records if r.get("type") == "user")


@_dataclass(frozen=True)
class SummaryProvenance:
    chat_session_id: str
    claude_session_id: str | None
    transcript_path: str
    chat_model: str
    generated_at: str  # ISO8601; supplied by the caller (Django side stamps it)


def parse_transcript(raw: bytes) -> ParsedTranscript:
    """Single-pass parse: split on newlines, orjson each line, keep raw bytes.

    Trailing empty line from a final newline is dropped; interior blank/malformed
    lines are retained and counted (resilient to transcript-format drift)."""
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    raw_lines: list[bytes] = []
    records: list[dict] = []
    for ln in lines:
        raw_lines.append(ln)
        try:
            obj = orjson.loads(ln) if ln.strip() else {"_type": "unparsed"}
            if not isinstance(obj, dict):
                obj = {"_type": "unparsed"}
        except orjson.JSONDecodeError:
            obj = {"_type": "unparsed"}
        records.append(obj)
    return ParsedTranscript(raw_lines=tuple(raw_lines), records=tuple(records))


def _truncate(text: str, limit: int) -> str:
    if limit > 0 and len(text) > limit:
        return text[:limit] + _TRUNC_SUFFIX
    return text


def _content_text(content) -> str:
    """Flatten a message 'content' (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(str(block.get("text", "")).strip())
        return " ".join(p for p in out if p).strip()
    return ""


def classify_tool_use(block: dict) -> tuple[str, str, str | None]:
    """Classify a tool_use content block into (kind, tool, detail). Shared by the
    1c memory summary (_tool_use_line) and the Step-3 trace (cc_trace) so the two
    can never drift. kind in {bash, write, edit, read, skill, tool}."""
    name = block.get("name", "") or ""
    inp = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
    n = name.lower()
    if n == "bash":
        return "bash", name, (str(inp.get("command", "")) or None)
    if n in ("write", "edit", "multiedit", "notebookedit"):
        kind = "write" if n == "write" else "edit"
        return kind, name, (inp.get("file_path") or inp.get("notebook_path") or None)
    if n == "read":
        return "read", name, (inp.get("file_path") or None)
    if n in ("skill", "task"):
        return "skill", name, (inp.get("skill") or inp.get("subagent_type") or name)
    return "tool", name, None


def _tool_use_line(block: dict, truncate_chars: int) -> str | None:
    kind, name, detail = classify_tool_use(block)
    if kind == "bash":
        return f"bash: {_truncate(detail or '', truncate_chars)}"
    if kind in ("write", "edit"):
        return f"{kind}: {detail or ''}"
    if kind == "read":
        return f"read: {detail or ''}"
    if kind == "skill":
        return f"skill: {detail or name}"
    return f"tool[{name}]"


def build_actions_view(parsed: ParsedTranscript, truncate_chars: int) -> str:
    """Numbered (L<n>) actions view fed to the summarizer; n is 1-based line idx."""
    out: list[str] = []
    for idx, rec in enumerate(parsed.records, start=1):
        rtype = rec.get("type")
        msg = rec.get("message", {}) if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if rtype == "user":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        res = block.get("content")
                        out.append(f"L{idx} result: "
                                   f"{_truncate(_stringify(res), truncate_chars)}")
            text = _content_text(content)
            if text:
                out.append(f"L{idx} user: {_truncate(text, truncate_chars)}")
        elif rtype == "assistant":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        line = _tool_use_line(block, truncate_chars)
                        if line:
                            out.append(f"L{idx} {line}")
            text = _content_text(content)
            if text:
                out.append(f"L{idx} assistant: {_truncate(text, truncate_chars)}")
    return "\n".join(out)


def _stringify(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in value
        )
    return str(value)


def verify_quote(parsed: ParsedTranscript, line_start: int, line_end: int, quote: str) -> bool:
    """True iff `quote` is a substring of raw lines [line_start, line_end] (1-based)."""
    if not quote:
        return False
    n = parsed.line_count
    if line_start < 1 or line_end < line_start or line_start > n:
        return False
    end = min(line_end, n)
    window = b"\n".join(parsed.raw_lines[line_start - 1:end])
    try:
        return quote.encode("utf-8") in window
    except (UnicodeEncodeError, TypeError):
        return False


def fingerprint(raw: bytes) -> dict:
    """Change-detection fingerprint of a transcript: line count + sha256 hex."""
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return {"line_count": len(lines), "hash": hashlib.sha256(raw).hexdigest()}


def is_changed(prev_fp: dict | None, new_fp: dict) -> bool:
    """True if there is no prior fingerprint or it differs from the new one."""
    if not prev_fp:
        return True
    return (prev_fp.get("line_count") != new_fp.get("line_count")
            or prev_fp.get("hash") != new_fp.get("hash"))


def apply_grounding(summary, parsed: ParsedTranscript):
    """Return a copy of `summary` with every EvidenceRef.verified set host-side.

    Unverified items are KEPT and flagged (verified=False), never dropped (SPEC §4)."""
    data = summary.model_dump()
    for item in data.get("items", []):
        for ev in item.get("evidence", []):
            ev["verified"] = verify_quote(
                parsed, int(ev.get("line_start", 0)), int(ev.get("line_end", 0)),
                _quote_str(ev.get("quote", "")),
            )
    return summary.__class__.model_validate(data)


def _types():
    """Lazy import of the vendored BAML pydantic types."""
    from dmac_assistant.router.baml_client import types as t
    return t


def _evidence_quote(t, quote: str):
    return t.Checked(value=quote, checks={})


def _quote_str(quote) -> str:
    if isinstance(quote, dict):
        return str(quote.get("value", ""))
    if hasattr(quote, "value"):
        return str(quote.value)
    return str(quote)


def build_fallback_summary(parsed: ParsedTranscript, provenance: SummaryProvenance,
                           cfg) -> object:
    """Deterministic actions-only SessionSummary (no LLM); grounded on tool_use/text
    lines. Used when the summarizer fails so a session is never left un-remembered."""
    t = _types()
    items = []
    for idx, rec in enumerate(parsed.records, start=1):
        if len(items) >= cfg.max_items:
            break
        msg = rec.get("message", {}) if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            line = _tool_use_line(block, cfg.truncate_chars)
            if not line:
                continue
            raw_line = parsed.raw_lines[idx - 1].decode("utf-8", "replace")
            quote = (block.get("input", {}) or {}).get("command") \
                or (block.get("input", {}) or {}).get("file_path") \
                or block.get("name", "")
            quote = str(quote)[: cfg.truncate_chars]
            verified = quote in raw_line if quote else False
            items.append(t.MemoryItem(
                category=t.MemoryCategory.Artifact,
                statement=line,
                evidence=[t.EvidenceRef(line_start=idx, line_end=idx,
                                        quote=_evidence_quote(t, quote or line),
                                        verified=verified)],
                confidence=t.Confidence.Medium,
            ))
            if len(items) >= cfg.max_items:
                break
    gist = items[0].statement if items else "session with no recorded actions"
    return t.SessionSummary(
        chat_session_id=provenance.chat_session_id,
        claude_session_id=provenance.claude_session_id,
        transcript_path=provenance.transcript_path,
        transcript_line_count=parsed.line_count,
        turn_count=parsed.turn_count,
        chat_model=provenance.chat_model,
        gist=gist, items=items,
        writer="fallback_actions", summary_model="none",
        schema_version=SCHEMA_VERSION, generated_at=provenance.generated_at,
    )


def _default_summarize_fn(summarize_input):
    """Guarded BAML b.Summarize via asyncio.run (mirrors router._baml_decision)."""
    import asyncio
    from dmac_assistant.router.baml_client import b
    return asyncio.run(b.Summarize(input=summarize_input))


def summarize_transcript(raw: bytes, provenance: SummaryProvenance, cfg,
                         *, summarize_fn=None):
    """Parse → summarize (LLM, injectable) → fill host provenance → ground.
    ANY failure degrades to the deterministic actions-only fallback."""
    parsed = parse_transcript(raw)
    t = _types()
    fn = summarize_fn or _default_summarize_fn
    try:
        summarize_input = t.SummarizeInput(
            chat_session_id=provenance.chat_session_id,
            chat_model=provenance.chat_model,
            actions_view=build_actions_view(parsed, cfg.truncate_chars),
        )
        result = fn(summarize_input)
        data = result.model_dump()
        data.update(
            chat_session_id=provenance.chat_session_id,
            claude_session_id=provenance.claude_session_id,
            transcript_path=provenance.transcript_path,
            transcript_line_count=parsed.line_count,
            turn_count=parsed.turn_count,
            chat_model=provenance.chat_model,
            writer="baml_gemini",
            summary_model=cfg.summary_model,
            schema_version=SCHEMA_VERSION,
            generated_at=provenance.generated_at,
        )
        summary = t.SessionSummary.model_validate(data)
        return apply_grounding(summary, parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cc-1c: summarizer failed (%s); using actions fallback",
                       type(exc).__name__)
        return build_fallback_summary(parsed, provenance, cfg)
