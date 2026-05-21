"""Unit tests for catalog_semantic: SemanticIndex, fuse_rrf, dynamic_cutoff."""
from chat_nextseek.helpers.tools.catalog_semantic import dynamic_cutoff


def test_dynamic_cutoff_respects_ratio():
    """Items below 0.7 * top_score are dropped."""
    scored = [("a", 1.0), ("b", 0.8), ("c", 0.6), ("d", 0.5)]
    # top=1.0; ratio=0.7 → threshold=0.7; b(0.8)>=0.7 stays, c/d drop
    assert dynamic_cutoff(scored, ratio=0.7, min_k=1, max_k=10) == ["a", "b"]


def test_dynamic_cutoff_floor_min_k():
    """If ratio filter keeps fewer than min_k, expand to min_k."""
    scored = [("a", 1.0), ("b", 0.1), ("c", 0.05), ("d", 0.01)]
    # ratio=0.7 alone keeps only "a"; min_k=3 → expand
    assert dynamic_cutoff(scored, ratio=0.7, min_k=3, max_k=10) == ["a", "b", "c"]


def test_dynamic_cutoff_ceiling_max_k():
    """If ratio filter keeps more than max_k, cap at max_k."""
    scored = [(c, 0.95) for c in "abcdefghij"]  # all close to top
    assert dynamic_cutoff(scored, ratio=0.7, min_k=1, max_k=3) == ["a", "b", "c"]


def test_dynamic_cutoff_empty():
    assert dynamic_cutoff([], ratio=0.7, min_k=1, max_k=10) == []


def test_dynamic_cutoff_zero_top_score():
    """All scores 0 → fall back to min_k items (no ratio meaning)."""
    scored = [("a", 0.0), ("b", 0.0), ("c", 0.0)]
    assert dynamic_cutoff(scored, ratio=0.7, min_k=2, max_k=10) == ["a", "b"]
