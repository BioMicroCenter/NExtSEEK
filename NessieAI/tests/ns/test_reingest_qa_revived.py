"""Revive the two dead qa_rows checks: missing_required (now HARD) and
unknown_sampletype, once granular.py actually feeds them a real catalog.
"""
from NessieAI.ns import reingest_qa as qa

EXISTING = {"D.SEQ-EXAMPLE-1"}


def test_a_missing_required_attribute_is_a_hard_reject():
    # File_PrimaryData is one member of the File_PrimaryData/Link_PrimaryData
    # alternatives group (see reingest_qa.ALTERNATIVE_REQUIRED_GROUPS): with
    # neither member present, the group produces one finding naming both,
    # not a finding for File_PrimaryData alone. Checksum_PrimaryData is not
    # in any group and is still flagged on its own.
    report = qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Scientist": "A Person"}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=["File_PrimaryData", "Checksum_PrimaryData", "Scientist"],
        existing_parent_uids=EXISTING)
    assert report.disposition == qa.HARD_REJECT
    missing = {f.attribute for f in report.findings if f.code == qa.MISSING_REQUIRED}
    assert missing == {qa.group_label(("File_PrimaryData", "Link_PrimaryData")),
                        "Checksum_PrimaryData"}


def test_uid_is_exempt_from_the_required_check_in_new_mode():
    report = qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Scientist": "A Person"}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=["UID", "Scientist"], existing_parent_uids=EXISTING)
    assert report.disposition == qa.CLEAN


def test_an_unknown_sample_type_is_a_hard_reject():
    report = qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1"}}],
        sample_type="A.NOPE", known_sampletypes={"A.GEX", "A.ALN", "D.SEQ"},
        existing_parent_uids=EXISTING)
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.UNKNOWN_SAMPLETYPE for f in report.findings)


def test_a_placeholder_satisfies_the_required_check_because_it_is_deliberate():
    report = qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1",
                            "Checksum_PrimaryData": "*** PLACEHOLDER: pending md5 ***"}}],
        sample_type="A.GEX", known_sampletypes={"A.GEX"},
        required_fields=["Checksum_PrimaryData"], existing_parent_uids=EXISTING)
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)
