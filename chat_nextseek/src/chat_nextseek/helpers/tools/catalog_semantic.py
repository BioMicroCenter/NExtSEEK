"""Semantic catalog retrieval: SemanticIndex + RRF fusion + dynamic cutoff.

Used by `shortlist_catalog` to supplement the existing lexical rapidfuzz pass
with semantic embedding-based retrieval and graceful fallback.
"""
from __future__ import annotations

from typing import Any, Callable


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


def fuse_rrf(
    *ranked_lists: list[tuple[Any, float, int]],
    id_fn: Callable[[Any], str],
    k: int = 60,
) -> list[tuple[Any, float]]:
    """Reciprocal Rank Fusion across any number of ranked lists.

    Each input is a list of (item, score, rank_1_indexed). Items are
    keyed by `id_fn(item)` so the same logical item across rankers
    accumulates fused score. Output is sorted descending by fused
    score; only the FIRST observed item-object for each id is kept
    in the output.

    `k=60` is the canonical RRF smoothing constant (Cormack et al. 2009):
    high enough that rank-1 in one ranker does not dominate rank-2
    in both rankers.
    """
    scores: dict[str, float] = {}
    items: dict[str, Any] = {}
    for ranking in ranked_lists:
        for item, _score, rank in ranking:
            key = id_fn(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            items.setdefault(key, item)
    ordered_keys = sorted(scores.keys(), key=lambda key: -scores[key])
    return [(items[key], scores[key]) for key in ordered_keys]


def _join_nonempty(parts: list[str]) -> str:
    return " | ".join(p for p in parts if p)


def doc_sampletype(st: dict) -> str:
    """Embeddable text for a sampletype catalog item."""
    return _join_nonempty([
        st.get("SampleType") or "",
        st.get("Name") or "",
        st.get("Tags") or "",
        st.get("Description") or "",
    ])


def doc_assay(assay: dict) -> str:
    """Embeddable text for an assay catalog item. Honors both PascalCase
    and snake_case alternative-names keys (catalog has historically used both).
    """
    alt = assay.get("Alternative Assay Names") or assay.get("alternative_assay_names") or ""
    return _join_nonempty([
        assay.get("Name") or "",
        alt,
        assay.get("Tags") or "",
        assay.get("Description") or "",
    ])


def doc_endpoint(ep: dict) -> str:
    """Embeddable text for an API endpoint catalog item."""
    tags = ep.get("tags") or []
    tags_str = " ".join(tags) if isinstance(tags, list) else str(tags)
    return _join_nonempty([
        ep.get("path") or "",
        ep.get("summary") or "",
        ep.get("description") or "",
        tags_str,
    ])
