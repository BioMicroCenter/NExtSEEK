"""Marked-block parsing and rendering for generated text surfaces."""
from __future__ import annotations


class MarkerError(ValueError):
    """Raised when marker structure is invalid."""


def validate_markers(text: str, begin_marker: str, end_marker: str) -> None:
    """Ensure exactly one well-ordered, non-nested marker pair exists."""
    begin_count = text.count(begin_marker)
    end_count = text.count(end_marker)
    if begin_count == 0 or end_count == 0:
        raise MarkerError(
            f"missing marker: BEGIN count={begin_count}, END count={end_count}"
        )
    if begin_count > 1 or end_count > 1:
        raise MarkerError(
            f"duplicate marker: BEGIN count={begin_count}, END count={end_count}"
        )

    begin_idx = text.index(begin_marker)
    end_idx = text.index(end_marker)
    if begin_idx >= end_idx:
        raise MarkerError(
            f"inverted marker order: BEGIN at {begin_idx}, END at {end_idx}"
        )

    inner = text[begin_idx + len(begin_marker) : end_idx]
    if begin_marker in inner or end_marker in inner:
        raise MarkerError("nested marker detected inside marked block")


def render_marked_file(
    original: str,
    begin_marker: str,
    end_marker: str,
    block_content: str,
) -> str:
    """Return text with only the marked region replaced."""
    validate_markers(original, begin_marker, end_marker)
    begin_idx = original.index(begin_marker)
    end_idx = original.index(end_marker)
    prefix = original[: begin_idx + len(begin_marker)]
    suffix = original[end_idx:]
    if block_content and not block_content.endswith("\n"):
        block_content = block_content + "\n"
    return prefix + block_content + suffix
