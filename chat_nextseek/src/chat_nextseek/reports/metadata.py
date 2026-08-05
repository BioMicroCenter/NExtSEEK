"""Reporter metadata helpers: sample-type annotation, metadata fetch,
summary-table builders, and lineage / DEG filters.

Moved out of ``helpers.py`` during Phase 2 of the src/ restructure.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..helpers.tools.nextseek_api import tool_nextseek_api_request


def annotate_metadata_with_sampletypes(config, metadata: dict) -> dict:
    """
    Enrich a metadata payload with human-friendly sample type names/descriptions from the cached lookup.
    Traverses entries safely and returns the original structure when shape mismatches occur.
    Keeps the function tolerant of missing fields so reporter flows do not fail on partial data.
    """
    _SAMPLETYPE_LOOKUP: dict[str, dict[str, str | None]] = {
        row.get("SampleType"): {"name": row.get("Name"), "description": row.get("Description")}
        for row in (config.MIN_SAMPLETYPES or [])
        if isinstance(row, dict) and row.get("SampleType")
    }
    try:
        data_block = metadata.get("data", {})
        entries = data_block.get("data", [])
        if not isinstance(entries, list):
            return metadata
        new_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                new_entries.append(entry)
                continue
            code = entry.get("sample_type")
            info = _SAMPLETYPE_LOOKUP.get(code, {})
            enriched = dict(entry)
            if info:
                enriched["sample_type_name"] = info.get("name")
                enriched["sample_type_description"] = info.get("description")
            new_entries.append(enriched)
        new_data_block = dict(data_block)
        new_data_block["data"] = new_entries
        new_metadata = dict(metadata)
        new_metadata["data"] = new_data_block
        return new_metadata
    except Exception as e:
        print("[DEBUG][REPORTER_META] Failed to annotate sampletypes:", repr(e))
        return metadata


def fetch_reporter_metadata(config, uids: list[str]) -> dict:
    """
    Fetch full sample metadata (and lineage) for provided UIDs using the admin retrieve endpoint.
    Returns the raw request result so callers can inspect ok/status and enrich downstream reports.
    Logs debug hints but tolerates failures by returning an error payload instead of raising.
    """
    if not uids:
        return {"ok": False, "error": "No UIDs provided for metadata retrieval"}

    print("[DEBUG][REPORTER_META] Fetching metadata for UIDs:", uids)
    try:
        result = tool_nextseek_api_request(
            config,
            endpoint="/nextseek_api/admin/samples/retrieve/",
            method="POST",
            requestBody={"identifiers": uids},
            queryParameters={},
        )
        print("[DEBUG][REPORTER_META] Metadata fetch ok:", result.get("ok"), "status:", result.get("status_code"))
        return result
    except Exception as e:
        print("[DEBUG][REPORTER_META] Metadata fetch exception:", repr(e))
        return {"ok": False, "error": repr(e)}


def _scalar_for_summary(value: Any) -> str | None:
    """Coerce a metadata value to a short string for summary display.
    Returns None for empties so they don't count as populated."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return json.dumps(value, default=str)[:120]
    if isinstance(value, dict):
        if not value:
            return None
        return json.dumps(value, default=str)[:120]
    return str(value)[:120]


def _entries_from_metadata(md: Any) -> list:
    """Find the list of sample-type blocks regardless of one or two levels of wrapping.

    tool_nextseek_api_request returns {"ok":..., "data": <body>} where body is
    {"total_samples":..., "data":[{sample_type, samples:[...]}]}.
    """
    if not isinstance(md, dict):
        return []
    inner = md.get("data")
    if isinstance(inner, dict):
        entries = inner.get("data")
        if isinstance(entries, list):
            return entries
    if isinstance(inner, list):
        return inner
    return []


def build_metadata_summary(metadata_map: dict | None) -> dict[str, Any]:
    """Walk per-UID metadata bundles and produce a sample-type-keyed summary
    of distinct field names + value-variation hints.

    Lineage edges are computed by following each sample's `Parent` metadata
    field up the chain (NExtSEEK's lineage representation). A `uid_index`
    is also returned to support post-hoc lineage filtering.

    Output shape:
        {
          "by_sample_type": {
            "<sample_type>": {
              "n_samples": int,
              "fields": {<name>: {n_populated, n_distinct, examples}}
            }
          },
          "lineage_edges": ["<parent_st> → <child_st>", ...],
          "_uid_index": {uid: {sample_type, parent_uid, metadata}}   # used by filter helpers
        }
    """
    by_st: dict[str, dict] = {}
    uid_index: dict[str, dict[str, Any]] = {}

    def _record(sample_type: str, sample_meta: dict | None) -> None:
        st = by_st.setdefault(sample_type, {"n_samples": 0, "fields": {}})
        st["n_samples"] += 1
        if not isinstance(sample_meta, dict):
            return
        for k, v in sample_meta.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue
            scalar = _scalar_for_summary(v)
            field = st["fields"].setdefault(k, {"n_populated": 0, "_seen": set(), "examples": []})
            if scalar is not None:
                field["n_populated"] += 1
                if scalar not in field["_seen"]:
                    field["_seen"].add(scalar)
                    if len(field["examples"]) < 3:
                        field["examples"].append(scalar)

    # Pass 1: collect every sample's (sample_type, parent_uid, metadata)
    for md in (metadata_map or {}).values():
        for st_block in _entries_from_metadata(md):
            if not isinstance(st_block, dict):
                continue
            top_st = st_block.get("sample_type") or "unknown"
            for sample in st_block.get("samples") or []:
                if not isinstance(sample, dict):
                    continue
                meta = sample.get("metadata") or {}
                uid = (
                    (isinstance(meta, dict) and meta.get("UID"))
                    or sample.get("uuid")
                    or sample.get("uid")
                )
                parent_uid = meta.get("Parent") if isinstance(meta, dict) else None
                if uid:
                    uid_index[uid] = {
                        "sample_type": top_st,
                        "parent_uid": parent_uid,
                        "metadata": meta,
                    }
                _record(top_st, meta)
                # Also descend into any `children` array (legacy / other
                # responses that nest lineage there)
                for related in sample.get("children") or []:
                    if not isinstance(related, dict):
                        continue
                    cmeta = related.get("metadata") or {}
                    cst = (
                        related.get("sample_type")
                        or related.get("sampletype")
                        or related.get("type")
                        or "unknown"
                    )
                    cuid = (
                        (isinstance(cmeta, dict) and cmeta.get("UID"))
                        or related.get("uuid")
                    )
                    cparent = cmeta.get("Parent") if isinstance(cmeta, dict) else None
                    if cuid:
                        uid_index.setdefault(cuid, {
                            "sample_type": cst,
                            "parent_uid": cparent,
                            "metadata": cmeta,
                        })
                    _record(cst, cmeta)

    # Pass 2: derive lineage edges via Parent links — child.parent_uid → parent_st → child_st
    lineage_edges: set[str] = set()
    for uid, entry in uid_index.items():
        parent_uid = entry.get("parent_uid")
        if not parent_uid:
            continue
        parent = uid_index.get(parent_uid)
        if not parent:
            continue
        parent_st = parent.get("sample_type")
        child_st = entry.get("sample_type")
        if parent_st and child_st and parent_st != child_st:
            lineage_edges.add(f"{parent_st} → {child_st}")

    # Finalize: drop the internal _seen sets and compute n_distinct
    for st_data in by_st.values():
        for fname, fdata in st_data["fields"].items():
            fdata["n_distinct"] = len(fdata.pop("_seen"))
    return {
        "by_sample_type": by_st,
        "lineage_edges": sorted(lineage_edges),
        "_uid_index": uid_index,
    }


# A.ALN is here because bamtofastq resolves cohorts to alignment records rather than
# raw reads. Without it, an A.ALN cohort gets filtered out of the summary entirely and
# the pipeline agent is offered no grouping fields for the very samples it is building
# rows from. It is a sequencing artifact, so it belongs on the same lineage branch.
_SEQUENCING_SAMPLE_TYPE_HINTS = ("D.SEQ", "D.SEQUENCING", "SEQ", "A.ALN")


def _is_sequencing_type(sample_type: str) -> bool:
    """Heuristic: which NExtSEEK sample types represent SEQUENCING data
    (where LibraryStrategy / accessions live)? D.SEQ raw reads plus A.ALN
    alignments. Extend the hint list if other sequencing data types appear."""
    if not isinstance(sample_type, str):
        return False
    st = sample_type.upper()
    return any(st == h or st.startswith(f"{h}.") for h in _SEQUENCING_SAMPLE_TYPE_HINTS)


def filter_summary_to_sequencing_lineage(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a slimmer summary with only the sequencing sample types and their
    LINEAGE ANCESTORS (samples that appear upstream via the `Parent` chain).

    For pipeline selection, only D.SEQ + ancestors are useful — biology fields
    live on the upstream samples (TIS, NHP, RNA Sample, etc.). Downstream
    artifacts (A.SCXP, A.FLOW, D.IMG, BAC, etc.) and parallel sample types
    contribute noise.

    If no sequencing sample types are present, returns the original summary
    unchanged (defensive fallback).
    """
    by_st = (summary or {}).get("by_sample_type") or {}
    uid_index = (summary or {}).get("_uid_index") or {}

    seq_types = {st for st in by_st.keys() if _is_sequencing_type(st)}
    if not seq_types:
        return summary

    # Walk Parent chains up from every sequencing sample to collect ancestor types
    keep_types: set[str] = set(seq_types)
    for uid, entry in uid_index.items():
        if entry.get("sample_type") not in seq_types:
            continue
        cursor = entry.get("parent_uid")
        seen: set[str] = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            parent = uid_index.get(cursor)
            if not parent:
                break
            keep_types.add(parent.get("sample_type"))
            cursor = parent.get("parent_uid")

    filtered_by_st = {
        st: data for st, data in by_st.items() if st in keep_types
    }
    # Keep only edges entirely within the kept set
    filtered_edges = [
        e for e in (summary.get("lineage_edges") or [])
        if all(side.strip() in keep_types for side in e.split("→"))
    ]
    return {
        "by_sample_type": filtered_by_st,
        "lineage_edges": filtered_edges,
        "_uid_index": uid_index,
    }


def build_accession_metadata_lookup(metadata_map: dict | None) -> dict[str, dict[str, Any]]:
    """For each sample, walk its lineage UPWARD via the `Parent` metadata
    field, flattening biology fields from upstream samples (TIS, NHP, PAV…)
    onto the leaf sample's flat metadata. Index by every accession found.

    Returns: {accession: flat_metadata_dict}.

    Flatten precedence: leaf sample's own metadata wins; upstream parents
    fill in missing keys but never overwrite. This way `UID` / `Strandedness`
    on D.SEQ stay D.SEQ's, while biology fields like `Treatment1` (which
    live on TIS or NHP upstream) get pulled in.

    Also walks legacy `children` arrays for older API responses that nest
    lineage there.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(metadata_map, dict):
        return out

    # Build a uid → (sample_dict, parent_uid) index across the entire bundle
    uid_to_sample: dict[str, dict[str, Any]] = {}

    def _walk_collect(sample: Any) -> None:
        if not isinstance(sample, dict):
            return
        meta = sample.get("metadata")
        uid = (
            (isinstance(meta, dict) and meta.get("UID"))
            or sample.get("uuid")
            or sample.get("uid")
        )
        if uid:
            uid_to_sample[uid] = sample
        for child in sample.get("children") or []:
            _walk_collect(child)

    for md in metadata_map.values():
        for st_block in _entries_from_metadata(md):
            if not isinstance(st_block, dict):
                continue
            for sample in st_block.get("samples") or []:
                _walk_collect(sample)

    def _flatten_with_parents(sample: dict) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        seen_uids: set[str] = set()
        cursor: dict[str, Any] | None = sample
        while isinstance(cursor, dict):
            meta = cursor.get("metadata") or {}
            cur_uid = meta.get("UID") if isinstance(meta, dict) else None
            if cur_uid and cur_uid in seen_uids:
                break  # cycle guard
            if cur_uid:
                seen_uids.add(cur_uid)
            if isinstance(meta, dict):
                for k, v in meta.items():
                    if isinstance(k, str) and k not in flat and v not in (None, ""):
                        flat[k] = v
            # Walk legacy `children` (some bundles nest lineage there)
            for child in cursor.get("children") or []:
                if isinstance(child, dict):
                    cmeta = child.get("metadata") or {}
                    if isinstance(cmeta, dict):
                        for k, v in cmeta.items():
                            if isinstance(k, str) and k not in flat and v not in (None, ""):
                                flat[k] = v
            # Walk upward via Parent UID
            parent_uid = (
                meta.get("Parent") if isinstance(meta, dict) else None
            )
            if not parent_uid or parent_uid in seen_uids:
                break
            cursor = uid_to_sample.get(parent_uid)
        return flat

    for sample in uid_to_sample.values():
        flat = _flatten_with_parents(sample)
        accessions: set[str] = set()
        for v in flat.values():
            if isinstance(v, str):
                for m in re.findall(
                    r"\b(?:SRR|SRX|SRP|SRS|ERR|ERX|ERP|ERS|DRR|DRX|DRP|DRS|GSE|GSM)\d+\b",
                    v,
                ):
                    accessions.add(m)
        for acc in accessions:
            out.setdefault(acc, flat)
    return out


def filter_summary_for_deg(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a slimmer summary keeping only fields that are good DEG candidates.

    Drops:
    - Fields with no populated values (n_distinct == 0).
    - Uniform fields (n_distinct == 1) — always useless for contrasts, regardless
      of sample count.
    - Per-sample-unique fields (n_distinct == n_samples) — only when n_samples >= 3,
      because with very small n every field tends to look unique.
    """
    filtered: dict[str, dict] = {}
    for st, st_data in (summary or {}).get("by_sample_type", {}).items():
        n = st_data.get("n_samples", 0)
        keep: dict[str, dict] = {}
        for fname, fdata in (st_data.get("fields") or {}).items():
            nd = fdata.get("n_distinct", 0)
            if nd <= 1:
                # No variation → skip
                continue
            if n >= 3 and nd == n:
                # Per-sample unique → likely an ID, not a contrast variable
                continue
            keep[fname] = fdata
        if keep:
            filtered[st] = {"n_samples": n, "fields": keep}
    return {
        "by_sample_type": filtered,
        "lineage_edges": (summary or {}).get("lineage_edges") or [],
    }
