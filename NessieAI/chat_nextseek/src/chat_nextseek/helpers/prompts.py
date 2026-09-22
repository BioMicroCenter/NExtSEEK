"""Prompt loading and LLM-usage logging helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path


def load_prompt(prompts_dir: str, name: str) -> str:
    """
    Load a prompt template file by name from the prompts directory with UTF-8 decoding.
    Raises if the file is missing so calling code can surface template errors early.
    """
    return (Path(prompts_dir).resolve() / name).read_text(encoding="utf-8")


def log_usage(resp, label: str):
    """
    Print token usage info for an OpenAI response.
    Supports both dict-like and model_dump()-capable objects so logging remains resilient across providers.
    Falls back gracefully when usage details are missing.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        print(f"[DEBUG][TOKENS][{label}] usage: missing")
        return

    usage_dict = None
    try:
        # pydantic-style objects expose model_dump()
        usage_dict = usage if isinstance(usage, dict) else usage.model_dump()
    except Exception:
        try:
            usage_dict = dict(usage)
        except Exception:
            pass

    prompt_tokens = None
    completion_tokens = None
    total_tokens = None

    if isinstance(usage_dict, dict):
        prompt_tokens = usage_dict.get("prompt_tokens")
        completion_tokens = usage_dict.get("completion_tokens")
        total_tokens = usage_dict.get("total_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)

    print(
        "[DEBUG][TOKENS][{label}] prompt={p} completion={c} total={t}".format(
            label=label,
            p=prompt_tokens if prompt_tokens is not None else "?",
            c=completion_tokens if completion_tokens is not None else "?",
            t=total_tokens if total_tokens is not None else "?",
        )
    )


_logger = logging.getLogger(__name__)

# One WARNING per stage per process for a prompt-log write that failed. Every failure
# used to be swallowed without a word, which is how a directory passed as the file path
# went unnoticed for as long as this function has existed. Keyed by stage, not global, so
# one caller that is known to fail cannot use up the warning another caller needs.
_failure_reported_stages: set[str] = set()


def log_prompt(log_path: str, stage: str, payload: dict, *, max_bytes: int | None = None):
    """
    Append a JSON line for the given stage to the FILE ``log_path``.
    Adds a timestamp automatically and never raises into the main flow. A write that
    fails is logged at WARNING the first time for its stage, with the path, so it cannot
    vanish. ``max_bytes`` rolls the file over to ``<log_path>.1`` (one generation kept)
    before an append that would find it at or past that size.
    """
    if not log_path:
        return
    try:
        entry = {"stage": stage, **payload, "timestamp": datetime.now().isoformat()}
        line = json.dumps(entry, default=str) + "\n"
        if max_bytes:
            try:
                if os.path.getsize(log_path) >= max_bytes:
                    os.replace(log_path, f"{log_path}.1")
            except FileNotFoundError:
                pass
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        if stage not in _failure_reported_stages:
            _failure_reported_stages.add(stage)
            _logger.warning(
                "log_prompt could not write stage %r to %s (%s: %s); further failures "
                "for this stage are not reported",
                stage, log_path, type(exc).__name__, exc,
            )


def log_llm_call(log_dir: str | None, entry: dict):
    """
    Append one JSON line per LLM call to a durable ledger so timeouts/latency/throttling
    survive the docker log ring buffer.

    Writes to ``<log_dir>/llm_calls.jsonl`` (per-run, mirrors log_prompt's LOG_DIR
    convention) and, when the ``NEXTSEEK_LLM_LOG`` env var is set, also appends to that
    rolling global path (e.g. /app/logs/llm_calls.jsonl in the bind-mounted logs dir).
    Never raises — instrumentation must not break the request path.
    """
    line = None
    try:
        record = {"ts": datetime.now().isoformat(), **entry}
        line = json.dumps(record, default=str) + "\n"
    except Exception:
        return

    targets: list[str] = []
    if log_dir:
        targets.append(os.path.join(log_dir, "llm_calls.jsonl"))
    global_path = os.getenv("NEXTSEEK_LLM_LOG")
    if global_path:
        targets.append(global_path)

    for path in targets:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
