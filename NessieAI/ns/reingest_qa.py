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

from NessieAI.ns.reingest import notes

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
    mode: str = "new",
    existing_notes: dict[str, str] | None = None,
    run_name: str = "",
) -> QaReport:
    """Validate one sample type's rows. Returns a QaReport (CLEAN/SOFT_FLAG/HARD_REJECT).

    ``mode`` is ``"new"`` (brand-new samples; rows must not carry a UID and
    must declare a Parent) or ``"update"`` (a backfill targeting samples that
    already exist; rows must carry a UID and have no Parent to declare).

    ``run_name`` -- the reingest run whose ``notes.compose`` output is being
    QA'd. ``compose`` is *specified* to remove this run's own previous block
    before appending the fresh one (see ``NessieAI/ns/reingest/notes.py``), so
    from the second run onward the composed Notes value legitimately no
    longer contains the fetched ``prior`` text verbatim -- only this run's own
    tag line is gone, nothing else is licensed to disappear. When ``run_name``
    is given, the guard below compares against ``notes.strip_block(prior,
    run_name)`` instead of raw ``prior``, so exactly that disappearance is
    forgiven. When ``run_name`` is empty (the default), nothing is licensed to
    disappear and the guard falls back to comparing against raw ``prior`` --
    the same behavior as before this parameter existed.

    ``existing_notes`` -- CONTRACT, load-bearing, read this before wiring a
    fetcher to this parameter:

    This is a ``{uid: fetched_notes_text}`` map, and it is the only thing
    standing between an update-mode backfill and silently destroying whatever
    a curator wrote in a sample's Notes (``deep_merge_metadata`` replaces the
    whole field). The map MUST distinguish two states per uid, and they are
    NOT the same value:

    - key ABSENT -- the fetch failed, or was never attempted, for this uid.
      Treated as "we do not know what is there"; any row that writes Notes
      for this uid is HARD-rejected (``NOTES_WOULD_CLOBBER``) rather than
      risking an overwrite of unread text.
    - key present with value ``""`` -- the fetch SUCCEEDED and the sample's
      Notes was genuinely empty. Treated as "nothing to preserve"; a Notes
      write for this uid is allowed through.

    Do NOT build this map with the common ``notes.get(uid, "")`` idiom (or
    any other default-to-empty-string fetch pattern): that collapses "fetch
    failed" into "fetched, empty", which reads to this guard as "nothing to
    preserve" and waves the write through -- defeating the entire point of
    this parameter. A failed fetch must leave the uid OUT of the dict, never
    map it to ``""``.
    """
    report = QaReport()
    required = required_fields or []
    existing = existing_parent_uids or set()

    if sample_type not in known_sampletypes:
        report.add(Finding(code=UNKNOWN_SAMPLETYPE, severity=HARD,
                            sample_type=sample_type))

    intra_names: set[str] = set()
    for i, row in enumerate(rows):
        meta = row.get("json_metadata") or {}

        if mode == "new":
            # Parent resolvability (;-split by the helper; skip placeholder
            # markers). Ancestors are declared across EVERY key containing
            # "parent" (AntibodyParent, CompensationFCSParent,
            # Treatment1Parent, …), so read them all: reading only the
            # literal "Parent" hard-rejected rows whose sole ancestor lived
            # in a variant key, and never resolvability-checked the variant
            # tokens it skipped. A backfill row targets an existing sample
            # and has no Parent to declare, so this whole check is
            # new-mode-only.
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

        uid = str(meta.get("UID") or "").strip()
        if mode == "update" and not uid:
            report.add(Finding(code=UID_MISSING_IN_UPDATE, severity=HARD,
                               sample_type=sample_type, attribute="UID", row_index=i))
        if mode == "new" and uid:
            report.add(Finding(code=UID_PRESENT_IN_NEW, severity=HARD,
                               sample_type=sample_type, attribute="UID", row_index=i,
                               detail={"uid": uid}))

        # Notes is overwritten wholesale by deep_merge_metadata, so a write that
        # does not carry the fetched existing text verbatim destroys it. A UID
        # absent from existing_notes means the fetch failed: refuse rather than
        # write over text we never read.
        if mode == "update" and "Notes" in meta:
            if uid not in (existing_notes or {}):
                report.add(Finding(code=NOTES_WOULD_CLOBBER, severity=HARD,
                                   sample_type=sample_type, attribute="Notes",
                                   row_index=i, detail={"uid": uid,
                                                        "reason": "existing Notes not fetched"}))
            else:
                prior = (existing_notes or {})[uid]
                # This run's own previous block is the one thing licensed to
                # disappear (notes.compose strips it before appending the
                # fresh one) -- but only when we know which run is writing.
                # No run_name means nothing is licensed to disappear, so fall
                # back to the raw fetched text.
                prior_for_compare = notes.strip_block(prior, run_name) if run_name else prior
                # Trailing-whitespace-only differences (a trailing space, a
                # trailing blank line) must never trip this guard: they are
                # not data loss. Strip trailing whitespace off `prior` only
                # before the containment check -- never off the composed
                # text, and never interior whitespace -- so a genuine drop of
                # any interior content still hard-rejects.
                #
                # Deliberate, documented non-issue (not fixed here): when
                # `run_name` is passed, `prior_for_compare` is
                # `notes.strip_block`'s output, whose own `.strip()` also
                # drops LEADING whitespace off the very first line of
                # `prior` (see `NessieAI/ns/reingest/notes.py`'s module
                # docstring). A prior Notes value of
                # ``"   indented curator note\n\n[block]"`` therefore passes
                # this guard even though the composed text has lost its
                # leading spaces -- whitespace-only, never content, so this
                # is intentionally not treated as clobbering.
                if prior_for_compare and prior_for_compare.rstrip() not in str(meta.get("Notes") or ""):
                    report.add(Finding(code=NOTES_WOULD_CLOBBER, severity=HARD,
                                       sample_type=sample_type, attribute="Notes",
                                       row_index=i, detail={"uid": uid,
                                                            "reason": "existing text absent"}))

        # Provenance-driven flags: a value from an unapproved source can never
        # come back CLEAN, which is what stops unreviewed rules drifting in.
        for attribute, origin in (row.get("provenance") or {}).items():
            if origin.get("origin") == "proposed":
                report.add(Finding(code=UNAPPROVED_ATTRIBUTE, severity=SOFT,
                                   sample_type=sample_type, attribute=attribute,
                                   row_index=i, detail=dict(origin)))
            elif origin.get("origin") == "parked":
                report.add(Finding(code=ATTRIBUTE_NOT_DEFINED, severity=SOFT,
                                   sample_type=sample_type, attribute=attribute,
                                   row_index=i, detail=dict(origin)))

        # Name uniqueness within the batch (if Names are used).
        name = str(meta.get("Name") or "").strip()
        if name:
            if name in intra_names:
                report.add(Finding(code=DUPLICATE_NAME, severity=HARD, row_index=i,
                                    detail={"name": name}))
            intra_names.add(name)

        # Required-attribute coverage. HARD, not advisory: Checksum_PrimaryData is
        # required on A.GEX / A.ALN / A.SCXP, so calling its absence a soft flag
        # only defers the server's rejection to upload time. New-mode-only for
        # ABSENCE, symmetrically with the Parent guard above: an update row
        # targets an existing sample that already carries its required
        # attributes, and only carries the metrics being backfilled, so a
        # required attribute missing from the row is not missing from the
        # database. But deep_merge_metadata overwrites on key PRESENCE: an
        # update row that carries a required key with a blank value blanks it
        # on the server -- the same wholesale-overwrite hazard the Notes guard
        # exists to stop -- so that case is flagged in BOTH modes.
        for req in required:
            if req == "UID":
                continue                          # rows never carry one
            # A falsy-but-present value (0, False) is a real measurement --
            # a 0% mapping rate is data, not a missing attribute. Only
            # "absent" (key missing, i.e. None) or "blank string" count as
            # missing; `meta.get(req) or ""` would collapse 0/False into ""
            # and wrongly hard-reject a legitimate zero.
            raw = meta.get(req)
            value = "" if raw is None else str(raw).strip()
            if mode == "new":
                if not value:
                    report.add(Finding(code=MISSING_REQUIRED, severity=HARD,
                                        sample_type=sample_type, attribute=req,
                                        row_index=i))
            elif req in meta and not value:
                report.add(Finding(code=MISSING_REQUIRED, severity=HARD,
                                    sample_type=sample_type, attribute=req,
                                    row_index=i))

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
