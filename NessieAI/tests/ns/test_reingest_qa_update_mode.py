from NessieAI.ns import reingest_qa as qa


def _update(rows, existing_notes=None):
    return qa.qa_rows(rows, sample_type="D.SEQ", known_sampletypes={"D.SEQ"},
                      mode="update", existing_notes=existing_notes or {})


def test_update_mode_hard_rejects_a_row_with_no_uid():
    report = _update([{"json_metadata": {"MappedPercent": 91.4}}])
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.UID_MISSING_IN_UPDATE for f in report.findings)


def test_new_mode_hard_rejects_a_row_that_carries_a_uid():
    report = qa.qa_rows([{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1",
                                            "Parent": "D.SEQ-EXAMPLE-0"}}],
                        sample_type="A.GEX", known_sampletypes={"A.GEX"},
                        existing_parent_uids={"D.SEQ-EXAMPLE-0"}, mode="new")
    assert any(f.code == qa.UID_PRESENT_IN_NEW for f in report.findings)


def test_a_notes_write_that_would_drop_existing_text_is_hard_rejected():
    report = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "Notes": "[nfcore-reingest] x"}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "Curator wrote this."})
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)


def test_a_notes_write_that_contains_the_existing_text_passes():
    report = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1",
                            "Notes": "Curator wrote this.\n\n[nfcore-reingest] x"}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "Curator wrote this."})
    assert not any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)


def test_a_uid_whose_notes_could_not_be_fetched_must_not_write_notes():
    # Absent from existing_notes entirely = fetch failed. Writing anyway could
    # destroy text we never read.
    report = _update([{"json_metadata": {"UID": "D.SEQ-EXAMPLE-9", "Notes": "x"}}],
                     existing_notes={})
    assert any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)


def test_an_unapproved_attribute_soft_flags_so_the_batch_is_never_clean():
    report = qa.qa_rows(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "ContamPercent": 3.2},
          "provenance": {"ContamPercent": {"origin": "proposed",
                                           "raw_key": "Kraken2_bracken_fraction",
                                           "times_proposed": 3}}}],
        sample_type="D.SEQ", known_sampletypes={"D.SEQ"}, mode="update",
        existing_notes={"D.SEQ-EXAMPLE-1": ""})
    assert report.disposition == qa.SOFT_FLAG
    finding = next(f for f in report.findings if f.code == qa.UNAPPROVED_ATTRIBUTE)
    assert finding.attribute == "ContamPercent"
    assert finding.detail["times_proposed"] == 3
