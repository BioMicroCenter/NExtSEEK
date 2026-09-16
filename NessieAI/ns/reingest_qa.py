"""QA the reingest rows CC composes, before they are rendered into an upload
workbook. Ported from dmac_curation's qa_flat_sheets checks, adapted to operate on
the in-memory row dicts (not a flat sheet).

A row: {"json_metadata": {<attr>: <value>, ...}, "assay_ids": [int, ...]}.
All reingest samples are [NEW] (UID blank / server-minted), so UID-uniqueness is
not checked; parents must resolve to EXISTING input UIDs (the D.SEQ cohort).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nextseek_api.batch_upload.helpers import collect_parent_tokens

# Placeholder markers are intentional/deferred (OK); surprise sentinels are flagged.
_PLACEHOLDER_MARKERS = ("*** PLACEHOLDER", "***PLACEHOLDER")
_SURPRISE_SENTINELS = ("XXX", "TODO", "FIXME", "???", "TBD", "UNCONFIRMED")

CLEAN = "CLEAN"
SOFT_FLAG = "SOFT_FLAG"
HARD_REJECT = "HARD_REJECT"

HARD = "hard"
SOFT = "soft"

BLANK_PARENT = "blank_parent"
PARENT_UID_NOT_FOUND = "parent_uid_not_found"
DUPLICATE_NAME = "duplicate_name"
SURPRISE_SENTINEL = "surprise_sentinel"
PLACEHOLDER_VALUE = "placeholder_value"
UNKNOWN_SAMPLETYPE = "unknown_sampletype"
MISSING_REQUIRED = "missing_required"
UID_MISSING_IN_UPDATE = "uid_missing_in_update"
UID_PRESENT_IN_NEW = "uid_present_in_new"
UNRESOLVED_UID = "unresolved_uid"
NOTES_WOULD_CLOBBER = "notes_would_clobber"
MULTIRUN_NOT_ATTRIBUTABLE = "multirun_not_attributable"
UNAPPROVED_ATTRIBUTE = "unapproved_attribute"
ATTRIBUTE_NOT_DEFINED = "attribute_not_defined"
METRIC_UNAVAILABLE = "metric_unavailable"


@dataclass
class Finding:
    """One QA observation, structured so N rows sharing it render as one sentence.

    The rendered string still goes into QaReport.hard/.soft: that list is the
    audit trail and the Provenance sheet's input, and dropping it would lose
    per-row detail the grouped message deliberately omits.
    """
    code: str
    severity: str
    sample_type: str = ""
    attribute: str = ""
    row_index: int = -1
    detail: dict = field(default_factory=dict)

    def render(self) -> str:
        if self.row_index >= 0:
            bits = [f"row {self.row_index}"]
            if self.sample_type:
                bits.append(self.sample_type)
        else:
            bits = [self.sample_type]
        bits.append(self.code)
        if self.attribute:
            bits.append(self.attribute)
        if self.detail:
            bits.append(repr(self.detail))
        return ": ".join(bits)


@dataclass
class QaReport:
    disposition: str = CLEAN
    hard: list[str] = field(default_factory=list)   # blockers
    soft: list[str] = field(default_factory=list)   # advisory
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        # Route on the two known severities explicitly and raise on anything
        # else: a QA gate whose entire job is blocking bad uploads must fail
        # loudly on a typo'd or future severity, not quietly file it under
        # .soft and let _finalize() report SOFT_FLAG where HARD_REJECT was owed.
        if finding.severity == HARD:
            self.hard.append(finding.render())
        elif finding.severity == SOFT:
            self.soft.append(finding.render())
        else:
            raise ValueError(f"unknown Finding severity: {finding.severity!r}")
        self.findings.append(finding)

    def _finalize(self) -> "QaReport":
        self.disposition = HARD_REJECT if self.hard else (SOFT_FLAG if self.soft else CLEAN)
        return self


def group(findings):
    """{(code, attribute): {"count": int, "rows": [...], "detail": dict}}.

    Rendering counts rather than enumerating: 24 rows sharing one finding is one
    sentence, not 24 lines of log leaked into a scientist's chat.
    """
    out: dict = {}
    for finding in findings:
        key = (finding.code, finding.attribute)
        bucket = out.setdefault(key, {"count": 0, "rows": [], "samples": [],
                                      "detail": {},
                                      "severity": finding.severity,
                                      "sample_type": finding.sample_type})
        bucket["count"] += 1
        bucket["rows"].append(finding.row_index)
        bucket["detail"] = bucket["detail"] or finding.detail
        named = finding.detail.get("nfcore_sample")
        if named:
            bucket["samples"].append(named)
    return out


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
        report.add(Finding(code=UNKNOWN_SAMPLETYPE, severity=HARD,
                            sample_type=sample_type))

    intra_names: set[str] = set()
    for i, row in enumerate(rows):
        meta = row.get("json_metadata") or {}

        # Parent resolvability (;-split by the helper; skip placeholder markers).
        # Ancestors are declared across EVERY key containing "parent"
        # (AntibodyParent, CompensationFCSParent, Treatment1Parent, …), so read
        # them all: reading only the literal "Parent" hard-rejected rows whose
        # sole ancestor lived in a variant key, and never resolvability-checked
        # the variant tokens it skipped.
        parent_tokens = collect_parent_tokens(meta)
        if not parent_tokens:
            report.add(Finding(
                code=BLANK_PARENT, severity=HARD, row_index=i,
                detail={"reason": "blank Parent (reingest outputs must be derived)"}))
        else:
            for token in parent_tokens:
                if _is_placeholder(token):
                    continue
                if token not in existing and not any(token in (r.get("json_metadata") or {}).get("Name", "") for r in rows):
                    report.add(Finding(code=PARENT_UID_NOT_FOUND, severity=HARD,
                                        row_index=i, detail={"token": token}))

        # Name uniqueness within the batch (if Names are used).
        name = str(meta.get("Name") or "").strip()
        if name:
            if name in intra_names:
                report.add(Finding(code=DUPLICATE_NAME, severity=HARD, row_index=i,
                                    detail={"name": name}))
            intra_names.add(name)

        # Required-field coverage (advisory — the server is the hard floor).
        for req in required:
            if req in ("UID",):
                continue
            if not str(meta.get(req) or "").strip():
                report.add(Finding(code=MISSING_REQUIRED, severity=SOFT, row_index=i,
                                    attribute=req))

        # Placeholder sniff.
        for key, value in meta.items():
            text = str(value or "")
            if _is_placeholder(text):
                continue
            for sentinel in _SURPRISE_SENTINELS:
                if sentinel in text:
                    report.add(Finding(code=SURPRISE_SENTINEL, severity=SOFT,
                                        row_index=i, attribute=key,
                                        detail={"sentinel": sentinel}))
                    break

    return report._finalize()


def _is_placeholder(text: str) -> bool:
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)
