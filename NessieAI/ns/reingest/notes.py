"""Compose the parked-value block written into a sample's Notes attribute.

Values with no matching sample attribute go here rather than being dropped, so
the measurement survives until a superuser defines the attribute properly.

The block is tagged with the RUN, not just a date, so re-running a reingest
replaces its own block instead of appending a second copy. Existing text always
survives: deep_merge_metadata overwrites the whole Notes value, so composing
blind would silently destroy whatever a curator wrote.

Block boundary contract: a block runs from its ``[nfcore-reingest ...]`` tag
line through the following run of non-empty ``key=value`` lines, and ends at
the first BLANK line or the next tag line, whichever comes first. Prose a
curator writes below a blank line therefore survives a recompose untouched.
The corollary: a line written immediately under the block with NO blank line
separating it from the block is, by this contract, part of the block, and is
replaced (or dropped) the next time this run recomposes. There is no way to
tell such a line apart from a block value line, so a curator who wants prose
kept must leave a blank line before it.
"""
from __future__ import annotations

import re

_TAG = "[nfcore-reingest"


def _block(run_name: str, values: dict, today: str) -> str:
    lines = [f"{_TAG} {today} {run_name}]"]
    lines += [f"{k}={v}" for k, v in values.items()]
    lines.append("(anything you write below a blank line survives the next reingest run)")
    return "\n".join(lines)


def strip_block(text: str, run_name: str) -> str:
    """Remove this run's block, leaving every other block and all prose.

    The body of the block is matched one line at a time, and each consumed
    line must be non-empty (``[^\\n]+``, not ``[^\\n]*``) so a blank line
    terminates the block rather than being swallowed along with whatever
    prose follows it. See the module docstring for the full boundary
    contract.

    Both ends are normalised with ``.strip()`` (not just ``.rstrip()``): a
    block at the very start of ``text`` leaves the separator that used to sit
    between it and whatever follows, so trimming only the trailing side would
    leak leading blank lines into the result. Interior whitespace -- the
    contract's blank-line boundary between blocks and between a block and
    trailing prose -- is untouched.
    """
    pattern = re.compile(
        rf"\n*{re.escape(_TAG)} \S+ {re.escape(run_name)}\](?:\n(?!{re.escape(_TAG)})[^\n]+)*",
        re.MULTILINE)
    return pattern.sub("", text).strip()


def compose(existing: str, run_name: str, values: dict, today: str) -> str:
    """``existing`` with this run's block replaced (or appended if absent)."""
    kept = strip_block(existing or "", run_name)
    block = _block(run_name, values, today)
    return f"{kept}\n\n{block}" if kept else block
