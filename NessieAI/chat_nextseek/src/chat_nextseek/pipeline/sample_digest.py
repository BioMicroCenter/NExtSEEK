"""Profile a cohort of samples without knowing which pipeline it is for.

`tool_resolve_samples` fetches a sample's whole family and then discards
everything that is not an accepted leaf type for the *already-chosen* pipeline
— so the evidence needed to choose is fetched only after the choice, and is
filtered by it. This module makes the same call without a pipeline_key and
without the accepted-types filter, and keeps all of it.

Costs zero LLM tokens: the expense of the reporter path is entirely in
report_writer_fn, which is not used here.

Imports come from chat_nextseek.reports.* directly. chat_nextseek.helpers
re-exports lazily via PEP 562 to dodge a circular import.
"""
from __future__ import annotations

import collections
import importlib.util
import re
import tempfile
from pathlib import Path
from typing import Any

from chat_nextseek.reports.metadata import (
    annotate_metadata_with_sampletypes,
    build_metadata_summary,
    fetch_reporter_metadata,
    filter_summary_for_deg,
)
from chat_nextseek.reports.protocols import (
    download_and_extract_protocol_blobs,
    extract_protocol_refs_from_metadata,
    fetch_protocols,
    sanitize_protocols_for_llm,
)


class DigestError(Exception):
    """The cohort could not be profiled. There is no selection without evidence."""


# ---------------------------------------------------------------------------
# Input inventory
#
# The per-field summary says how many records populate `DataType` and shows up
# to three of its distinct values. It never says the one thing that decides
# nf-core eligibility: whether this cohort has raw reads to run on at all.
#
# For a cohort whose queried UIDs are themselves D.SEQ records that is easy to
# read off the top block. For one whose queried UIDs are ANALYSIS PRODUCTS it
# is not, and the model has been observed getting it backwards — the
# fibroblast-subtypes cohort (queried as A.SCXP/A.SPTX, with 114 FASTQ D.SEQ
# ancestors in the same digest) was refused with "already provided as processed
# ... rather than raw FASTQs", the opposite of what its own D.SEQ block says.
#
# So state it, as a count, derived from the metadata rather than inferred from
# it. This block draws no conclusion and names no pipeline; it reports what
# files the cohort's records point at, and which of those records were asked
# about versus fetched as lineage relatives.
# ---------------------------------------------------------------------------

#: Compression wrappers stripped before an extension is read, so
#: `reads_R1.fastq.gz` classifies on `.fastq` and `analysis.zarr.zip` on `.zarr`.
_COMPRESSION_SUFFIXES = (".gz", ".bz2", ".zip", ".zst", ".xz")

#: Raw sequencing reads — the input every pipeline in the RNA atlas requires.
_RAW_READ_EXTS = frozenset({".fastq", ".fq"})
_ALIGNED_READ_EXTS = frozenset({".bam", ".cram", ".sam"})

#: NExtSEEK spells `DataType` inconsistently across studies — "fastq", "FastQ"
#: and "FASTQ" all occur within a single cohort — so it is always lowercased
#: before comparison. Used as a fallback when no filename is recorded.
_RAW_READ_DATATYPES = frozenset({"fastq", "fq"})
_ALIGNED_READ_DATATYPES = frozenset({"bam", "cram", "sam"})

_NOT_RECORDED = "(not recorded)"

_INVENTORY_NOTE = (
    "Derived from this cohort's own metadata by classifying each record's "
    "File_PrimaryData filenames (falling back to Link_PrimaryData, then to "
    "DataType). `asked_about` is the UIDs this cohort was queried with; every "
    "other record counted here is a lineage relative fetched alongside them, so "
    "raw reads listed under `raw_reads` belong to this cohort even when the "
    "queried UIDs are analysis products. Counts are over distinct UIDs."
)


def _split_file_list(value: Any) -> list[str]:
    """`File_PrimaryData` holds one filename or several joined by `;` or `,`."""
    if not isinstance(value, str):
        return []
    return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]


def _base_extension(filename: str) -> str:
    """The extension that says what the file IS, with compression wrappers off."""
    name = filename.strip().lower()
    while name.endswith(_COMPRESSION_SUFFIXES):
        name = name[: name.rindex(".")]
    head, dot, ext = name.rpartition(".")
    return f".{ext}" if dot and head else ""


def _classify_record(meta: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (category, filenames) for one sample's primary data.

    Filenames decide before `DataType` does: the filename is what the record
    actually points at, while `DataType` is a hand-entered label.
    """
    filenames = _split_file_list(meta.get("File_PrimaryData")) or _split_file_list(
        meta.get("Link_PrimaryData")
    )
    extensions = {_base_extension(name) for name in filenames}
    if extensions & _RAW_READ_EXTS:
        return "raw_reads", filenames
    if extensions & _ALIGNED_READ_EXTS:
        return "aligned_reads", filenames

    data_type = meta.get("DataType")
    data_type = data_type.strip().lower() if isinstance(data_type, str) else ""
    if data_type in _RAW_READ_DATATYPES:
        return "raw_reads", filenames
    if data_type in _ALIGNED_READ_DATATYPES:
        return "aligned_reads", filenames

    if filenames or data_type:
        return "derived_products", filenames
    return "no_primary_data", filenames


def _tally(records: list[tuple[str, dict[str, Any], list[str]]]) -> dict[str, Any]:
    """Counts plus a few example filenames for one category of record."""
    by_sample_type: collections.Counter = collections.Counter()
    by_data_type: collections.Counter = collections.Counter()
    by_sequencing_type: collections.Counter = collections.Counter()
    by_library_strategy: collections.Counter = collections.Counter()
    examples: list[str] = []

    def _value(meta: dict[str, Any], field: str) -> str:
        raw = meta.get(field)
        return raw.strip() if isinstance(raw, str) and raw.strip() else _NOT_RECORDED

    for sample_type, meta, filenames in records:
        by_sample_type[sample_type or "unknown"] += 1
        by_data_type[_value(meta, "DataType")] += 1
        by_sequencing_type[_value(meta, "SequencingType")] += 1
        by_library_strategy[_value(meta, "LibraryStrategy")] += 1
        for name in filenames:
            if len(examples) < 3 and name not in examples:
                examples.append(name)

    return {
        "n_records": len(records),
        "by_sample_type": dict(by_sample_type.most_common()),
        "by_data_type": dict(by_data_type.most_common()),
        "by_sequencing_type": dict(by_sequencing_type.most_common()),
        "by_library_strategy": dict(by_library_strategy.most_common()),
        "example_files": examples,
    }


def build_input_inventory(
    uid_index: dict[str, Any] | None, queried_uids: list[str] | None
) -> dict[str, Any]:
    """Count what primary data this cohort's records point at.

    `uid_index` is `build_metadata_summary`'s `_uid_index` — every fetched
    sample's full metadata, keyed by UID, which is why this is computed inside
    `build_sample_digest` before that index is stripped from the summary.
    """
    buckets: dict[str, list[tuple[str, dict[str, Any], list[str]]]] = collections.defaultdict(list)
    for entry in (uid_index or {}).values():
        if not isinstance(entry, dict):
            continue
        meta = entry.get("metadata")
        meta = meta if isinstance(meta, dict) else {}
        category, filenames = _classify_record(meta)
        buckets[category].append((entry.get("sample_type") or "unknown", meta, filenames))

    asked_about: collections.Counter = collections.Counter()
    for uid in queried_uids or []:
        entry = (uid_index or {}).get(uid)
        sample_type = entry.get("sample_type") if isinstance(entry, dict) else None
        asked_about[sample_type or "unresolved"] += 1

    inventory: dict[str, Any] = {
        "note": _INVENTORY_NOTE,
        "asked_about": {
            "n_uids": len(queried_uids or []),
            "by_sample_type": dict(asked_about.most_common()),
        },
    }
    for category in ("raw_reads", "aligned_reads", "derived_products"):
        inventory[category] = _tally(buckets.get(category, []))

    # Biological-material records (TIS, PAT, RNA ...) carry no file at all, so
    # a full tally of them would be four dicts of "(not recorded)". They still
    # get a count, because "these 220 records are specimens, not data" is worth
    # saying — it stops the absence of files reading as missing data.
    no_files = buckets.get("no_primary_data", [])
    inventory["no_primary_data"] = {
        "n_records": len(no_files),
        "by_sample_type": dict(
            collections.Counter(sample_type for sample_type, _, _ in no_files).most_common()
        ),
    }
    return inventory


def pdf_extraction_available() -> bool:
    """True when PyPDF2 can be imported in this process."""
    return importlib.util.find_spec("PyPDF2") is not None


_DEFAULT_DEPS = {
    "fetch_metadata": fetch_reporter_metadata,
    "annotate": annotate_metadata_with_sampletypes,
    "summarise": build_metadata_summary,
    "filter_deg": filter_summary_for_deg,
    "extract_refs": extract_protocol_refs_from_metadata,
    "fetch_protocols": fetch_protocols,
    "download_blobs": download_and_extract_protocol_blobs,
    "sanitize": sanitize_protocols_for_llm,
}


def _is_pdf_attachment(attachment: dict) -> bool:
    """Mirror the extractor choice in download_and_extract_protocol_blobs:
    an attachment only takes the PDF path when its content_type says pdf or
    its filename ends .pdf. Everything else (docx, "word", fallback) never
    touches PyPDF2."""
    ctype = (attachment.get("content_type") or "").lower()
    filename = (attachment.get("filename") or "").lower()
    return "pdf" in ctype or filename.endswith(".pdf")


def _classify_extraction(attachment: dict, pdf_available: bool) -> str:
    """Say why an attachment has no text — a missing library and an empty file
    must not look the same."""
    if attachment.get("text"):
        return "ok"
    if not attachment.get("ok", True):
        return f"failed: {attachment.get('error', 'download failed')}"
    if not pdf_available and _is_pdf_attachment(attachment):
        return "unavailable: PyPDF2 not importable"
    if attachment.get("text_error"):
        return f"failed: {attachment['text_error']}"
    return "failed: no text extracted"


def _protocol_text_status(protocols: dict[str, Any]) -> dict[str, Any]:
    """Summarize whether each discovered protocol actually yielded readable
    text, so a caller (selection_context) can act on the failure instead of
    the model silently inferring a library type from an empty attachment.

    A protocol counts as "ok" when at least one of its attachments extracted
    ("extraction" == "ok"); otherwise it counts as "failed", and its
    attachments' non-ok extraction classifications (already produced by
    _classify_extraction — this does not re-derive them) are folded into the
    distinct failure_reasons list. A protocol with zero attachments is its
    own failure reason: there was nothing to extract from at all.
    """
    n_protocols = len(protocols)
    n_ok = 0
    reasons: set[str] = set()
    for entry in protocols.values():
        attachments = entry.get("attachments") or []
        if any(att.get("extraction") == "ok" for att in attachments):
            n_ok += 1
            continue
        if attachments:
            reasons.update(
                att.get("extraction") for att in attachments if att.get("extraction") != "ok"
            )
        else:
            reasons.add("no attachments found")
    return {
        "n_protocols": n_protocols,
        "n_ok": n_ok,
        "n_failed": n_protocols - n_ok,
        "failure_reasons": sorted(reasons),
    }


def build_sample_digest(
    config,
    uids: list[str],
    *,
    base_dir: str | Path | None = None,
    deps: dict[str, Any] | None = None,
    pdf_available: bool | None = None,
) -> dict[str, Any]:
    """Return a bounded profile of a cohort plus the full text of its protocols.

    Raises DigestError on an empty cohort or a failed metadata fetch.
    """
    if not uids:
        raise DigestError("Cannot profile a cohort with no sample UIDs")

    d = {**_DEFAULT_DEPS, **(deps or {})}
    pdf_ok = pdf_extraction_available() if pdf_available is None else pdf_available

    metadata = d["fetch_metadata"](config, uids)
    if not isinstance(metadata, dict) or not metadata.get("ok"):
        reason = (metadata or {}).get("error", "unknown error")
        raise DigestError(f"Sample metadata fetch failed: {reason}")

    annotated = d["annotate"](config, metadata)

    # build_metadata_summary expects a MAP of bundles, not a bare bundle.
    summary = d["summarise"]({"__sample__": annotated}) or {}
    grouping = d["filter_deg"](summary) or {}

    # Computed BEFORE _uid_index is dropped — it is the only place the full
    # per-record metadata is available.
    inventory = build_input_inventory(summary.get("_uid_index"), uids)

    # _uid_index carries every sample's complete metadata; leaving it in
    # multiplies the digest by cohort size and defeats the point of a summary.
    summary = {k: v for k, v in summary.items() if k != "_uid_index"}
    grouping = {k: v for k, v in grouping.items() if k != "_uid_index"}

    refs = d["extract_refs"](annotated) or []
    # Download from the RAW fetched payloads, matching the established caller
    # in reports/outputs.py: sanitize_protocols_for_llm's localhost rewrite is
    # unconditional, while download_and_extract_protocol_blobs' own localhost
    # fixup rebuilds the link from the protocol's source_base_url (e.g. the
    # FairDOMHub host). Sanitizing first would erase the "localhost" substring
    # the download-time fixup looks for, misrouting FairDOMHub blobs.
    raw_payloads = d["fetch_protocols"](config, refs) if refs else {}

    # base_dir supplied: it's a caller-owned session artifact root, write there
    # and leave it alone. base_dir omitted: nothing owns the fallback, so scope
    # it to a TemporaryDirectory that is removed once blob extraction — the
    # only step that writes files — has finished. The extracted text is
    # already captured in `blobs` (in memory) by the time the directory goes
    # away, so nothing downstream needs the files on disk.
    # token_limit=None: the selection path needs uncapped text — the library
    # prep kit name that discriminates between these pipelines is often deep
    # in a methods PDF, past where the default 3000-token cap would cut it.
    if base_dir:
        target_dir = Path(base_dir)
        blobs = d["download_blobs"](raw_payloads, target_dir, config=config, token_limit=None) if raw_payloads else {}
    else:
        with tempfile.TemporaryDirectory(prefix="nessie-digest-") as tmp_dir:
            blobs = d["download_blobs"](raw_payloads, Path(tmp_dir), config=config, token_limit=None) if raw_payloads else {}

    payloads = d["sanitize"](raw_payloads) if raw_payloads else {}

    protocols: dict[str, Any] = {}
    for pid, payload in payloads.items():
        attachments = []
        for att in blobs.get(pid) or []:
            attachments.append({
                "filename": att.get("filename"),
                "content_type": att.get("content_type"),
                "text": att.get("text"),
                "text_truncated": att.get("text_truncated", False),
                "extraction": _classify_extraction(att, pdf_ok),
            })
        protocols[pid] = {"payload": payload, "attachments": attachments}

    return {
        "n_uids": len(uids),
        # Ahead of metadata_summary deliberately: it is two dozen lines that
        # answer "can this cohort be run at all", while the summary is tens of
        # thousands of characters of per-field statistics.
        "input_data_inventory": inventory,
        "metadata_summary": summary,
        "grouping_candidates": grouping,
        "protocols": protocols,
        "protocol_text_status": _protocol_text_status(protocols),
    }
