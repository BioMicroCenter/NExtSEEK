"""Lineage leaf enumeration for sample bundle traversal. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations


def enumerate_lineage_leaves(
    metadata_bundle: dict,
    *,
    accepted_types: list[str],
) -> list[dict[str, str]]:
    """Walk a NExtSEEK metadata bundle and return every sample whose
    ``sample_type`` is in ``accepted_types``.

    Returns a list of ``{uid, sample_type, assay, source_uid}`` dicts. ``source_uid``
    is the top-level (root) sample UID this leaf was reached from, used by the
    sanity step to report "X source UIDs have zero matching leaves".

    Empty ``accepted_types`` short-circuits to ``[]`` — used by pipelines with
    ``samplesheet_input_kind="accession"`` (e.g. fetchngs) where lineage isn't
    walked at all.

    The bundle shape mirrors the reporter API response:
        {"data": [
            {"sample_type": "NHP", "samples": [
                {"uuid": "NHP-1", "metadata": {...}, "children": [
                    {"uuid": "TIS-1", "metadata": {...}, "children": [
                        {"uuid": "D.SEQ-1", "metadata": {"UID": "...", "assay_name": "RNA-seq"}},
                    ]},
                ]},
            ]},
        ]}

    Assay value is read from ``metadata["assay_name"]`` with fallbacks to
    ``"assay"`` and ``"AssayName"``; missing assay defaults to "".
    """
    accepted = set(accepted_types or [])
    if not accepted:
        return []

    out: list[dict[str, str]] = []

    def _walk(sample: dict, source_uid: str, leaf_sample_type: str | None) -> None:
        if not isinstance(sample, dict):
            return
        uid = (sample.get("metadata") or {}).get("UID") or sample.get("uuid") or ""
        st = leaf_sample_type or ""
        # Only emit when sample_type is in accepted set.
        if st in accepted and uid:
            md = sample.get("metadata") or {}
            assay = (
                md.get("assay_name")
                or md.get("assay")
                or md.get("AssayName")
                or ""
            )
            out.append({
                "uid": str(uid),
                "sample_type": st,
                "assay": str(assay),
                "source_uid": source_uid,
                "metadata": md if isinstance(md, dict) else {},
            })
        for child in sample.get("children") or []:
            # Children's sample_type comes from the inner-block annotation OR
            # from each child's own metadata. Reporter response stamps it on
            # the surrounding block; for nested children we re-read from
            # metadata.sample_type when available.
            child_st = (child.get("metadata") or {}).get("sample_type") or st
            _walk(child, source_uid, child_st)

    # The blocks may sit at bundle["data"] (one-level, as in unit fixtures) OR at
    # bundle["data"]["data"] (the real API body: fetch_reporter_metadata returns
    # {"ok":..., "data": {"total_samples":..., "data": [blocks]}}, which
    # annotate_metadata_with_sampletypes preserves). Unwrap the body dict so we
    # iterate the sample-type blocks, not the body's string keys.
    data = (metadata_bundle or {}).get("data")
    if isinstance(data, dict):
        data = data.get("data")
    for block in data if isinstance(data, list) else []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("sample_type")
        for sample in block.get("samples") or []:
            root_uid = (sample.get("metadata") or {}).get("UID") or sample.get("uuid") or ""
            _walk(sample, source_uid=str(root_uid), leaf_sample_type=block_type)

    return out
