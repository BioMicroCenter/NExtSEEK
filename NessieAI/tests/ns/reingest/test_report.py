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


def test_n_rows_sharing_a_finding_render_as_one_sentence_not_n_lines():
    text = report.render_qa_for_user({"D.SEQ": _soft_report(24)}, ARTIFACTS, RUN)
    assert text.count("ContamPercent") <= 2
    assert "24" in text


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
    text = report.render_qa_for_user({"A.GEX": qa.QaReport()._finalize(),
                                      "D.SEQ": _soft_report()}, ARTIFACTS, RUN)
    assert text.index("reingest_A.GEX.xlsx") < text.index("reingest_D.SEQ_update.xlsx")
    assert "update existing" in text.lower()
