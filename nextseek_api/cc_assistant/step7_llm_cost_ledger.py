"""Step 7 live-gate LLM cost ledger (Gate 3C.5).

Records actual provider token usage from ``chat_nextseek.llm_clients`` when
``STEP7_LLM_LEDGER=1``. Cost = tokens × published rates — no estimates.
Pattern from ``nextseek_api/assistant/tests/test_granular_realstack.py``.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

PRICE_TABLE_VERSION = "2026-06-granular-realstack"

# USD per token (input, output). Unpriced models → hard error.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (1.50e-6, 9.00e-6),
    "gemini-2.5-flash": (0.30e-6, 2.50e-6),
    "us.anthropic.claude-opus-4-7": (5.50e-6, 27.50e-6),
    "us.anthropic.claude-opus-4-8-20250514-v1:0": (5.50e-6, 27.50e-6),
}

_LOCK = threading.Lock()
_INSTALLED = False
_ORIG_CHAT: dict[str, Any] = {}


def ledger_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path(os.environ.get("DJANGO_BASE_DIR", "/app"))
    return root / "outputs" / "_ledger.jsonl"


def entry_usd(entry: dict[str, Any]) -> float:
    model = str(entry.get("model", ""))
    rate = PRICES.get(model)
    if rate is None:
        raise ValueError(f"UNPRICED MODEL in ledger: {model!r}")
    return (entry.get("in") or 0) * rate[0] + (entry.get("out") or 0) * rate[1]


def total_usd(entries: list[dict[str, Any]]) -> float:
    return round(sum(entry_usd(e) for e in entries), 6)


def read_ledger_lines(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or ledger_path()
    if not p.is_file():
        return []
    lines: list[dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            lines.append(obj)
    return lines


def read_delta(start: int, end: int | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    lines = read_ledger_lines(path)
    return lines[start:end]


def _record(model: str, usage: Any, path: Path) -> None:
    entry = {
        "model": str(model),
        "in": (usage or {}).get("prompt_tokens") if isinstance(usage, dict) else getattr(usage, "prompt_tokens", None),
        "out": (usage or {}).get("completion_tokens") if isinstance(usage, dict) else getattr(usage, "completion_tokens", None),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def install(*, base_dir: Path | None = None) -> None:
    """Patch LLM clients to append token usage to ``outputs/_ledger.jsonl``."""
    global _INSTALLED
    if _INSTALLED:
        return
    path = ledger_path(base_dir)
    import chat_nextseek.llm_clients as llm

    for name in ("OpenAIClient", "GeminiClient", "AnthropicClient", "BedrockClient"):
        cls = getattr(llm, name, None)
        if cls is None or name in _ORIG_CHAT:
            continue
        orig = cls.chat
        _ORIG_CHAT[name] = orig

        def make(orig_chat, ledger_path=path):
            def chat(self, *a, **k):
                resp = orig_chat(self, *a, **k)
                model = k.get("model") or (a[0] if a else getattr(self, "model", "?"))
                _record(str(model), getattr(resp, "usage", None), ledger_path)
                return resp
            return chat
        cls.chat = make(orig)
    _INSTALLED = True


def uninstall() -> None:
    global _INSTALLED
    if not _INSTALLED:
        return
    import chat_nextseek.llm_clients as llm
    for name, orig in _ORIG_CHAT.items():
        cls = getattr(llm, name, None)
        if cls is not None:
            cls.chat = orig
    _ORIG_CHAT.clear()
    _INSTALLED = False


def maybe_install_from_env() -> None:
    if os.environ.get("STEP7_LLM_LEDGER") == "1":
        install()


def budget_guard(cumulative_usd: float, cap_usd: float, *, op: str) -> None:
    if cumulative_usd > cap_usd:
        raise RuntimeError(
            f"STEP7 budget cap ${cap_usd:.2f} exceeded (${cumulative_usd:.4f}) before {op}"
        )


def build_extraction_entry(
    op: str,
    *,
    call_id: str,
    ledger_line_start: int,
    ledger_line_end: int,
    ledger_path_rel: str = "outputs/_ledger.jsonl",
    path: Path | None = None,
) -> dict[str, Any]:
    delta = read_delta(ledger_line_start, ledger_line_end, path)
    if not delta:
        raise ValueError(f"{op}: no ledger lines in [{ledger_line_start}:{ledger_line_end})")
    usd = total_usd(delta)
    models = sorted({str(e.get("model")) for e in delta})
    return {
        "op": op,
        "call_id": call_id,
        "source_system": "llm_client_ledger",
        "ledger_path": ledger_path_rel,
        "ledger_line_start": ledger_line_start,
        "ledger_line_end": ledger_line_end,
        "model": models[0] if len(models) == 1 else models,
        "tokens_in": sum(e.get("in") or 0 for e in delta),
        "tokens_out": sum(e.get("out") or 0 for e in delta),
        "usd": usd,
        "price_table_version": PRICE_TABLE_VERSION,
    }
