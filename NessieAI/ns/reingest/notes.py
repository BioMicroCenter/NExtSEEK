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

Blocks written before the terminator existed have none. For those,
``strip_block`` falls back to the original blank-line-bounded contract: the
block runs through the following run of non-empty lines and ends at the
first blank line or the next tag line, whichever comes first. The terminated
form is tried first; the blank-line fallback only applies when no terminator
is found for that run's tag.

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

    Tries the terminated form first: the tag line, then the body matched
    lazily (``*?``) up to and including the literal terminator line. Lazy
    matching means the search stops at the FIRST terminator it finds, so a
    block is never over-consumed past its own boundary.

    If no terminator is found for this run's tag anywhere in ``text`` (an
    old-style block, written before the terminator existed), falls back to
    the original contract: the tag line through the following run of
    non-empty lines, ending at the first blank line or the next tag line.
    Each consumed line there must be non-empty (``[^\\n]+``, not
    ``[^\\n]*``) so a blank line terminates the block rather than being
    swallowed along with whatever prose follows it.

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

    # Fallback for a block with no terminator at all (pre-fix data).
    untagged = re.compile(
        rf"\n*{tag_open}(?:\n(?!{re.escape(_TAG)})[^\n]+)*",
        re.MULTILINE)
    return untagged.sub("", text).strip()


def compose(existing: str, run_name: str, values: dict, today: str) -> str:
    """``existing`` with this run's block replaced (or appended if absent)."""
    kept = strip_block(existing or "", run_name)
    block = _block(run_name, values, today)
    return f"{kept}\n\n{block}" if kept else block
