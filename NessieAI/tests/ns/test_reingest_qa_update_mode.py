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


def test_update_mode_does_not_hard_reject_a_required_attribute_missing_from_the_row():
    # An update row only carries the metrics being backfilled; the target
    # sample already holds its required attributes, so their absence from
    # the row is not absence from the database.
    report = qa.qa_rows(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "MappedPercent": 91.4}}],
        sample_type="D.SEQ", known_sampletypes={"D.SEQ"},
        required_fields=["Checksum_PrimaryData"], mode="update",
        existing_notes={})
    assert not any(f.code == qa.MISSING_REQUIRED for f in report.findings)


def test_new_mode_still_hard_rejects_the_same_missing_required_attribute():
    report = qa.qa_rows(
        [{"json_metadata": {"Parent": "D.SEQ-EXAMPLE-0", "MappedPercent": 91.4}}],
        sample_type="D.SEQ", known_sampletypes={"D.SEQ"},
        required_fields=["Checksum_PrimaryData"],
        existing_parent_uids={"D.SEQ-EXAMPLE-0"}, mode="new")
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.MISSING_REQUIRED for f in report.findings)


def test_trailing_whitespace_only_difference_in_notes_does_not_clobber():
    # A trailing space or trailing blank lines picked up by a round-tripped
    # fetch must not trip the guard: no content was dropped.
    report = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1",
                            "Notes": "Curator wrote this.\n\n[nfcore-reingest] x"}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "Curator wrote this.  "})
    assert not any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)

    report2 = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1",
                            "Notes": "Curator wrote this.\n\n[nfcore-reingest] x"}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "Curator wrote this.\n\n\n"})
    assert not any(f.code == qa.NOTES_WOULD_CLOBBER for f in report2.findings)


def test_a_genuine_content_drop_still_hard_rejects_despite_the_whitespace_fix():
    report = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1",
                            "Notes": "[nfcore-reingest] x"}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "Curator wrote this.   "})
    assert report.disposition == qa.HARD_REJECT
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
