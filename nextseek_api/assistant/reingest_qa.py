"""QA the reingest rows CC composes, before they are rendered into an upload
workbook. Ported from dmac_curation's qa_flat_sheets checks, adapted to operate on
the in-memory row dicts (not a flat sheet).

A row: {"json_metadata": {<attr>: <value>, ...}, "assay_ids": [int, ...]}.
All reingest samples are [NEW] (UID blank / server-minted), so UID-uniqueness is
not checked; parents must resolve to EXISTING input UIDs (the D.SEQ cohort).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Placeholder markers are intentional/deferred (OK); surprise sentinels are flagged.
_PLACEHOLDER_MARKERS = ("*** PLACEHOLDER", "***PLACEHOLDER")
_SURPRISE_SENTINELS = ("XXX", "TODO", "FIXME", "???", "TBD", "UNCONFIRMED")

CLEAN = "CLEAN"
SOFT_FLAG = "SOFT_FLAG"
HARD_REJECT = "HARD_REJECT"


@dataclass
class QaReport:
    disposition: str = CLEAN
    hard: list[str] = field(default_factory=list)   # blockers
    soft: list[str] = field(default_factory=list)   # advisory

    def _finalize(self) -> "QaReport":
        self.disposition = HARD_REJECT if self.hard else (SOFT_FLAG if self.soft else CLEAN)
        return self


def qa_rows(
    rows: list[dict],
    *,
    sample_type: str,
    known_sampletypes: set[str],
    required_fields: list[str] | None = None,
    existing_parent_uids: set[str] | None = None,
) -> QaReport:
    """Validate one sample type's rows. Returns a QaReport (CLEAN/SOFT_FLAG/HARD_REJECT)."""
    report = QaReport()
    required = required_fields or []
    existing = existing_parent_uids or set()

    if sample_type not in known_sampletypes:
        report.hard.append(f"unknown_sampletype: {sample_type!r}")

    intra_names: set[str] = set()
    for i, row in enumerate(rows):
        meta = row.get("json_metadata") or {}

        # Parent resolvability (;-split; skip placeholder markers).
        parent = str(meta.get("Parent") or "").strip()
        if not parent:
            report.hard.append(f"row {i}: blank Parent (reingest outputs must be derived)")
        else:
            for token in (t.strip() for t in parent.split(";") if t.strip()):
                if _is_placeholder(token):
                    continue
                if token not in existing and not any(token in (r.get("json_metadata") or {}).get("Name", "") for r in rows):
                    report.hard.append(f"row {i}: parent_uid_not_found: {token!r}")

        # Name uniqueness within the batch (if Names are used).
        name = str(meta.get("Name") or "").strip()
        if name:
            if name in intra_names:
                report.hard.append(f"row {i}: duplicate_name: {name!r}")
            intra_names.add(name)

        # Required-field coverage (advisory — the server is the hard floor).
        for req in required:
            if req in ("UID",):
                continue
            if not str(meta.get(req) or "").strip():
                report.soft.append(f"row {i}: missing_required: {sample_type}:{req}")

        # Placeholder sniff.
        for key, value in meta.items():
            text = str(value or "")
            if _is_placeholder(text):
                continue
            for sentinel in _SURPRISE_SENTINELS:
                if sentinel in text:
                    report.soft.append(f"row {i}: surprise_sentinel {sentinel!r} in {key}")
                    break

    return report._finalize()


def _is_placeholder(text: str) -> bool:
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)
