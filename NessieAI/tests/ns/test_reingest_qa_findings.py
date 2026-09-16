import pytest

from NessieAI.ns import reingest_qa as qa

ROWS = [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Name": "n1"}},
        {"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Name": "n2"}}]
EXISTING = {"D.SEQ-EXAMPLE-1"}


def _qa(rows=None, **kw):
    params = dict(sample_type="A.GEX", known_sampletypes={"A.GEX"},
                  existing_parent_uids=EXISTING)
    params.update(kw)
    return qa.qa_rows(rows if rows is not None else ROWS, **params)


def test_a_clean_batch_produces_no_findings():
    report = _qa()
    assert report.disposition == qa.CLEAN
    assert report.findings == []


def test_a_finding_is_structured_not_a_string():
    report = _qa([{"json_metadata": {"Name": "n1"}}])
    finding = report.findings[0]
    assert finding.code == qa.BLANK_PARENT
    assert finding.severity == "hard"
    assert finding.row_index == 0


def test_hard_and_soft_string_lists_are_still_populated():
    report = _qa([{"json_metadata": {"Name": "n1"}}])
    assert report.disposition == qa.HARD_REJECT
    assert report.hard and isinstance(report.hard[0], str)


def test_findings_sharing_a_code_and_attribute_group_for_rendering():
    rows = [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Name": f"n{i}",
                               "Notes": "TODO"}} for i in range(24)]
    report = _qa(rows)
    grouped = qa.group(report.findings)
    key = (qa.SURPRISE_SENTINEL, "Notes")
    assert grouped[key]["count"] == 24
    assert len(grouped[key]["rows"]) == 24


def test_add_raises_on_an_unrecognised_severity():
    report = qa.QaReport()
    with pytest.raises(ValueError, match="nonsense"):
        report.add(qa.Finding(code=qa.BLANK_PARENT, severity="nonsense", row_index=0))
    assert report.findings == []
    assert report.hard == []
    assert report.soft == []


def test_add_still_files_a_soft_finding_in_soft():
    report = qa.QaReport()
    report.add(qa.Finding(code=qa.MISSING_REQUIRED, severity=qa.SOFT, row_index=0,
                           attribute="SomeField"))
    assert report.hard == []
    assert len(report.soft) == 1
    assert len(report.findings) == 1


def test_render_includes_sample_type_on_a_row_level_finding_that_has_one():
    finding = qa.Finding(code=qa.MISSING_REQUIRED, severity=qa.SOFT, row_index=3,
                          sample_type="A.GEX", attribute="SomeField")
    assert finding.render() == "row 3: A.GEX: missing_required: SomeField"


def test_render_of_a_batch_level_finding_is_unchanged():
    finding = qa.Finding(code=qa.UNKNOWN_SAMPLETYPE, severity=qa.HARD,
                          sample_type="A.GEX")
    assert finding.render() == "A.GEX: unknown_sampletype"


def test_every_finding_lands_in_exactly_one_of_hard_or_soft():
    # missing_required moved to HARD (Task 4), so it can no longer supply this
    # test's SOFT example; surprise_sentinel still is one.
    rows = [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Name": "n1", "Notes": "TODO"}},
            {"json_metadata": {"Name": "n2"}}]  # n2 has no Parent -> HARD BLANK_PARENT
    report = _qa(rows)  # n1's "TODO" Notes -> SOFT surprise_sentinel
    assert report.hard  # at least one hard finding present
    assert report.soft  # at least one soft finding present
    assert len(report.hard) + len(report.soft) == len(report.findings)
