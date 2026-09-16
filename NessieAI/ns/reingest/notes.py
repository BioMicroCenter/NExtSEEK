"""Compose the parked-value block written into a sample's Notes attribute.

Values with no matching sample attribute go here rather than being dropped, so
the measurement survives until a superuser defines the attribute properly.

The block is tagged with the RUN, not just a date, so re-running a reingest
replaces its own block instead of appending a second copy. Existing text always
survives: deep_merge_metadata overwrites the whole Notes value, so composing
blind would silently destroy whatever a curator wrote.
"""
from __future__ import annotations

import re

_TAG = "[nfcore-reingest"


def _block(run_name: str, values: dict, today: str) -> str:
    lines = [f"{_TAG} {today} {run_name}]"]
    lines += [f"{k}={v}" for k, v in values.items()]
    return "\n".join(lines)


def strip_block(text: str, run_name: str) -> str:
    """Remove this run's block, leaving every other block and all prose."""
    pattern = re.compile(
        rf"\n*{re.escape(_TAG)} \S+ {re.escape(run_name)}\](?:\n(?!{re.escape(_TAG)})[^\n]*)*",
        re.MULTILINE)
    return pattern.sub("", text).rstrip()


def compose(existing: str, run_name: str, values: dict, today: str) -> str:
    """``existing`` with this run's block replaced (or appended if absent)."""
    kept = strip_block(existing or "", run_name)
    block = _block(run_name, values, today)
    return f"{kept}\n\n{block}" if kept else block
