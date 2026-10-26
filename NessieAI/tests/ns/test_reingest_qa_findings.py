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
    # No parent-ish key at all is state 3 of the three-state Parent rule
    # (reingest_qa.py) -- LINEAGE_UNRESOLVED, SOFT, not BLANK_PARENT/HARD.
    report = _qa([{"json_metadata": {"Name": "n1"}}])
    finding = report.findings[0]
    assert finding.code == qa.LINEAGE_UNRESOLVED
    assert finding.severity == "soft"
    assert finding.row_index == 0


def test_hard_and_soft_string_lists_are_still_populated():
    # Minor 6: a two-row fixture covering both severities, so this test's
    # name (which promises both lists) actually matches its depth -- the
    # single-row Name-only fixture it replaced only ever populated `.soft`.
    rows = [{"json_metadata": {"Parent": "", "Name": "n1"}},
            {"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Name": "n2",
                               "Notes": "TODO"}}]
    report = _qa(rows)
    assert report.disposition == qa.HARD_REJECT
    assert report.hard and isinstance(report.hard[0], str)
    assert report.soft and isinstance(report.soft[0], str)


def test_parent_none_and_non_string_parent_land_in_blank_parent_hard():
    # Minor 4: collect_parent_tokens (helpers.py) skips a value that is
    # falsy or not a str, so it returns [] for None/int/list Parent values
    # exactly as it does for a genuinely blank string -- these all reach
    # BLANK_PARENT/HARD via _has_any_parent_key, not LINEAGE_UNRESOLVED/SOFT
    # (state 3, no parent-ish key at all). See the three-state comment in
    # reingest_qa.py for why this reading is deliberate.
    for value in (None, 12345, ["D.SEQ-EXAMPLE-1"]):
        report = _qa([{"json_metadata": {"Parent": value, "Name": "n1"}}])
        assert report.disposition == qa.HARD_REJECT, value
        codes = [f.code for f in report.findings]
        assert qa.BLANK_PARENT in codes, value
        assert qa.LINEAGE_UNRESOLVED not in codes, value


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
    # test's SOFT example; surprise_sentinel still is one. A row with no
    # Parent key at all is now state 3 (LINEAGE_UNRESOLVED, SOFT) of the
    # three-state Parent rule, so it can no longer supply this test's HARD
    # example either -- a present-but-blank Parent key (state 2) still is.
    rows = [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-1", "Name": "n1", "Notes": "TODO"}},
            {"json_metadata": {"Parent": "", "Name": "n2"}}]  # blank Parent -> HARD BLANK_PARENT
    report = _qa(rows)  # n1's "TODO" Notes -> SOFT surprise_sentinel
    assert report.hard  # at least one hard finding present
    assert report.soft  # at least one soft finding present
    assert len(report.hard) + len(report.soft) == len(report.findings)
