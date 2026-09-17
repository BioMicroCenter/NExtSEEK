"""File_PrimaryData / Link_PrimaryData alternatives group in qa_rows.

The A.GEX / A.ALN / A.SCXP catalog (startup/seed/dmac.sql.gz, table
sample_types_context) declares required_metadata including BOTH
File_PrimaryData (a filesystem path) and Link_PrimaryData (a URL) -- two ways
to point at the same primary-data artifact. NessieAI/ns/reingest_qa.py's
ALTERNATIVE_REQUIRED_GROUPS treats them as interchangeable: either satisfies
the requirement. Checksum_PrimaryData, required by that same catalog row, is
deliberately NOT part of any group and must keep hard-rejecting on its own
(spec Section 9): these tests pin that limit as much as the alternatives rule
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
    report = _qa({"File_PrimaryData": "/net/cluster/runs/gideon4wk/sample1.bam"})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert report.disposition == qa.CLEAN


def test_link_primary_data_alone_satisfies_the_group():
    report = _qa({"Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert report.disposition == qa.CLEAN


def test_both_members_present_satisfies_the_group():
    report = _qa({"File_PrimaryData": "/net/cluster/runs/gideon4wk/sample1.bam",
                  "Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert report.disposition == qa.CLEAN


def test_neither_member_present_is_exactly_one_finding_naming_both():
    report = _qa({})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == _GROUP_LABEL
    assert "File_PrimaryData" in missing_required[0].attribute
    assert "Link_PrimaryData" in missing_required[0].attribute
    assert report.disposition == qa.HARD_REJECT


def test_a_blank_value_for_one_member_with_the_other_present_still_satisfies():
    report = _qa({"File_PrimaryData": "   ",
                  "Link_PrimaryData": "https://example.org/gideon4wk/sample1.bam"})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
    assert report.disposition == qa.CLEAN


def test_both_members_blank_is_still_exactly_one_finding():
    report = _qa({"File_PrimaryData": "", "Link_PrimaryData": "   "})
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == _GROUP_LABEL
    assert report.disposition == qa.HARD_REJECT


def test_checksum_primary_data_absent_still_hard_rejects_on_its_own():
    # This fix's deliberate limit: Checksum_PrimaryData is required by the
    # same real catalog row but is NOT declared as an alternative to
    # anything, and its absence must still hard-reject exactly as before --
    # even when the PrimaryData pair is fully satisfied.
    report = _qa(
        {"File_PrimaryData": "/net/cluster/runs/gideon4wk/sample1.bam"},
        required=["File_PrimaryData", "Link_PrimaryData", "Checksum_PrimaryData"])
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == "Checksum_PrimaryData"
    assert report.disposition == qa.HARD_REJECT


def test_an_attribute_not_in_any_group_behaves_exactly_as_today():
    # _qa's base metadata only ever injects Parent, so an ungrouped required
    # attribute like Scientist is absent here and must still be flagged
    # individually, by its own bare title, exactly as before this change.
    report = _qa({}, required=["Scientist"])
    missing_required = [f for f in report.findings if f.code == qa.MISSING_REQUIRED]
    assert len(missing_required) == 1
    assert missing_required[0].attribute == "Scientist"


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
