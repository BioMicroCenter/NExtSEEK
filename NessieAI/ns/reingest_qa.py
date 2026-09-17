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
# Parent-key presence is a three-state signal, not two (see the new-mode
# Parent-resolvability block below): a parent-ish key present with every
# value blank is BLANK_PARENT, HARD -- something tried to set lineage and
# produced nothing, a real defect. No parent-ish key present at ALL is this
# code, SOFT -- the upstream mapper's deliberate way of shipping a sample it
# could not match to a D.SEQ (see NessieAI/ns/reingest/mapper.py) rather than
# silently dropping the analysis output. Never conflate the two: collapsing
# them back to one HARD code is exactly the regression this split exists to
# prevent (docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md's
# "Matches none" row -- hard on the backfill only, children still ship).
LINEAGE_UNRESOLVED = "lineage_unresolved"
PARENT_UID_NOT_FOUND = "parent_uid_not_found"
DUPLICATE_NAME = "duplicate_name"
SURPRISE_SENTINEL = "surprise_sentinel"
PLACEHOLDER_VALUE = "placeholder_value"
UNKNOWN_SAMPLETYPE = "unknown_sampletype"
MISSING_REQUIRED = "missing_required"
# The two sub-cases of the ALTERNATIVE_REQUIRED_GROUPS SOFT direction each
# get their own code (never MISSING_REQUIRED) so that `group()`'s
# `(code, attribute)` key can never merge a SOFT and a HARD finding that
# share a group-label attribute into one bucket -- see the ALTERNATIVE_
# REQUIRED_GROUPS comment below and report.py's mixed-batch regression test.
PRIMARY_DATA_LINK_ONLY = "primary_data_link_only"
PRIMARY_DATA_UNNAMED = "primary_data_unnamed"
UID_MISSING_IN_UPDATE = "uid_missing_in_update"
UID_PRESENT_IN_NEW = "uid_present_in_new"
UNRESOLVED_UID = "unresolved_uid"
NOTES_WOULD_CLOBBER = "notes_would_clobber"
MULTIRUN_NOT_ATTRIBUTABLE = "multirun_not_attributable"
UNAPPROVED_ATTRIBUTE = "unapproved_attribute"
ATTRIBUTE_NOT_DEFINED = "attribute_not_defined"
METRIC_UNAVAILABLE = "metric_unavailable"
# A required attribute the CATALOG wants but SEEK's own sample_attributes
# table does not (`server_required` is false or unknown for it) -- present
# in `required_fields` but absent from `server_required_fields`/the
# fallback-derived hard set. SEEK would accept the row without it, so this
# is a curation expectation, not a blocker: never MISSING_REQUIRED, which is
# reserved for an attribute the server itself would reject the row over. See
# the qa_rows docstring's `server_required_fields` section for the split.
# History: between a720b7fe and this branch's merge, `Parent` was carved out
# of this split with a module-level `_ALWAYS_HARD_REQUIRED = {"Parent"}`,
# forcing a missing Parent to stay MISSING_REQUIRED/HARD regardless of SEEK's
# own required=0 flag on it. That carve-out existed only because the UID
# resolver behind Parent resolution
# (`nextseek_api/services/reingest_lookups.py`'s `uids_by_primary_data`) was
# hard-scoped to `title="D.SEQ"`, so `RESOLUTION_UNRESOLVED` conflated a
# genuine orphan (no parent exists) with a parent that exists but was never
# searched for (an already-analysed A.* sample). Downgrading that conflated
# signal to SOFT would have silently shipped real orphans as root samples
# (`nextseek_api/batch_upload/orchestrator.py`). The resolver now searches
# every type a pipeline's map declares via `accepts_parent_types`, not just
# D.SEQ (see `NessieAI/ns/reingest/maps.py`), so a parent that exists and is
# findable resolves regardless of its type: an unresolved sample is once
# again a genuine orphan, exactly the case this SOFT code and the
# three-state `LINEAGE_UNRESOLVED` finding below are written for. `Parent`
# therefore follows the plain split again, with no carve-out -- do not
# re-add one without first checking whether the resolver has regressed back
# to a narrow scope.
CATALOG_REQUIRED_MISSING = "catalog_required_missing"

# Used by granular.py's manifest-driven build-upload-xlsx path: every
# attribute on a sample type's rows was parked (attribute_exists said none is
# defined on the schema) and the Notes fetch that would have recorded them
# also failed, so json_metadata is left holding nothing but UID -- nothing
# survives to write. Caught there as a QA hard-reject rather than letting
# render_upload_workbook's ValueError ("rows carry no json_metadata") escape
# as an opaque 502.
NO_ATTRIBUTES_TO_WRITE = "no_attributes_to_write"

# ---------------------------------------------------------------------------
# Alternative-required-attribute groups
# ---------------------------------------------------------------------------
#
# The A.GEX / A.ALN / A.SCXP / D.SEQ catalog rows (startup/seed/dmac.sql.gz,
# table sample_types_context) declare required_metadata including BOTH
# File_PrimaryData (a filesystem path) and Link_PrimaryData (a URL) -- two
# ways to point at the same primary-data artifact, so they are
# interchangeable as DATA.
#
# They are NOT interchangeable at the upload GATE, though, and that is the
# reason this group is directional rather than a plain either-will-do set.
# Per startup/seed/seek_production.sql.gz, table sample_attributes:
# File_PrimaryData is required=1 on some sample types (D.SEQ, A.SCXP) and
# Link_PrimaryData is required=0 on every one of the 82 types that declare
# it at all. So a row that supplies only File_PrimaryData is always safe,
# but a row that supplies only Link_PrimaryData may still be missing the
# path the server actually demands for its sample type -- SEEK's
# `_verifyRequiredFields` (seek/sample/core.py) checks the stored required
# flag, not this group. Concretely:
#   - `primary` present and non-blank -> the requirement is satisfied,
#     full stop, regardless of the secondaries. This is the direction the
#     shipped reingest recipe actually exercises and it is always safe.
#   - only a `secondary` present -> NOT provably satisfied against the
#     PrimaryData requirement itself: the server may still reject the row
#     for lacking `primary`, but it may also not (not every sample type
#     requires it), so this must not block a workbook that could well be
#     fine. This is PRIMARY_DATA_LINK_ONLY, SOFT -- *unless* the row also
#     has none of `_TITLE_FALLBACKS` below: every SEEK upload path titles a
#     new sample by walking Name -> File_PrimaryData -> File_PrimaryData_
#     Forward -> File_PrimaryData_Reverse in order and using the first
#     non-blank one, so a row with none of the four can never be titled and
#     is rejected on EVERY sample type, not a maybe. That sub-case is
#     PRIMARY_DATA_UNNAMED, HARD, even though the PrimaryData requirement
#     itself is only ever a maybe.
#   - neither `primary` nor any secondary present -> exactly one HARD
#     MISSING_REQUIRED finding, naming every member.
#
# This does NOT extend to Checksum_PrimaryData, required by that same
# catalog row: it is not declared here as an alternative to anything, and
# must not be added to a group. Like Parent, Checksum_PrimaryData follows
# the plain required_fields/server_required_fields split like any other
# ordinary attribute: its absence SOFT-flags (CATALOG_REQUIRED_MISSING), not
# a hard reject, because SEEK's own required flag does not enforce it either
# (see the MISSING_REQUIRED loop below).
@dataclass(frozen=True)
class _AlternativeGroup:
    """One directional alternative-required group: `primary`'s presence
    alone satisfies the requirement; each of `secondaries` satisfies it only
    provisionally (soft-flagged when `primary` is absent)."""
    primary: str
    secondaries: tuple[str, ...]

    @property
    def members(self) -> tuple[str, ...]:
        return (self.primary, *self.secondaries)


ALTERNATIVE_REQUIRED_GROUPS: tuple[_AlternativeGroup, ...] = (
    _AlternativeGroup("File_PrimaryData", ("Link_PrimaryData",)),
)

# The full title-derivation chain every SEEK upload path walks before giving
# up on naming a new sample, in order -- mirrored from, not invented beside,
# these three call sites (all four names checked in the same order at each):
#   seek/sample/upload.py:427-434   Name -> File_PrimaryData ->
#                                    File_PrimaryData_Forward ->
#                                    File_PrimaryData_Reverse -> error 302
#   seek/sample/core.py:183-190     same four, then title falls back further,
#                                    to the literal string "Undefined"
#   seek/sample/api.py:141          same chain
# A row missing ALL FOUR cannot be titled by any of these paths and is
# rejected (or silently titled "Undefined") on every sample type -- not a
# maybe, unlike the PrimaryData requirement itself. PRIMARY_DATA_UNNAMED
# below tests against this whole tuple, not just "Name" -- a Forward- or
# Reverse-only, Name-less row is exactly as nameable as a File_PrimaryData-only
# one, and treating it as unnamed silently HARD_REJECTs a row SEEK would
# accept, taking the row's whole sample-type workbook down with it
# (granular.py skips render_upload_workbook on HARD_REJECT).
_TITLE_FALLBACKS: tuple[str, ...] = (
    "Name", "File_PrimaryData", "File_PrimaryData_Forward", "File_PrimaryData_Reverse")

_GROUP_LABEL_SEP = " or "


def _group_containing(attribute: str) -> _AlternativeGroup | None:
    """The ALTERNATIVE_REQUIRED_GROUPS group `attribute` belongs to (as
    primary or secondary), or None."""
    for group in ALTERNATIVE_REQUIRED_GROUPS:
        if attribute in group.members:
            return group
    return None


def group_label(members: tuple[str, ...]) -> str:
    """The Finding.attribute value used for a whole-group MISSING_REQUIRED
    finding, e.g. "File_PrimaryData or Link_PrimaryData" -- named so the
    reader knows every member is named, whatever the finding's severity.
    `report.py`'s `is_group_label` recognises this exact shape to render
    alternatives-aware prose. Takes the group's `.members` tuple (primary
    first), not the `_AlternativeGroup` itself, so a caller that already has
    the plain member names on hand (tests included) need not construct one."""
    return _GROUP_LABEL_SEP.join(members)


def is_group_label(attribute: str) -> bool:
    """True when `attribute` is a `group_label()` rendering of one of
    ALTERNATIVE_REQUIRED_GROUPS, rather than a single attribute title."""
    return any(attribute == group_label(group.members) for group in ALTERNATIVE_REQUIRED_GROUPS)


def group_members_for_label(attribute: str) -> tuple[str, ...] | None:
    """The `.members` tuple (primary first) of the ALTERNATIVE_REQUIRED_GROUPS
    group whose `group_label()` equals `attribute`, or None when `attribute`
    is not a group label. Lets a renderer recover which member is the
    primary from just the label string carried on a Finding/bucket, without
    duplicating `_GROUP_LABEL_SEP` or the group table itself."""
    for group in ALTERNATIVE_REQUIRED_GROUPS:
        if attribute == group_label(group.members):
            return group.members
    return None


def _value_missing(raw) -> bool:
    """True when `raw` (a metadata value for a required attribute) counts as
    missing.

    A falsy-but-present value (0, False) is a real measurement -- a 0%
    mapping rate is data, not a missing attribute. Only "absent" (key
    missing, i.e. None) or a blank/whitespace-only STRING count as missing;
    `meta.get(req) or ""` would collapse 0/False into "" and wrongly
    hard-reject a legitimate zero. `str(raw).strip()` is exactly as wrong in
    the other direction: it stringifies an empty list/dict into "[]"/"{}", a
    non-blank string, so an empty collection would wrongly count as present.
    So: only a string is blank-checked; an empty collection is judged by its
    own truthiness (missing, like a blank string); anything else non-None
    (numbers, bools) is never missing, however falsy -- 0 and False stay
    present.
    """
    if raw is None:
        return True
    if isinstance(raw, str):
        return not raw.strip()
    if isinstance(raw, (list, dict, set, tuple)):
        return not raw
    return False


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
    """{(code, attribute): bucket}, where each bucket is:

        {"count": int,          -- how many findings share this (code, attribute)
         "rows": [...],         -- their row_index values, in encounter order
         "samples": [...],      -- detail["nfcore_sample"] for findings that carry one
         "detail": dict,        -- the first non-empty finding.detail seen for this key
         "severity": str,       -- qa.HARD or qa.SOFT, from the first finding seen
         "sample_type": str}    -- sample_type from the first finding seen

    Consumed by ``NessieAI/ns/reingest/report.py``'s renderer: ``severity``
    routes each bucket to the blocking or advisory section (an unrecognised
    value raises there, same as ``QaReport.add`` does here), and ``samples``
    feeds the capped per-sample listing for ``UNRESOLVED_UID``. ``sample_type``
    is read there too, though the renderer prefers its own (the QaReport being
    iterated is already scoped to one sample type, which is more reliable than
    whatever the first grouped finding happened to carry).

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
    server_required_fields: list[str] | None = None,
    existing_parent_uids: set[str] | None = None,
    mode: str = "new",
    existing_notes: dict[str, str] | None = None,
    run_name: str = "",
) -> QaReport:
    """Validate one sample type's rows. Returns a QaReport (CLEAN/SOFT_FLAG/HARD_REJECT).

    ``required_fields`` / ``server_required_fields`` -- two stores disagree
    about which attributes are required (see
    ``nextseek_api/services/reingest_lookups.py::attributes_for``), and this
    is the split between them for the plain (non-grouped, new-mode absence)
    required-attribute check below:

    - ``required_fields`` -- the catalog's policy (unchanged meaning from
      before this parameter existed). Every title here is still checked for
      absence; this list alone decides WHICH attributes are checked, not
      their severity.
    - ``server_required_fields`` -- the subset of ``required_fields`` that
      SEEK's own ``sample_attributes.required`` flag actually enforces at
      upload. A title missing here (present in ``required_fields``, absent
      or falsy in the row) is genuinely a blocker: ``MISSING_REQUIRED``,
      HARD. A title that IS in ``required_fields`` but NOT in
      ``server_required_fields`` is a curation expectation SEEK would not
      reject the row over: ``CATALOG_REQUIRED_MISSING``, SOFT.
    - ``server_required_fields=None`` (the default) -- an un-updated caller
      that only knows the old parameter. The safe default is today's
      behaviour: every ``required_fields`` title is treated as
      server-required too, so nothing here silently loosens the gate for a
      caller that never learned the split. Pass an explicit list (``[]``
      included) to opt into the split; passing ``[]`` deliberately means
      "nothing here is a hard blocker", which is exactly what an explicit
      empty list should mean, hence the ``None``-vs-``[]`` distinction
      rather than a single falsy check.

    This split does NOT touch the ``ALTERNATIVE_REQUIRED_GROUPS`` directional
    group logic below (File_PrimaryData/Link_PrimaryData), the ``UID``
    exemption, or the update-mode present-but-blank rule -- all three keep
    their existing HARD/SOFT behaviour unchanged. ``Parent`` is no longer
    carved out of this split either -- see the ``CATALOG_REQUIRED_MISSING``
    comment above for why a missing ``Parent`` was briefly forced HARD and
    why that carve-out was removed once the UID resolver could see A.*
    parents again.

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
    # None (not given) -> today's behaviour: every required_fields title is
    # also server-required. An explicit list (including []) is the caller
    # opting into the split -- see the docstring above.
    hard_required = set(required) if server_required_fields is None else set(server_required_fields)
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
            #
            # collect_parent_tokens() returns [] for two genuinely different
            # situations, and `helpers.py` does not (and should not) tell
            # them apart -- it is a plain token collector, used elsewhere for
            # more than this gate. So the distinction is made here, against
            # the raw keys, instead:
            #   1. tokens found -> resolvability-checked exactly as before.
            #   2. a parent-ish key IS present, but every one of them is
            #      blank/whitespace -> BLANK_PARENT, HARD. Something tried to
            #      set lineage and produced nothing -- a real defect.
            #   3. no parent-ish key is present at all -> LINEAGE_UNRESOLVED,
            #      SOFT, and the row still ships. This is the upstream
            #      mapper's deliberate signal that it could not match this
            #      sample to a D.SEQ (no `Parent` key at all, never
            #      `Parent: ""`) -- the spec's "Matches none" row is hard on
            #      the backfill only, so a new-mode child must not be
            #      silently dropped for a lineage gap a curator can attach
            #      later.
            #
            # `collect_parent_tokens` (in `helpers.py`, not editable from
            # here) skips a value that is falsy or not a `str` -- so
            # `{"Parent": None}`, `{"Parent": 12345}` and
            # `{"Parent": ["D.SEQ-EXAMPLE-1"]}` all return [] from it exactly
            # like a genuinely absent key, and land in state 2 (BLANK_PARENT,
            # HARD) via `_has_any_parent_key`, not state 3. That is a
            # deliberate, if narrow, reading: the mapper's actual signal for
            # "could not resolve" is key *absence*, and an explicit `null` (or
            # a non-string value the mapper should never emit) is not
            # absence, so treating it as "something tried to set lineage and
            # produced nothing" is defensible. It is also unpinned by any
            # other comment, so state it once, here: `Parent: None` is
            # newly load-bearing now that it sits on the HARD/SOFT boundary
            # this three-state split created, where before this fix it and
            # every other "no usable Parent value" shape were collapsed into
            # one HARD code.
            parent_tokens = collect_parent_tokens(meta)
            if parent_tokens:
                for token in parent_tokens:
                    if _is_placeholder(token):
                        continue
                    if token not in existing and not any(token in (r.get("json_metadata") or {}).get("Name", "") for r in rows):
                        report.add(Finding(code=PARENT_UID_NOT_FOUND, severity=HARD,
                                            row_index=i, detail={"token": token}))
            elif _has_any_parent_key(meta):
                report.add(Finding(
                    code=BLANK_PARENT, severity=HARD, row_index=i,
                    detail={"reason": "blank Parent (reingest outputs must be derived)"}))
            else:
                report.add(Finding(
                    code=LINEAGE_UNRESOLVED, severity=SOFT, row_index=i,
                    detail={"reason": "no Parent key present (lineage could not be resolved)"}))

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

        # Required-attribute coverage, for an ungrouped attribute: HARD
        # (MISSING_REQUIRED) only when the title is genuinely
        # server-required (in `hard_required` -- SEEK's own
        # `sample_attributes.required` flag enforces it, per
        # `server_required_fields`; `Parent` is no longer carved out of this
        # -- see the `CATALOG_REQUIRED_MISSING` comment above). Every other
        # catalog-required title that is merely absent from SEEK's own flag
        # SOFT-flags instead (CATALOG_REQUIRED_MISSING): Checksum_PrimaryData,
        # for one, is required=0 on A.GEX / A.ALN / A.SCXP / D.SEQ in
        # startup/seed/seek_production.sql.gz, so the server will happily
        # accept a row without it, and hard-rejecting the whole workbook
        # over it would block rows SEEK would have accepted (see
        # a720b7fe, which split this gate from a blanket HARD into this
        # server-required-aware check for exactly that reason). It still
        # SOFT-flags rather than passing silently, because a reingest row
        # with no checksum cannot be verified against the file it claims to
        # describe and a human should decide that, just not by having the
        # upload blocked outright.
        # New-mode-only for ABSENCE, symmetrically with the Parent guard
        # above: an update row targets an existing sample that already
        # carries its required attributes, and only carries the metrics
        # being backfilled, so a required attribute missing from the row is
        # not missing from the database. But deep_merge_metadata overwrites
        # on key PRESENCE: an update row that carries a required key with a
        # blank value blanks it on the server -- the same wholesale-overwrite
        # hazard the Notes guard exists to stop -- so that case is flagged in
        # BOTH modes.
        #
        # Some required titles are interchangeable as DATA but not at the
        # GATE (ALTERNATIVE_REQUIRED_GROUPS above): File_PrimaryData is
        # required=1 on some sample types and Link_PrimaryData is required=0
        # on all of them, so only File_PrimaryData's presence can be trusted
        # to satisfy the requirement unconditionally. That only changes the
        # NEW-mode ABSENCE check, and only for a `req` that names a group
        # member -- once a group has been checked for this row
        # (`handled_groups`), a later `req` naming the same group is skipped.
        # Update-mode's presence-but-blank hazard is NOT grouped: a row that
        # carries one member blank still blanks that specific attribute on
        # the server regardless of its alternative, so each member is
        # checked independently there, exactly as before.
        handled_groups: set[_AlternativeGroup] = set()
        for req in required:
            if req == "UID":
                continue                          # rows never carry one
            if mode == "new":
                group = _group_containing(req)
                if group is not None:
                    if group in handled_groups:
                        continue
                    handled_groups.add(group)
                    if not _value_missing(meta.get(group.primary)):
                        continue                  # primary present: satisfied, full stop
                    present_secondaries = [
                        member for member in group.secondaries
                        if not _value_missing(meta.get(member))]
                    if present_secondaries:
                        # A secondary alone is NOT provably enough against the
                        # PrimaryData requirement itself -- SEEK may still
                        # require the primary for this sample type, but it may
                        # also not -- so that half stays advisory, not
                        # blocking. But a row with none of `_TITLE_FALLBACKS`
                        # (Name, File_PrimaryData, or either paired-end
                        # variant) cannot be titled by ANY sample type's
                        # upload path, which is a guaranteed rejection, not a
                        # maybe -- so that sub-case is its own HARD code,
                        # never folded into the advisory one (see the code
                        # constants' comment: a SOFT and a HARD finding must
                        # never share a code, or a mixed batch merges them
                        # into one bucket and loses one severity's findings
                        # entirely).
                        if all(_value_missing(meta.get(k)) for k in _TITLE_FALLBACKS):
                            report.add(Finding(
                                code=PRIMARY_DATA_UNNAMED, severity=HARD,
                                sample_type=sample_type,
                                attribute=group_label(group.members), row_index=i,
                                detail={"primary": group.primary,
                                        "present_secondary": present_secondaries[0]}))
                        else:
                            report.add(Finding(
                                code=PRIMARY_DATA_LINK_ONLY, severity=SOFT,
                                sample_type=sample_type,
                                attribute=group_label(group.members), row_index=i,
                                detail={"primary": group.primary,
                                        "present_secondary": present_secondaries[0]}))
                    else:
                        report.add(Finding(code=MISSING_REQUIRED, severity=HARD,
                                            sample_type=sample_type,
                                            attribute=group_label(group.members),
                                            row_index=i))
                    continue
                if _value_missing(meta.get(req)):
                    # SEEK's own required flag is the HARD blocker; a title
                    # the catalog wants but SEEK does not enforce is a
                    # curation expectation, not a rejection -- see the
                    # docstring's required_fields/server_required_fields
                    # split above.
                    if req in hard_required:
                        report.add(Finding(code=MISSING_REQUIRED, severity=HARD,
                                            sample_type=sample_type, attribute=req,
                                            row_index=i))
                    else:
                        report.add(Finding(code=CATALOG_REQUIRED_MISSING, severity=SOFT,
                                            sample_type=sample_type, attribute=req,
                                            row_index=i))
            elif req in meta and _value_missing(meta.get(req)):
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


def _has_any_parent_key(meta: dict) -> bool:
    """True when `meta` has at least one key whose name contains "parent"
    (case-insensitive) -- the same key family `collect_parent_tokens` scans
    -- whatever that key's value is. `collect_parent_tokens` returns [] both
    when no such key exists and when every such key is blank; this is the
    other half of the check that tells those two cases apart (see the
    new-mode Parent-resolvability block in `qa_rows`)."""
    return any("parent" in key.lower() for key in meta)
