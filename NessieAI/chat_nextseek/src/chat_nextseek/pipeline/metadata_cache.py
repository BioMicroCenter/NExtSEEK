"""Memoise the fetch that selection and resolve_samples both begin with.

`build_sample_digest` and `tool_resolve_samples` share three calls —
fetch_reporter_metadata, annotate_metadata_with_sampletypes,
build_metadata_summary — then diverge completely. The digest deletes the
per-record index and downloads protocol blobs; resolve filters to a pipeline's
accepted leaf types and builds a per-leaf table. Neither can be derived from
the other, so both steps stay and only the fetch is shared.

Deliberately NOT in the agent's session state: the cached blob carries every
sample's full metadata, and pipeline/agent.py writes its state to the session
on every loop iteration. This is a process cache with the same lifetime as the
nf-core schema cache — one gunicorn worker — and a miss costs exactly what the
code cost before it existed.

Callers must treat what `get` returns as read-only. It is shared, not copied;
copying it would reintroduce the cost this module exists to remove.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

#: A build touches one cohort, occasionally two when the user re-scopes. Four
#: entries covers that with room to spare; the bound exists so a long-lived
#: worker cannot accumulate cohorts indefinitely.
MAX_ENTRIES = 4

_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def cache_key(uids: Sequence[str]) -> str:
    """Order- and duplicate-insensitive key for a UID set."""
    unique = sorted({str(u) for u in uids if u})
    return hashlib.sha256("|".join(unique).encode()).hexdigest()


def get(uids: Sequence[str]) -> dict[str, Any] | None:
    """Return {"raw", "annotated", "summary"} for this UID set, or None."""
    if not [u for u in uids if u]:
        return None
    key = cache_key(uids)
    entry = _CACHE.get(key)
    if entry is None:
        return None
    _CACHE.move_to_end(key)
    return entry


def put(uids: Sequence[str], *, raw: Any, annotated: Any, summary: Any) -> None:
    """Store the shared prefix for this UID set, evicting the least-recently used."""
    if not [u for u in uids if u]:
        return
    key = cache_key(uids)
    _CACHE[key] = {"raw": raw, "annotated": annotated, "summary": summary}
    _CACHE.move_to_end(key)
    while len(_CACHE) > MAX_ENTRIES:
        _CACHE.popitem(last=False)


def clear() -> None:
    """Drop everything. Tests use this; nothing in production needs to."""
    _CACHE.clear()
