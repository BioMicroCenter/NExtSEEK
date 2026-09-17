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


def test_link_primary_data_alone_soft_flags_not_clean():
    # The unsafe direction: SEEK requires File_PrimaryData on some sample
    # types and never requires Link_PrimaryData on any of them, so a
    # Link-only row is not provably safe. It must not be silently waved
    # through as CLEAN (2ad401d0's defect) -- but it must also not HARD
    # block a workbook that may well be fine for a type where SEEK does not
    # require the path, so it soft-flags instead.
    report = _qa({"Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    finding = missing_required[0]
    assert finding.severity == qa.SOFT
    assert finding.attribute == _GROUP_LABEL
    assert finding.detail == {"primary": "File_PrimaryData",
                               "present_secondary": "Link_PrimaryData"}
    assert report.disposition == qa.SOFT_FLAG


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
    # not a fresh "blank beats present" exception.
    report = _qa({"File_PrimaryData": "   ",
                  "Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].severity == qa.SOFT
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
