"""Compose the parked-value block written into a sample's Notes attribute.

Values with no matching sample attribute go here rather than being dropped, so
the measurement survives until a superuser defines the attribute properly.

The block is tagged with the RUN, not just a date, so re-running a reingest
replaces its own block instead of appending a second copy. Existing text always
survives: deep_merge_metadata overwrites the whole Notes value, so composing
blind would silently destroy whatever a curator wrote.

Block boundary contract: a block runs from its ``[nfcore-reingest ...]`` tag
line through its body (``key=value`` lines) down to its TERMINATOR line --
the human-readable sentence ``_block`` always emits last. ``strip_block``
stops AT AND INCLUDING that terminator line; anything below it, blank line or
not, is prose and is never consumed. That is the fix for a real data-loss
defect: an earlier version of this contract ended the block at the first
blank line, which meant a curator line typed immediately under the block --
one missing Enter keypress -- was indistinguishable from a block body line
and was silently dropped on the next recompose. The terminator line removes
that ambiguity: it is not a value a curator would ever type as their own
content, so its presence unambiguously marks "block ends here."

There is no fallback for a block with no terminator. ``_block`` has emitted
one on every block since the very first line of this module, so a tag line
for this run with no valid terminator anywhere after it cannot be genuine
pre-terminator data -- it can only mean the terminator sentence was typo'd,
edited, deleted, or line-wrapped, most plausibly by a curator editing near
the block. ``strip_block`` cannot safely guess where such a block ends, so
it does not try: an unterminated tag line is not recognised as a block at
all, and nothing under it is touched. An earlier version of this module did
fall back to a blank-line-bounded guess in that case, which carried the same
data-loss defect the terminator itself was added to fix: any non-blank line
sitting right under the tag -- a curator's own line, one missing blank
line -- was consumed as if it were block content. That fallback is gone.
The cost is a harmless, bounded residual: a block that loses its terminator
is left in place, untouched, on every future recompose (verified in
``NessieAI/tests/ns/reingest/test_notes.py``) -- it never grows past the one
leftover occurrence, and it is never itself a source of data loss, only of
one stale tag line a human should clean up.

Deliberate, documented non-issue: ``strip_block``'s ``.strip()`` trims
leading whitespace off the very first line of ``text`` along with the block
it removes. A Notes value like ``"   indented curator note\\n\\n[block]"``
recomposes to ``"indented curator note\\n\\n[block]"`` -- the leading spaces
are gone, but no *content* is lost, so the guard does not (and should not)
treat this as clobbering. Recorded here so it is not mistaken for a defect
when spotted in a diff of production Notes.
"""
from __future__ import annotations

import re

_TAG = "[nfcore-reingest"
_TERMINATOR = "(anything written below this line is preserved by every future reingest run)"


def _block(run_name: str, values: dict, today: str) -> str:
    lines = [f"{_TAG} {today} {run_name}]"]
    lines += [f"{k}={v}" for k, v in values.items()]
    lines.append(_TERMINATOR)
    return "\n".join(lines)


def strip_block(text: str, run_name: str) -> str:
    """Remove this run's block, leaving every other block and all prose.

    Matches only the terminated form: the tag line, then the body matched
    lazily (``*?``) up to and including the literal terminator line. Lazy
    matching means the search stops at the FIRST terminator it finds, so a
    block is never over-consumed past its own boundary.

    If no terminator is found for this run's tag anywhere in ``text``, the
    tag line is left alone -- not recognised as a block, nothing under it
    consumed -- rather than guessed at via a blank-line-bounded fallback.
    See the module docstring for why that fallback was removed and what
    residual it trades for (a stale, but harmless and bounded, leftover
    tag line instead of a chance of losing real content).

    Both ends are normalised with ``.strip()`` (not just ``.rstrip()``): a
    block at the very start of ``text`` leaves the separator that used to sit
    between it and whatever follows, so trimming only the trailing side would
    leak leading blank lines into the result. Interior whitespace -- the
    contract's boundary between blocks and between a block and trailing
    prose -- is untouched (see the module docstring for the one deliberate
    exception: leading whitespace on the very first line).
    """
    tag_open = rf"{re.escape(_TAG)} \S+ {re.escape(run_name)}\]"

    terminated = re.compile(
        rf"\n*{tag_open}(?:\n(?!{re.escape(_TAG)})[^\n]+)*?\n{re.escape(_TERMINATOR)}",
        re.MULTILINE)
    stripped, hits = terminated.subn("", text)
    if hits:
        return stripped.strip()

    # No valid terminator for this run's tag: nothing is licensed to
    # disappear, so consume nothing (see module docstring and this
    # function's docstring for why there is deliberately no fallback here).
    return text


def compose(existing: str, run_name: str, values: dict, today: str) -> str:
    """``existing`` with this run's block replaced (or appended if absent)."""
    kept = strip_block(existing or "", run_name)
    block = _block(run_name, values, today)
    return f"{kept}\n\n{block}" if kept else block
