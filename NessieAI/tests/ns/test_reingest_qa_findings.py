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
