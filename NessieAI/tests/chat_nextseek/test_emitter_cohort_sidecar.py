import json


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
