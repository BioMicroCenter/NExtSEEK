"""Prompt loading and LLM-usage logging helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
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


def log_prompt(log_path: str, stage: str, payload: dict):
    """
    Append a JSON line for the given stage to the prompts log if a path is configured in session state.
    Adds a timestamp automatically and swallows IO errors to avoid interrupting the main flow.
    """
    entry = {"stage": stage, **payload, "timestamp": datetime.now().isoformat()}
    if not log_path:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
