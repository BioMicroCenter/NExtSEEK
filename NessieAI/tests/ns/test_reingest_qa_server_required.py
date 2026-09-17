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
