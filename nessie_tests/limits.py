"""Graph LIMIT sentinels, in a dependency-free module.

These live here rather than in ``evaluate.py`` because ``consistency.py`` needs them
too, and importing ``evaluate`` drags in ``e2e.criteria`` and openpyxl for what is a
tuple of two integers.

A sentinel comparison is only ever a GUESS that a result was capped: it infers
truncation from the row count happening to equal a known limit. It went stale the
moment the graph limit moved from 250 to 5000, which left the anti-truncation guard
dead while still looking like a guard.

The real signal is ``graph_result.truncated``, which the Neo4j tool now sets by
comparing the returned row count against the query's own trailing LIMIT. Prefer it.
These values remain only as a fallback for results produced before that flag existed.
"""
from __future__ import annotations

# Every graph LIMIT the corpus has run under. 250 was the old default; 5000 is current.
GRAPH_LIMIT_SENTINELS: tuple[int, ...] = (250, 5000)
