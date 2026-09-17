from NessieAI.ns import reingest_qa as qa
from NessieAI.ns.reingest import report

RUN = "nfcore_rnaseq_fixture"
ARTIFACTS = {"reingest_A.GEX": "/out/reingest_A.GEX.xlsx",
             "reingest_D.SEQ_update": "/out/reingest_D.SEQ_update.xlsx"}


def _soft_report(count=24):
    built = qa.QaReport()
    for i in range(count):
        built.add(qa.Finding(code=qa.UNAPPROVED_ATTRIBUTE, severity=qa.SOFT,
                             sample_type="D.SEQ", attribute="ContamPercent",
                             row_index=i,
                             detail={"raw_key": "Kraken2_bracken_fraction_total_reads",
                                     "example": "SAMPLE_01 = 3.2%", "times_proposed": 1}))
    return built._finalize()


def _single_finding_report(code, severity, **kwargs):
    built = qa.QaReport()
    built.add(qa.Finding(code=code, severity=severity, **kwargs))
    return built._finalize()


def test_n_rows_sharing_a_finding_render_as_one_sentence_not_n_lines():
    # A mutant that returns a fixed string regardless of input would make
    # `few` and `many` identical, so "24" could not be in one and not the
    # other, and the friendly name could not appear the same fixed number of
    # times for both -- this is the count-don't-enumerate rule made testable.
    few = report.render_qa_for_user({"D.SEQ": _soft_report(2)}, ARTIFACTS, RUN)
    many = report.render_qa_for_user({"D.SEQ": _soft_report(24)}, ARTIFACTS, RUN)

    assert few.lower().count("contamination percentage") == 1
    assert many.lower().count("contamination percentage") == 1
    assert "24" in many and "24" not in few
    assert "2" in few
    # The finding renders as one sentence, not N lines: growing the row count
    # from 2 to 24 must not grow the rendered text at all.
    assert len(many.splitlines()) == len(few.splitlines())


def test_the_raw_key_is_not_shown_to_the_user():
    text = report.render_qa_for_user({"D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    assert "Kraken2_bracken_fraction_total_reads" not in text


def test_the_message_says_the_judgement_is_unconfirmed():
    text = report.render_qa_for_user({"D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    lowered = text.lower()
    assert "judgement" in lowered or "judgment" in lowered
    assert "confirm" in lowered


def test_every_flag_offers_a_choice_the_reader_can_make():
    text = report.render_qa_for_user({"D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    assert "Upload as-is" in text
    assert "administrator" in text


def test_a_clean_run_says_so_without_a_check_section():
    text = report.render_qa_for_user({"A.GEX": qa.QaReport()._finalize()}, ARTIFACTS, RUN)
    assert "TO CHECK" not in text.upper()
    # A vacuous negative isn't enough: the run must positively say it's ready.
    assert "ready" in text.lower()


def test_a_hard_reject_names_the_affected_samples_and_the_next_step():
    built = qa.QaReport()
    for i, sample in enumerate(["SAMPLE_22", "SAMPLE_23", "SAMPLE_24"]):
        built.add(qa.Finding(code=qa.UNRESOLVED_UID, severity=qa.HARD,
                             sample_type="D.SEQ", row_index=i,
                             detail={"nfcore_sample": sample,
                                     "fastq_1": f"/net/cluster/fastq/{sample}_R1.fastq.gz"}))
    text = report.render_qa_for_user({"D.SEQ": built._finalize()}, ARTIFACTS, RUN)
    assert "SAMPLE_22" in text and "SAMPLE_24" in text
    assert "blocked" in text.lower()


def test_the_upload_order_puts_children_before_the_backfill():
    reports = {"A.GEX": qa.QaReport()._finalize(), "D.SEQ": _soft_report()}

    text = report.render_qa_for_user(reports, ARTIFACTS, RUN)
    upload = text.split("TO UPLOAD", 1)[1]
    assert upload.index("reingest_A.GEX.xlsx") < upload.index("reingest_D.SEQ_update.xlsx")
    assert "update existing" in text.lower()

    # Prove the order tracks which artifact is actually the backfill, not
    # some property of the reports dict or a fixed sample-type name: swap
    # which sample type owns the "_update" workbook and the rendered order
    # must flip with it. A fixed-string mutant cannot contain both filename
    # orderings for two different artifact dicts.
    swapped_artifacts = {"reingest_A.GEX_update": "/out/reingest_A.GEX_update.xlsx",
                          "reingest_D.SEQ": "/out/reingest_D.SEQ.xlsx"}
    swapped_text = report.render_qa_for_user(reports, swapped_artifacts, RUN)
    swapped_upload = swapped_text.split("TO UPLOAD", 1)[1]
    assert swapped_upload.index("reingest_D.SEQ.xlsx") < swapped_upload.index("reingest_A.GEX_update.xlsx")
    assert "update existing" in swapped_text.lower()


def test_a_blocked_workbook_is_not_offered_for_upload():
    # Important 2: a HARD_REJECT sample type's workbook must not be listed
    # under TO UPLOAD -- it was just called blocked above.
    built = _single_finding_report(qa.UNKNOWN_SAMPLETYPE, qa.HARD, sample_type="D.SEQ")
    text = report.render_qa_for_user({"D.SEQ": built}, ARTIFACTS, RUN)

    upload_section = text.split("TO UPLOAD", 1)[1]
    assert "reingest_D.SEQ_update.xlsx" not in upload_section
    assert "xlsx" not in upload_section
    assert "fix" in upload_section.lower() or "nothing" in upload_section.lower()


def test_a_mix_of_hard_and_soft_still_offers_the_clean_workbook():
    # One sample type hard-rejected, another only soft-flagged: the soft
    # workbook should still be offered for upload even though the run overall
    # reads as blocked.
    hard = _single_finding_report(qa.UNKNOWN_SAMPLETYPE, qa.HARD, sample_type="D.SEQ")
    soft = _soft_report(3)
    artifacts = {"reingest_D.SEQ_update": "/out/reingest_D.SEQ_update.xlsx",
                 "reingest_A.GEX": "/out/reingest_A.GEX.xlsx"}
    text = report.render_qa_for_user({"D.SEQ": hard, "A.GEX": soft}, artifacts, RUN)

    upload_section = text.split("TO UPLOAD", 1)[1]
    assert "reingest_D.SEQ_update.xlsx" not in upload_section
    assert "reingest_A.GEX.xlsx" in upload_section


def test_severity_split_blockers_and_advisories_get_separate_headers():
    # Important 3: a hard finding must render under a blocking header with no
    # "upload as-is" choice nearby, and a soft finding must render under the
    # checking header, where "Upload as-is" is a real option.
    hard = _single_finding_report(qa.UNKNOWN_SAMPLETYPE, qa.HARD, sample_type="Z.BOGUS")
    soft = _soft_report(3)
    text = report.render_qa_for_user({"Z.BOGUS": hard, "D.SEQ": soft}, ARTIFACTS, RUN)

    assert "WHAT IS BLOCKING" in text
    assert "THING" in text  # "ONE THING TO CHECK" / "N THINGS TO CHECK"
    blocking_at = text.index("WHAT IS BLOCKING")
    checking_at = text.index("THING", blocking_at + len("WHAT IS BLOCKING"))
    upload_as_is_at = text.index("Upload as-is")

    assert blocking_at < checking_at < upload_as_is_at
    # Each numbered item names which workbook it concerns.
    assert "Z.BOGUS" in text[blocking_at:checking_at]
    assert "D.SEQ" in text[checking_at:upload_as_is_at]


_NEW_CODE_CASES = [
    (qa.UNKNOWN_SAMPLETYPE, qa.HARD,
     dict(sample_type="Z.BOGUS"),
     ["catalog", "administrator"]),
    (qa.BLANK_PARENT, qa.HARD,
     dict(sample_type="D.SEQ", row_index=0, detail={"reason": "blank Parent"}),
     ["input sample", "which sequencing sample"]),
    (qa.PARENT_UID_NOT_FOUND, qa.HARD,
     dict(sample_type="D.SEQ", row_index=0, detail={"token": "D.SEQ-EXAMPLE-1"}),
     ["does not resolve", "register"]),
    (qa.DUPLICATE_NAME, qa.HARD,
     dict(sample_type="D.SEQ", row_index=0, detail={"name": "SAMPLE_X"}),
     ["already used", "rename"]),
    (qa.SURPRISE_SENTINEL, qa.SOFT,
     dict(sample_type="D.SEQ", row_index=0, attribute="Notes",
          detail={"sentinel": "TODO"}),
     ["placeholder", "confirm"]),
    (qa.UID_MISSING_IN_UPDATE, qa.HARD,
     dict(sample_type="D.SEQ", attribute="UID", row_index=0),
     ["existing sample", "identify"]),
    (qa.UID_PRESENT_IN_NEW, qa.HARD,
     dict(sample_type="D.SEQ", attribute="UID", row_index=0, detail={"uid": "D.SEQ-EXAMPLE-1"}),
     ["cannot be mixed", "which was intended"]),
]


def test_each_new_code_offers_the_prescribed_choice_and_leaks_no_raw_code():
    for code, severity, kwargs, expected_phrases in _NEW_CODE_CASES:
        built = _single_finding_report(code, severity, **kwargs)
        text = report.render_qa_for_user({kwargs["sample_type"]: built}, ARTIFACTS, RUN)
        # Collapse wrapped lines so a phrase split across the prose's own
        # line breaks still matches a contiguous check.
        lowered = " ".join(text.lower().split())
        for phrase in expected_phrases:
            assert phrase.lower() in lowered, f"{code}: missing {phrase!r} in:\n{text}"
        assert code not in text, f"{code}: leaked its own developer code:\n{text}"


def test_notes_would_clobber_distinguishes_not_fetched_from_would_overwrite():
    not_fetched = _single_finding_report(
        qa.NOTES_WOULD_CLOBBER, qa.HARD, sample_type="D.SEQ", attribute="Notes",
        row_index=0, detail={"uid": "D.SEQ-EXAMPLE-1",
                              "reason": "existing Notes not fetched"})
    text = report.render_qa_for_user({"D.SEQ": not_fetched}, ARTIFACTS, RUN)
    lowered = text.lower()
    assert "could not read" in lowered
    assert "nothing was written" in lowered
    assert qa.NOTES_WOULD_CLOBBER not in text

    would_overwrite = _single_finding_report(
        qa.NOTES_WOULD_CLOBBER, qa.HARD, sample_type="D.SEQ", attribute="Notes",
        row_index=0, detail={"uid": "D.SEQ-EXAMPLE-1",
                              "reason": "existing text absent"})
    text2 = report.render_qa_for_user({"D.SEQ": would_overwrite}, ARTIFACTS, RUN)
    lowered2 = text2.lower()
    assert "does not contain" in lowered2
    assert "nothing was written" in lowered2
    assert qa.NOTES_WOULD_CLOBBER not in text2

    # The two reasons must not read identically.
    assert text != text2


def test_unresolved_uid_caps_the_sample_list_instead_of_enumerating_all():
    built = qa.QaReport()
    samples = [f"SAMPLE_{i:03d}" for i in range(40)]
    for i, sample in enumerate(samples):
        built.add(qa.Finding(code=qa.UNRESOLVED_UID, severity=qa.HARD,
                             sample_type="D.SEQ", row_index=i,
                             detail={"nfcore_sample": sample}))
    text = report.render_qa_for_user({"D.SEQ": built._finalize()}, ARTIFACTS, RUN)
    assert "40" in text
    assert "and 30 more" in text
    # Not all 40 sample names are dumped into the chat.
    assert text.count("SAMPLE_0") + text.count("SAMPLE_1") + text.count("SAMPLE_2") + \
        text.count("SAMPLE_3") < 40
