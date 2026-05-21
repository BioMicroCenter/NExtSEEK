"""Semantic catalog retrieval: SemanticIndex + RRF fusion + dynamic cutoff.

Used by `shortlist_catalog` to supplement the existing lexical rapidfuzz pass
with semantic embedding-based retrieval and graceful fallback.
"""
from __future__ import annotations

from typing import Any


def dynamic_cutoff(
    scored: list[tuple[Any, float]],
    *,
    ratio: float = 0.7,
    min_k: int = 10,
    max_k: int = 80,
) -> list[Any]:
    """Cut a sorted-desc scored list to a dynamic top-K.

    Keeps every item with `score >= ratio * top_score`, then enforces
    `min_k` (always at least N items if available) and `max_k` (never
    more than N). Inputs MUST be pre-sorted by descending score.

    With `top_score == 0` the ratio rule is meaningless; fall through
    to `min_k`.
    """
    if not scored:
        return []
    top_score = scored[0][1]
    if top_score > 0:
        threshold = ratio * top_score
        kept = [item for item, score in scored if score >= threshold]
    else:
        kept = []
    if len(kept) < min_k:
        kept = [item for item, _ in scored[:min_k]]
    if len(kept) > max_k:
        kept = kept[:max_k]
    return kept
