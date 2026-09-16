import json


def test_the_sidecar_survives_a_pipeline_that_renames_sample_away(tmp_path):
    """Regression for the post-remap sidecar bug.

    ampliseq renames `sample` -> `sampleID` on the CSV (PIPELINE_COLUMN_ALIASES).
    Before the fix, emit_nfcore_artifacts built the sidecar from the post-remap
    rows, so `row.get("sample")` was always missing and every nfcore_sample came
    out "" — silently converting "this run processed these samples" into "this
    run processed nothing" for PipelineRun.knows_sample/uid_for. This exercises
    the real emission path (not _write_cohort_sidecar directly) so it fails
    before the emitter.py fix and passes after.
    """
    from chat_nextseek.seqera.emitter import emit_nfcore_artifacts

    rows = [{"sample": "REAL_SAMPLE_A", "accession": "D.SEQ-EXAMPLE-9"}]
    meta = {"D.SEQ-EXAMPLE-9": {"Link_PrimaryData": "/net/bmc-lab/REAL_SAMPLE_A_R1.fastq.gz",
                                "Link_SecondaryData": "/net/bmc-lab/REAL_SAMPLE_A_R2.fastq.gz"}}
    res = emit_nfcore_artifacts(tmp_path, pipeline="ampliseq", samplesheet_rows=rows,
                                resolutions=[], accession_metadata=meta, launch_plan=None,
                                tower_env={}, selector_rationale="t")

    # Confirm this actually exercised the aliased path: the CSV really did rename
    # `sample` away, and the real name is nowhere in the CSV headers/columns.
    sheet_text = (tmp_path / "samplesheet.csv").read_text()
    assert "sampleID" in sheet_text

    cohort = json.loads((tmp_path / "cohort.json").read_text())
    assert len(cohort) == 1
    assert cohort[0]["nfcore_sample"] == "REAL_SAMPLE_A"
    assert cohort[0]["d_seq_uid"] == "D.SEQ-EXAMPLE-9"
    assert cohort[0]["fastq_1"] == "/net/bmc-lab/REAL_SAMPLE_A_R1.fastq.gz"
    assert cohort[0]["fastq_2"] == "/net/bmc-lab/REAL_SAMPLE_A_R2.fastq.gz"
    assert res.samplesheet_row_count == 1


def test_the_sidecar_carries_the_uid_and_the_csv_does_not(tmp_path):
    from chat_nextseek.seqera import emitter

    rows = [{"sample": "CONTROL_REP1", "fastq_1": "/net/cluster/fastq/a_R1.fastq.gz",
             "fastq_2": "/net/cluster/fastq/a_R2.fastq.gz",
             "strandedness": "auto", "accession": "D.SEQ-EXAMPLE-1"}]
    sheet = tmp_path / "samplesheet.csv"
    emitter._write_csv(sheet, rows, ["sample", "fastq_1", "fastq_2", "strandedness"])
    emitter._write_cohort_sidecar(sheet, rows)

    # The UID must NOT reach the samplesheet nf-core consumes.
    assert "D.SEQ-EXAMPLE-1" not in sheet.read_text()

    entries = json.loads((tmp_path / "cohort.json").read_text())
    assert entries == [{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
                        "fastq_1": "/net/cluster/fastq/a_R1.fastq.gz",
                        "fastq_2": "/net/cluster/fastq/a_R2.fastq.gz"}]


def test_a_row_with_no_accession_is_recorded_with_a_null_uid(tmp_path):
    from chat_nextseek.seqera import emitter

    rows = [{"sample": "S1", "fastq_1": "/x_R1.fastq.gz", "fastq_2": ""}]
    sheet = tmp_path / "samplesheet.csv"
    emitter._write_cohort_sidecar(sheet, rows)
    entries = json.loads((tmp_path / "cohort.json").read_text())
    assert entries[0]["d_seq_uid"] is None
    assert entries[0]["nfcore_sample"] == "S1"
