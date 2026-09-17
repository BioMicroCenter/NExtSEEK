"""File_PrimaryData / Link_PrimaryData alternatives group in qa_rows.

The A.GEX / A.ALN / A.SCXP / D.SEQ catalog (startup/seed/dmac.sql.gz, table
sample_types_context) declares required_metadata including BOTH
File_PrimaryData (a filesystem path) and Link_PrimaryData (a URL) -- two ways
to point at the same primary-data artifact, so they are interchangeable as
DATA. They are NOT interchangeable at the upload gate: per
startup/seed/seek_production.sql.gz, table sample_attributes,
File_PrimaryData is required=1 on some sample types (D.SEQ, A.SCXP) while
Link_PrimaryData is required=0 on every one of the 82 types that declare it
at all. So NessieAI/ns/reingest_qa.py's ALTERNATIVE_REQUIRED_GROUPS is
directional, not a plain either-will-do set:

- File_PrimaryData (the primary) present and non-blank -> satisfied, full
  stop, regardless of Link_PrimaryData. This is the direction the shipped
  reingest recipe actually exercises, and it is always safe.
- Link_PrimaryData (a secondary) present without File_PrimaryData -> NOT
  provably safe (the server may still require File_PrimaryData for this
  sample type), so this is a SOFT flag, not CLEAN and not HARD.
- Neither present -> exactly one HARD finding naming every member.

Checksum_PrimaryData, required by that same catalog row, is deliberately NOT
part of any group and must keep hard-rejecting on its own regardless of the
group's state: these tests pin that limit as much as the alternatives rule
itself.
"""
from NessieAI.ns import reingest_qa as qa

EXISTING = {"D.SEQ-EXAMPLE-1"}
_GROUP_LABEL = qa.group_label(("File_PrimaryData", "Link_PrimaryData"))
_REQUIRED = ["File_PrimaryData", "Link_PrimaryData"]


def _qa(meta, required=None, **kw):
    return qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", **meta}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=_REQUIRED if required is None else required,
        existing_parent_uids=EXISTING, **kw)


def test_file_primary_data_alone_satisfies_the_group():
    # The safe direction, and the one the shipped agent recipe exercises:
    # File_PrimaryData alone must keep this CLEAN. This is the regression
    # this whole fix protects -- 2ad401d0's symmetric group got this right
    # only by accident, by also getting the unsafe direction wrong.
    report = _qa({"File_PrimaryData": "/net/cluster/runs/gideon4wk/sample1.bam"})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert report.disposition == qa.CLEAN


def test_link_primary_data_alone_with_a_name_soft_flags_not_clean():
    # The unsafe direction, named sub-case: SEEK requires File_PrimaryData on
    # some sample types and never requires Link_PrimaryData on any of them,
    # so a Link-only row is not provably safe against that requirement. It
    # must not be silently waved through as CLEAN (2ad401d0's defect) -- but
    # a Name is present here, so both SEEK upload paths can still title the
    # sample, and the row is not a guaranteed rejection. It must also not
    # HARD block a workbook that may well be fine for a type where SEEK does
    # not require the path, so it soft-flags instead, as PRIMARY_DATA_LINK_ONLY
    # (its own code -- never MISSING_REQUIRED, which is reserved for the
    # neither-present HARD case; see Important 1 of the fix that added this).
    report = _qa({"Name": "SAMPLE_1",
                  "Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    link_only = [f for f in report.findings if f.code == qa.PRIMARY_DATA_LINK_ONLY]
    assert len(link_only) == 1
    finding = link_only[0]
    assert finding.severity == qa.SOFT
    assert finding.attribute == _GROUP_LABEL
    assert finding.detail == {"primary": "File_PrimaryData",
                               "present_secondary": "Link_PrimaryData"}
    assert report.disposition == qa.SOFT_FLAG
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert not any(f.code == qa.PRIMARY_DATA_UNNAMED for f in report.findings)


def test_link_primary_data_alone_without_a_name_hard_rejects():
    # Important 3: the same Link-only shape as above, but with no Name
    # either. Both SEEK upload paths derive the sample title from Name,
    # falling back to File_PrimaryData (seek/sample/upload.py's per-row
    # check errors with code 302; seek/sample/core.py falls back further, to
    # the literal title "Undefined") -- with neither present, the row cannot
    # be titled and is rejected on EVERY sample type, not a maybe. That is
    # decidable from the row alone, so it hard-rejects as PRIMARY_DATA_UNNAMED
    # instead of the usual advisory SOFT.
    report = _qa({"Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    unnamed = [f for f in report.findings if f.code == qa.PRIMARY_DATA_UNNAMED]
    assert len(unnamed) == 1
    finding = unnamed[0]
    assert finding.severity == qa.HARD
    assert finding.attribute == _GROUP_LABEL
    assert finding.detail == {"primary": "File_PrimaryData",
                               "present_secondary": "Link_PrimaryData"}
    assert report.disposition == qa.HARD_REJECT
    assert not any(f.code == qa.PRIMARY_DATA_LINK_ONLY for f in report.findings)
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)


def test_link_primary_data_with_forward_only_soft_flags_not_hard_rejects():
    # Important 1: PRIMARY_DATA_UNNAMED's title check is the full four-link
    # chain (_TITLE_FALLBACKS), not just Name -- SEEK's own upload paths
    # (seek/sample/upload.py:427-434, seek/sample/core.py:183-190,
    # seek/sample/api.py:141) all fall back from Name to File_PrimaryData to
    # File_PrimaryData_Forward to File_PrimaryData_Reverse before giving up.
    # A Forward-only row with no Name is exactly as nameable (by
    # File_PrimaryData_Forward) as a File_PrimaryData-only row, so it must
    # soft-flag as PRIMARY_DATA_LINK_ONLY, the same as the named case above --
    # truncating the chain to just Name would hard-reject a row SEEK accepts
    # and, via granular.py skipping render_upload_workbook on HARD_REJECT,
    # drop the whole sample type's workbook.
    report = _qa({"Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam",
                  "File_PrimaryData_Forward": "fwd.fastq"})
    link_only = [f for f in report.findings if f.code == qa.PRIMARY_DATA_LINK_ONLY]
    assert len(link_only) == 1
    assert link_only[0].severity == qa.SOFT
    assert report.disposition == qa.SOFT_FLAG
    assert not any(f.code == qa.PRIMARY_DATA_UNNAMED for f in report.findings)


def test_link_primary_data_with_reverse_only_soft_flags_not_hard_rejects():
    # Same as the Forward case above, for the other paired-end fallback.
    report = _qa({"Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam",
                  "File_PrimaryData_Reverse": "rev.fastq"})
    link_only = [f for f in report.findings if f.code == qa.PRIMARY_DATA_LINK_ONLY]
    assert len(link_only) == 1
    assert link_only[0].severity == qa.SOFT
    assert report.disposition == qa.SOFT_FLAG
    assert not any(f.code == qa.PRIMARY_DATA_UNNAMED for f in report.findings)


def test_link_primary_data_alone_with_no_fallback_at_all_still_hard_rejects():
    # The genuine PRIMARY_DATA_UNNAMED case survives the widened chain: a
    # Link-only row with none of Name/File_PrimaryData/Forward/Reverse is
    # still unnameable by any SEEK upload path.
    report = _qa({"Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    unnamed = [f for f in report.findings if f.code == qa.PRIMARY_DATA_UNNAMED]
    assert len(unnamed) == 1
    assert unnamed[0].severity == qa.HARD
    assert report.disposition == qa.HARD_REJECT


def test_both_members_present_satisfies_the_group():
    report = _qa({"File_PrimaryData": "/net/cluster/runs/gideon4wk/sample1.bam",
                  "Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert report.disposition == qa.CLEAN


def test_neither_member_present_is_exactly_one_hard_finding_naming_both():
    report = _qa({})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].severity == qa.HARD
    assert missing_required[0].attribute == _GROUP_LABEL
    assert "File_PrimaryData" in missing_required[0].attribute
    assert "Link_PrimaryData" in missing_required[0].attribute
    assert report.disposition == qa.HARD_REJECT


def test_a_blank_file_primary_data_with_link_present_soft_flags():
    # A blank string is "missing" (_value_missing), so a blank
    # File_PrimaryData does not count as the primary being present -- this
    # is the same soft-flag case as File_PrimaryData being wholly absent,
    # not a fresh "blank beats present" exception. A Name is supplied so this
    # exercises only that question, not the separate Important-3 Name check.
    report = _qa({"Name": "SAMPLE_1", "File_PrimaryData": "   ",
                  "Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    link_only = [f for f in report.findings if f.code == qa.PRIMARY_DATA_LINK_ONLY]
    assert len(link_only) == 1
    assert link_only[0].severity == qa.SOFT
    assert report.disposition == qa.SOFT_FLAG


def test_both_members_blank_is_still_exactly_one_hard_finding():
    report = _qa({"File_PrimaryData": "", "Link_PrimaryData": "   "})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].severity == qa.HARD
    assert missing_required[0].attribute == _GROUP_LABEL
    assert report.disposition == qa.HARD_REJECT


def test_checksum_primary_data_absent_still_hard_rejects_on_its_own():
    # This fix's deliberate limit: Checksum_PrimaryData is required by the
    # same real catalog row but is NOT declared as an alternative to
    # anything, and its absence must still hard-reject exactly as before --
    # even when the PrimaryData pair is fully satisfied (File_PrimaryData
    # present, the safe direction).
    report = _qa(
        {"File_PrimaryData": "/net/cluster/runs/gideon4wk/sample1.bam"},
        required=["File_PrimaryData", "Link_PrimaryData", "Checksum_PrimaryData"])
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == "Checksum_PrimaryData"
    assert missing_required[0].severity == qa.HARD
    assert report.disposition == qa.HARD_REJECT


def test_an_attribute_not_in_any_group_behaves_exactly_as_today():
    # _qa's base metadata only ever injects Parent, so an ungrouped required
    # attribute like Scientist is absent here and must still be flagged
    # individually, by its own bare title, exactly as before this change.
    report = _qa({}, required=["Scientist"])
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == "Scientist"
    assert missing_required[0].severity == qa.HARD


def test_update_mode_still_flags_only_present_but_blank_per_member():
    # Update-mode behaviour is unchanged by the group: it flags only a
    # present-but-blank required key (deep_merge_metadata overwrites on key
    # PRESENCE), independently per member, never grouped. A blank
    # File_PrimaryData still flags File_PrimaryData even though
    # Link_PrimaryData is fine -- the row would blank File_PrimaryData on
    # the server regardless of its alternative.
    report = qa.qa_rows(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "File_PrimaryData": "",
                            "Link_PrimaryData": "https://example.org/x.bam"}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=_REQUIRED, mode="update", existing_notes={})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == "File_PrimaryData"
    assert report.disposition == qa.HARD_REJECT


def test_update_mode_absence_of_both_members_is_not_flagged():
    # Symmetric with the general update-mode rule: an update row only
    # carries the metrics being backfilled, so absence from the row is not
    # absence from the database -- this holds for group members too.
    report = qa.qa_rows(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "MappedPercent": 91.4}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=_REQUIRED, mode="update", existing_notes={})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
