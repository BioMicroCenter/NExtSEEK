from NessieAI.ns import reingest_qa as qa


def _update(rows, existing_notes=None, run_name=""):
    return qa.qa_rows(rows, sample_type="D.SEQ", known_sampletypes={"D.SEQ"},
                      mode="update", existing_notes=existing_notes or {},
                      run_name=run_name)


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


def test_update_mode_hard_rejects_a_required_attribute_present_but_blank():
    # deep_merge_metadata overwrites on key PRESENCE: a row that carries the
    # key with a blank value blanks it on the server -- the same wholesale-
    # overwrite hazard the Notes guard exists to stop. Absence is fine
    # (covered above); presence-but-blank is not.
    report = qa.qa_rows(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "Checksum_PrimaryData": "",
                            "MappedPercent": 91.4}}],
        sample_type="D.SEQ", known_sampletypes={"D.SEQ"},
        required_fields=["Checksum_PrimaryData"], mode="update",
        existing_notes={})
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


def test_interior_whitespace_differences_in_notes_still_clobber():
    # Only TRAILING whitespace is forgiven. A fix that normalised interior
    # whitespace too would also pass this pair, so pin the distinction: "a\n\nb"
    # is not contained in "a b ..." even though both are "a", whitespace, "b".
    report = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1", "Notes": "a b ..."}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "a\n\nb"})
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)


def test_a_genuine_content_drop_still_hard_rejects_despite_the_whitespace_fix():
    report = _update(
        [{"json_metadata": {"UID": "D.SEQ-EXAMPLE-1",
                            "Notes": "[nfcore-reingest] x"}}],
        existing_notes={"D.SEQ-EXAMPLE-1": "Curator wrote this.   "})
    assert report.disposition == qa.HARD_REJECT
    assert any(f.code == qa.NOTES_WOULD_CLOBBER for f in report.findings)


def test_composing_across_two_runs_round_trips_through_the_guard():
    # notes.compose is *specified* to drop this run's own previous block
    # before appending the fresh one, so run 2's composed output legitimately
    # no longer contains run 1's tag line verbatim. Passing run_name licenses
    # exactly that disappearance -- nothing else -- so the guard must not
    # read it as data loss.
    from NessieAI.ns.reingest import notes as notes_mod

    run_name = "nfcore_rnaseq_run"
    uid = "D.SEQ-EXAMPLE-1"

    run1_notes = notes_mod.compose("Resequenced after low yield.", run_name,
                                    {"MappedPercent": 91.4}, "2026-09-15")
    report1 = _update([{"json_metadata": {"UID": uid, "Notes": run1_notes}}],
                       existing_notes={uid: "Resequenced after low yield."},
                       run_name=run_name)
    assert not any(f.code == qa.NOTES_WOULD_CLOBBER for f in report1.findings)

    # Run 2 the next day, with a metric value that CHANGED: run 1's tag line
    # is legitimately gone from the composed output, and the value differs
    # too -- the guard must still not read either as data loss.
    run2_notes = notes_mod.compose(run1_notes, run_name,
                                    {"MappedPercent": 93.7}, "2026-09-16")
    report2 = _update([{"json_metadata": {"UID": uid, "Notes": run2_notes}}],
                       existing_notes={uid: run1_notes},
                       run_name=run_name)
    assert not any(f.code == qa.NOTES_WOULD_CLOBBER for f in report2.findings)

    # But a write that genuinely drops the curator's own prose -- not just
    # this run's own block -- must still be caught. Compose run 2's block
    # against nothing, as if the curator's line "Resequenced after low
    # yield." never made it into the new write.
    lossy_notes = notes_mod.compose("", run_name, {"MappedPercent": 93.7}, "2026-09-16")
    report3 = _update([{"json_metadata": {"UID": uid, "Notes": lossy_notes}}],
                       existing_notes={uid: run1_notes},
                       run_name=run_name)
    assert any(f.code == qa.NOTES_WOULD_CLOBBER for f in report3.findings)


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
