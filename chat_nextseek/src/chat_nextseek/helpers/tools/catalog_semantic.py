"""Semantic catalog retrieval: SemanticIndex + RRF fusion + dynamic cutoff.

Used by `shortlist_catalog` to supplement the existing lexical rapidfuzz pass
with semantic embedding-based retrieval and graceful fallback.
"""
from __future__ import annotations
