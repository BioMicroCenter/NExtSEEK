"""The fetch pre-stage must fill the read column the samplesheet ACTUALLY has.

Found by generating a real detaxizer samplesheet and reading it, not by a test —
which is why these exist now.

The bug: `fill_rows` hardcoded fastq_1/fastq_2. Five catalogued pipelines rename
those columns (ampliseq, mag, bacass, detaxizer, pathogensurveillance). For any of
them the sequence was:

  1. every row looks unfilled, because the sheet has no `fastq_1` at all
  2. fetchngs downloads the reads — real time, real storage
  3. fill_rows writes row["fastq_1"]
  4. the write-back uses extrasaction="ignore" with the ORIGINAL header, so that
     key is silently discarded
  5. the pipeline launches with empty read columns and no error anywhere

Worst affected was `ampliseq`, which covers 1,179 of our 2,057 sequencing samples
and is the next pipeline slated for a cluster test.
"""
import csv

import pytest

from chat_nextseek.luria import fetchngs_helpers as F
from chat_nextseek.luria.run_script import render_run_script
from chat_nextseek.luria.submitter import _fetch_read_columns, _sheet_needs_fetch
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.emitter import PIPELINE_COLUMN_ALIASES

RENAMED = {
    "ampliseq": ("forwardReads", "reverseReads"),
    "mag": ("short_reads_1", "short_reads_2"),
    "bacass": ("R1", "R2"),
    "detaxizer": ("short_reads_fastq_1", "short_reads_fastq_2"),
    "pathogensurveillance": ("path", "path_2"),
}


@pytest.mark.parametrize("key,cols", list(RENAMED.items()))
def test_submitter_resolves_the_real_read_columns(key, cols):
    assert _fetch_read_columns(key) == cols


def test_standard_pipelines_are_unchanged():
    for key in ("rnaseq", "scrnaseq", "sarek", "seqinspector"):
        assert _fetch_read_columns(key) == ("fastq_1", "fastq_2")


def test_every_fastq_pipeline_with_a_renamed_read_column_is_covered():
    """Guards the list above against a new alias being added and forgotten."""
    renamed = {k for k, al in PIPELINE_COLUMN_ALIASES.items()
               if al.get("fastq_1")
               and NFCORE_PIPELINE_CATALOG[k]["samplesheet_input_kind"] == "fastq"}
    assert renamed == set(RENAMED), (
        "a pipeline renames fastq_1 but is not covered here — check the fetch pre-stage")


@pytest.mark.parametrize("key,cols", list(RENAMED.items()))
def test_fill_writes_into_the_column_the_sheet_has(tmp_path, key, cols):
    r1, r2 = cols
    cache = tmp_path / "cache" / "fastq"
    cache.mkdir(parents=True)
    (cache / "SRX1_SRR1_1.fastq.gz").write_text("")
    (cache / "SRX1_SRR1_2.fastq.gz").write_text("")

    rows = [{"sample": "S1", r1: "", r2: "", "accession": "SRR1"}]
    filled, missing = F.fill_rows(rows, str(tmp_path / "cache"), r1, r2)
    assert missing == []
    assert filled[0][r1].endswith("SRX1_SRR1_1.fastq.gz")
    assert filled[0][r2].endswith("SRX1_SRR1_2.fastq.gz")
    # The standard names must NOT be introduced — they would be dropped on write-back.
    assert "fastq_1" not in filled[0] and "fastq_2" not in filled[0]


def test_fill_refuses_rather_than_silently_dropping_the_value(tmp_path, capsys):
    """If the column really isn't there, fail loudly. Downloading and then writing
    nowhere is the failure this whole module exists to prevent."""
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,short_reads_fastq_1,accession\nS1,,SRR1\n")
    rc = F._main_fill(str(tmp_path), "fastq_1", "fastq_2", str(sheet))
    assert rc == 1
    assert "not in the samplesheet header" in capsys.readouterr().err


def test_a_renamed_sheet_round_trips_through_the_cli(tmp_path):
    cache = tmp_path / "cache" / "fastq"
    cache.mkdir(parents=True)
    (cache / "SRX1_SRR1_1.fastq.gz").write_text("")
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sampleID,forwardReads,reverseReads,accession\nS1,,,SRR1\n")

    assert F._main_ids("forwardReads", str(sheet), str(tmp_path / "ids.csv")) == 0
    assert (tmp_path / "ids.csv").read_text().split() == ["SRR1"]

    assert F._main_fill(str(tmp_path / "cache"), "forwardReads", "reverseReads", str(sheet)) == 0
    row = next(iter(csv.DictReader(sheet.open(newline=""))))
    assert row["forwardReads"].endswith("SRX1_SRR1_1.fastq.gz")
    assert row["reverseReads"] == ""          # single-end in the cache
    assert set(row) == {"sampleID", "forwardReads", "reverseReads", "accession"}


def test_needs_fetch_uses_the_right_column(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    # forwardReads already populated -> nothing to fetch. Reading `fastq_1` instead
    # would see a missing column, call the row blank, and re-download it.
    sheet.write_text("sampleID,forwardReads,accession\nS1,/net/x/a.fastq.gz,SRR1\n")
    assert _sheet_needs_fetch(str(sheet), "forwardReads") is False
    assert _sheet_needs_fetch(str(sheet), "fastq_1") is True   # the old, wrong behaviour


@pytest.mark.parametrize("key,cols", list(RENAMED.items()))
def test_run_sh_passes_the_columns_to_the_helper(key, cols):
    sh = render_run_script(
        job_name="t", pipeline=f"nf-core/{key}", revision="1.0.0", run_dir="/net/x/r",
        work_dir="/net/x/w", singularity_cache="/net/x/c", genome="GRCh38", resources=None,
        needs_fetch=True, fastq_cache="/net/x/fc", reference_cli_flags=[],
        fetch_read_columns=cols)
    assert f"fetchngs_helpers.py ids {cols[0]}" in sh
    assert f'fetchngs_helpers.py fill "$CACHE" {cols[0]} {cols[1]}' in sh


def test_column_names_are_validated_before_interpolation():
    # They land in a shell script; a command substitution must not survive.
    with pytest.raises(ValueError):
        render_run_script(
            job_name="t", pipeline="nf-core/rnaseq", revision="1.0.0", run_dir="/net/x/r",
            work_dir="/net/x/w", singularity_cache="/net/x/c", genome="GRCh38",
            resources=None, needs_fetch=True, fastq_cache="/net/x/fc",
            reference_cli_flags=[], fetch_read_columns=("fastq_1; rm -rf /", "fastq_2"))


def test_default_behaviour_is_unchanged_for_standard_pipelines():
    sh = render_run_script(
        job_name="t", pipeline="nf-core/rnaseq", revision="3.18.0", run_dir="/net/x/r",
        work_dir="/net/x/w", singularity_cache="/net/x/c", genome="GRCh38", resources=None,
        needs_fetch=True, fastq_cache="/net/x/fc", reference_cli_flags=["genome"])
    assert "fetchngs_helpers.py ids fastq_1" in sh
    assert 'fetchngs_helpers.py fill "$CACHE" fastq_1 fastq_2' in sh
