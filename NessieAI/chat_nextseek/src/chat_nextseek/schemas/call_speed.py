"""Round 7 (T4): how fast an agent's recent first tries ran on this box, read from the LLM ledger.

``<LOG_DIR>/llm_calls.jsonl`` is appended by every gunicorn worker and survives rebuilds, so it sees every call.
Only the last 256 KiB are read (about 590 rows). Never raises: a missing or unreadable file is no samples.
"""
from __future__ import annotations

import json
import os
import statistics

TAIL_BYTES = 256 * 1024
SAMPLES = 20


def recent_first_tries(log_dir: str | None, agent: str, model: str, n: int = SAMPLES) -> list[float]:
    """Seconds of the last ``n`` first tries of ``agent`` on ``model``, oldest first.

    A first try is a row with ``attempt`` 1 and no ``fallback_from``. An answered call counts its elapsed time, a
    timed-out call the window it had. A ``deadline_capped`` row counts too: every window this rule shortens is
    written capped, so skipping those would leave the rule no samples of its own.
    """
    if not log_dir:
        return []
    try:
        with open(os.path.join(log_dir, "llm_calls.jsonl"), "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(f.tell() - TAIL_BYTES, 0)
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()
            if start:
                lines = lines[1:]  # cut mid-line
    except OSError:
        return []
    out: list[float] = []
    for line in lines:
        try:
            r = json.loads(line)
            if r["agent"] != agent or r["model"] != model or r["attempt"] != 1 or r.get("fallback_from"):
                continue
            if r["outcome"] == "ok":
                out.append(r["elapsed_ms"] / 1000)
            elif r["outcome"] == "timeout":
                out.append(float(r["timeout_seconds"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out[-n:]


def median_first_try(log_dir: str | None, agent: str, model: str, n: int = SAMPLES) -> float | None:
    """The median of ``recent_first_tries``, or None when there are fewer than ``n``."""
    xs = recent_first_tries(log_dir, agent, model, n)
    return statistics.median(xs) if len(xs) >= n else None
