"""qa_rows' required_fields / server_required_fields split.

Two stores disagree about which attributes are required (see
nextseek_api/services/reingest_lookups.py::attributes_for's docstring):

- the catalog's required_metadata (required_fields) -- a curation policy
- SEEK's own sample_attributes.required (server_required_fields) -- the
  thing that actually rejects a row at upload

A title present in required_fields but absent from server_required_fields
must SOFT-flag (CATALOG_REQUIRED_MISSING), never hard-reject: SEEK would
accept the row without it, so blocking the whole workbook over it invents a
rejection that will not happen at upload. A title present in BOTH, when
missing, must still hard-reject exactly as before this split existed.
"""
from NessieAI.ns import reingest_qa as qa

EXISTING = {"D.SEQ-EXAMPLE-1"}


def _qa(meta, **kw):
    return qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Scientist": "A Person", **meta}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        existing_parent_uids=EXISTING, **kw)


def test_catalog_only_required_attribute_soft_flags_not_hard_rejects():
    # Checksum_PrimaryData: catalog-required, SEEK does not enforce it
    # (verified against startup/seed/seek_production.sql.gz: required=0 on
    # A.GEX/A.ALN/A.SCXP/D.SEQ). Absent from the row, and from
    # server_required_fields -- this is the behaviour change that matters.
    report = _qa({}, required_fields=["Checksum_PrimaryData"],
                 server_required_fields=[])
    assert report.disposition == qa.SOFT_FLAG
    assert not report.hard
    codes = {f.code for f in report.findings}
    assert qa.CATALOG_REQUIRED_MISSING in codes
    assert qa.MISSING_REQUIRED not in codes
    finding = next(f for f in report.findings if f.code == qa.CATALOG_REQUIRED_MISSING)
    assert finding.severity == qa.SOFT
    assert finding.attribute == "Checksum_PrimaryData"


def test_server_required_attribute_still_hard_rejects():
    # A title in BOTH lists, missing, must still hard-reject -- this split
    # must not loosen a genuine server-enforced requirement.
    report = _qa({}, required_fields=["Scientist_X"], server_required_fields=["Scientist_X"])
    assert report.disposition == qa.HARD_REJECT
    codes = {f.code for f in report.findings}
    assert qa.MISSING_REQUIRED in codes
    assert qa.CATALOG_REQUIRED_MISSING not in codes


def test_mixed_batch_soft_flags_one_and_hard_rejects_the_other():
    # A row missing both a server-required and a catalog-only attribute must
    # get one of each finding, at their respective severities -- neither
    # masks the other, and the disposition is HARD_REJECT (any hard finding
    # wins) even though a SOFT one is also present.
    report = _qa({}, required_fields=["Checksum_PrimaryData", "Scientist_X"],
                 server_required_fields=["Scientist_X"])
    assert report.disposition == qa.HARD_REJECT
    by_code = {f.code: f for f in report.findings
               if f.code in (qa.MISSING_REQUIRED, qa.CATALOG_REQUIRED_MISSING)}
    assert by_code[qa.MISSING_REQUIRED].attribute == "Scientist_X"
    assert by_code[qa.MISSING_REQUIRED].severity == qa.HARD
    assert by_code[qa.CATALOG_REQUIRED_MISSING].attribute == "Checksum_PrimaryData"
    assert by_code[qa.CATALOG_REQUIRED_MISSING].severity == qa.SOFT


def test_an_unupdated_caller_passing_only_required_fields_still_gets_hard():
    # No server_required_fields at all (None, the default) -- the safe
    # default is today's behaviour: every required_fields title is treated
    # as server-required too, so an un-updated caller cannot silently end up
    # with a looser gate than before this split existed.
    report = _qa({}, required_fields=["Checksum_PrimaryData"])
    assert report.disposition == qa.HARD_REJECT
    codes = {f.code for f in report.findings}
    assert qa.MISSING_REQUIRED in codes
    assert qa.CATALOG_REQUIRED_MISSING not in codes


def test_a_present_catalog_only_attribute_is_clean_not_flagged():
    report = _qa({"Checksum_PrimaryData": "abc123"},
                 required_fields=["Checksum_PrimaryData"], server_required_fields=[])
    assert report.disposition == qa.CLEAN
    assert not report.hard and not report.soft


# --- Parent: follows this split like any other title, no carve-out --------
#
# Between a720b7fe and this branch's merge, Parent was briefly carved out of
# this split unconditionally (reingest_qa._ALWAYS_HARD_REQUIRED), because the
# UID resolver behind Parent resolution only ever searched D.SEQ, so an
# unresolved parent could mean either "genuinely no parent" or "the parent
# exists but is an A.*-typed sample the resolver never looked for" --
# treating both as SOFT would have silently shipped real orphans as root
# samples. The resolver now searches every type a pipeline's map declares
# (see NessieAI/ns/reingest/maps.py's accepts_parent_types), so a findable
# parent resolves regardless of its type and an unresolved one is once again
# a genuine orphan -- exactly what CATALOG_REQUIRED_MISSING/SOFT and the
# three-state LINEAGE_UNRESOLVED rule are for. Parent is required=0 in SEEK
# on all four reingest sample types (A.GEX/A.ALN/A.SCXP/D.SEQ), so it now
# SOFT-flags like Checksum_PrimaryData or any other catalog-only title.
# `_qa`'s default `meta` always carries a resolvable Parent, so these tests
# build their own rows without it.


def test_parent_missing_soft_flags_when_seek_says_it_is_not_required():
    built = qa.qa_rows(
        [{"json_metadata": {"Scientist": "A Person"}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=["Parent"], server_required_fields=[],
        existing_parent_uids=EXISTING)
    assert built.disposition == qa.SOFT_FLAG
    assert not built.hard
    codes_attrs = {(f.code, f.attribute) for f in built.findings}
    assert (qa.CATALOG_REQUIRED_MISSING, "Parent") in codes_attrs
    assert (qa.MISSING_REQUIRED, "Parent") not in codes_attrs


def test_missing_parent_key_also_soft_flags_lineage_unresolved_alongside_the_catalog_finding():
    # A row with no parent-ish key at all trips BOTH the three-state
    # LINEAGE_UNRESOLVED SOFT finding (no key present at all -- see qa_rows'
    # new-mode Parent-resolvability block) and the plain CATALOG_REQUIRED_
    # MISSING SOFT finding for the same underlying gap. Two SOFT findings on
    # one row saying two different things is fine -- neither is HARD, so
    # nothing here blocks the workbook; pin exactly which codes appear so
    # this interaction cannot silently change. See
    # NessieAI/ns/reingest/report.py's LINEAGE_UNRESOLVED branch, which no
    # longer needs to defer to anything now that both are SOFT.
    built = qa.qa_rows(
        [{"json_metadata": {"Scientist": "A Person"}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=["Parent"], server_required_fields=[],
        existing_parent_uids=EXISTING)
    assert built.disposition == qa.SOFT_FLAG
    assert not built.hard
    codes_attrs = {(f.code, f.attribute) for f in built.findings}
    assert (qa.CATALOG_REQUIRED_MISSING, "Parent") in codes_attrs
    assert any(f.code == qa.LINEAGE_UNRESOLVED for f in built.findings)


def test_parent_present_is_clean():
    # A resolvable Parent must never be flagged at all.
    report = _qa({}, required_fields=["Parent"], server_required_fields=[])
    codes = {f.code for f in report.findings}
    assert qa.MISSING_REQUIRED not in codes
    assert qa.CATALOG_REQUIRED_MISSING not in codes
    assert qa.LINEAGE_UNRESOLVED not in codes
