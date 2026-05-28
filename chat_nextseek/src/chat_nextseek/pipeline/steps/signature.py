"""Group candidate sequencing leaves by their metadata 'signature' — the subset
of low-cardinality descriptor fields — so the sanity step can classify a handful
of groups instead of hundreds of individual leaves.

Cardinality-driven on purpose: no field names are hardcoded. A field joins the
signature only when its values REPEAT across leaves (few distinct values,
populated on >= 2 leaves); per-leaf identity fields (UID, FASTQ file names,
dates, checksums) are high-cardinality and fall out automatically. Leaves of the
same experiment kind therefore collapse into one group, while a mixed cohort
(e.g. RNA-seq + WGS under the same mice) splits on the field that actually
differs."""
from __future__ import annotations

from typing import Any


def _scalar(value: Any) -> str | None:
    """Coerce a metadata value to a comparable categorical string, or None when
    it isn't a scalar descriptor (lists/dicts/empties are skipped)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def group_leaves_by_signature(
    leaves: list[dict],
    *,
    max_distinct: int = 16,
) -> list[dict[str, Any]]:
    """Cluster leaf records by descriptor-field signature.

    A field is a descriptor (part of the signature) when, across the candidate
    leaves, it is populated on at least 2 leaves and has few distinct values
    (<= ``min(max_distinct, n_leaves // 2)``). Identity fields are excluded by
    that cardinality cap automatically.

    Each leaf is ``{"uid", "metadata": {...}, ...}`` (as emitted by
    ``enumerate_lineage_leaves``). Returns a list of group dicts:
    ``{"group_id", "signature": {field: value}, "leaf_uids": [...], "n_leaves": int}``,
    ordered largest-first (stable for ties).
    """
    leaves = [lf for lf in leaves if isinstance(lf, dict)]
    n = len(leaves)
    if n == 0:
        return []

    # Per-field distinct scalar values + populated count, across all leaves.
    distinct: dict[str, set[str]] = {}
    populated: dict[str, int] = {}
    per_leaf_scalars: list[dict[str, str]] = []
    for lf in leaves:
        md = lf.get("metadata") or {}
        scalars: dict[str, str] = {}
        if isinstance(md, dict):
            for k, v in md.items():
                if not isinstance(k, str) or k.startswith("_"):
                    continue
                s = _scalar(v)
                if s is None:
                    continue
                scalars[k] = s
                distinct.setdefault(k, set()).add(s)
                populated[k] = populated.get(k, 0) + 1
        per_leaf_scalars.append(scalars)

    cap = min(max_distinct, max(1, n // 2))
    descriptor_fields = {
        field
        for field, values in distinct.items()
        if populated.get(field, 0) >= 2 and 1 <= len(values) <= cap
    }

    # Cluster by signature key (sorted descriptor field/value pairs).
    order: list[tuple] = []
    groups: dict[tuple, dict[str, Any]] = {}
    for lf, scalars in zip(leaves, per_leaf_scalars):
        sig_items = tuple(sorted(
            (f, scalars[f]) for f in descriptor_fields if f in scalars
        ))
        group = groups.get(sig_items)
        if group is None:
            order.append(sig_items)
            group = {"signature": dict(sig_items), "leaf_uids": [], "n_leaves": 0}
            groups[sig_items] = group
        uid = lf.get("uid")
        if uid:
            group["leaf_uids"].append(uid)
        group["n_leaves"] += 1

    ordered = [groups[key] for key in order]
    ordered.sort(key=lambda g: -g["n_leaves"])  # stable: ties keep first-seen order
    for i, group in enumerate(ordered, 1):
        group["group_id"] = f"g{i}"
    return ordered
