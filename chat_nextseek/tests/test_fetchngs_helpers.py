from pathlib import Path
from chat_nextseek.luria.fetchngs_helpers import needs_fetch_accessions, fill_rows


def test_needs_fetch_accessions_selects_blank_fastq_srr_rows():
    rows = [
        {"sample": "a", "accession": "SRR1", "fastq_1": ""},
        {"sample": "b", "accession": "SRR2", "fastq_1": "/net/bmc/b.fastq.gz"},  # already local
        {"sample": "c", "accession": "", "fastq_1": ""},                          # no accession
        {"sample": "d", "accession": "SRR1", "fastq_1": ""},                      # dup
    ]
    assert needs_fetch_accessions(rows) == ["SRR1"]


def test_needs_fetch_accessions_rejects_non_bare_ids():
    assert needs_fetch_accessions([{"accession": "../evil", "fastq_1": ""}]) == []


def test_fill_rows_paired(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRR1_1.fastq.gz").write_text("x")
    (tmp_path / "fastq" / "SRR1_2.fastq.gz").write_text("x")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR1", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRR1_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("SRR1_2.fastq.gz")


def test_fill_rows_single_end(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRR9.fastq.gz").write_text("x")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR9", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == [] and rows[0]["fastq_1"].endswith("SRR9.fastq.gz") and rows[0]["fastq_2"] == ""


def test_fill_rows_reports_missing(tmp_path):
    (tmp_path / "fastq").mkdir()
    rows, missing = fill_rows([{"sample": "a", "accession": "SRRX", "fastq_1": ""}], str(tmp_path))
    assert missing == ["SRRX"]


def test_fill_rows_leaves_local_rows_untouched(tmp_path):
    rows, missing = fill_rows([{"sample": "a", "fastq_1": "/net/bmc/a.fastq.gz"}], str(tmp_path))
    assert missing == [] and rows[0]["fastq_1"] == "/net/bmc/a.fastq.gz"


def test_fill_rows_matches_experiment_prefixed_fetchngs_names(tmp_path):
    # nf-core/fetchngs writes <experiment>_<run>_1.fastq.gz, NOT <run>_1.fastq.gz —
    # confirmed on Luria (SRX6818190_SRR10085181_1.fastq.gz).
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRX6818190_SRR10085181_1.fastq.gz").write_text("x")
    (tmp_path / "fastq" / "SRX6818190_SRR10085181_2.fastq.gz").write_text("x")
    rows, missing = fill_rows(
        [{"sample": "a", "accession": "SRR10085181", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRX6818190_SRR10085181_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("SRX6818190_SRR10085181_2.fastq.gz")


def test_fill_rows_experiment_prefixed_single_end(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRX999_SRR8.fastq.gz").write_text("x")
    rows, missing = fill_rows(
        [{"sample": "a", "accession": "SRR8", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRX999_SRR8.fastq.gz") and rows[0]["fastq_2"] == ""
