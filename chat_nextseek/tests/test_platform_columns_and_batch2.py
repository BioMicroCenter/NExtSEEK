"""Second census batch, plus the platform-derived column mechanism it needed.

Adding pipelines we hold no data for is deliberate: the goal is that a lab arriving
with long-read or isolate data finds the capability already there. That makes unit
tests the only safety net these have — nobody will notice a mistake by running one.

The new mechanism here is PIPELINE_PLATFORM_COLUMNS. genomeassembler names its read
column after the sequencing platform (`ontreads` vs `hifireads`), which the static
alias map cannot express and elicitation cannot supply either — write_samplesheet
runs before configure_run. The platform is in the sample's own metadata, so it is
read from there instead.
"""
import csv
import json
from pathlib import Path

import pytest

from chat_nextseek.seqera.catalog import get_pipeline_entry
from chat_nextseek.seqera.emitter import _platform_from_meta, emit_nfcore_artifacts

TEMPLATES = Path(__file__).resolve().parent.parent / "src/chat_nextseek/reports/templates/nfcore"

# key: (revision, reference_cli_flags, required_columns)
BATCH2 = {
    "denovotranscript":     ("1.2.1", ["genome", "fasta", "gtf"], ["sample", "fastq_1", "fastq_2"]),
    "detaxizer":            ("1.3.0", ["genome"], ["sample", "short_reads_fastq_1", "short_reads_fastq_2"]),
    "fastqrepair":          ("1.0.0", [], ["sample", "fastq_1", "fastq_2"]),
    "genomeassembler":      ("1.1.0", [], ["sample"]),
    "pathogensurveillance": ("1.1.0", [], ["sample_id", "path", "path_2"]),
}


def _read(d):
    with (Path(d) / "samplesheet.csv").open(newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


@pytest.mark.parametrize("key,expected", list(BATCH2.items()))
def test_catalog_matches_pinned_schema(key, expected):
    rev, flags, cols = expected
    e = get_pipeline_entry(key)
    assert e["default_revision"] == rev
    assert e["reference_cli_flags"] == flags
    assert e["required_columns"] == cols


@pytest.mark.parametrize("key", list(BATCH2))
def test_template_loads_and_agrees(key):
    doc = json.loads((TEMPLATES / f"{key}.json").read_text())
    assert doc["pipeline"]["required_columns"] == get_pipeline_entry(key)["required_columns"]
    assert doc["reference_resources"] == []
    for name, spec in doc["params"].items():
        assert "type" in spec and "default" in spec and "steerable" in spec, f"{key}.{name}"


@pytest.mark.parametrize("key", list(BATCH2))
def test_aliases_resolve(key):
    from chat_nextseek.reports.templates_meta import nfcore_pipeline_from_report_type
    assert nfcore_pipeline_from_report_type(f"NFCORE_{key.upper()}") == key


# --- platform detection ------------------------------------------------------

@pytest.mark.parametrize("meta,expected", [
    ({"Sequencer": "Illumina NovaSeq 6000"}, "illumina"),
    ({"Sequencer": "Illumina MiSeq"}, "illumina"),
    ({"Platform": "PacBio Revio"}, "pacbio"),
    ({"SequencingType": "HiFi long read"}, "pacbio"),
    ({"Instrument": "Oxford Nanopore PromethION"}, "nanopore"),
    ({"Sequencer": "MinION"}, "nanopore"),
    ({"Sequencer": "Singular G4"}, "illumina"),
    ({"Sequencer": ""}, ""),
    ({}, ""),
])
def test_platform_is_read_from_any_field_by_value(meta, expected):
    assert _platform_from_meta(meta) == expected


def test_pacbio_wins_over_illumina_when_both_words_appear():
    # A record mentioning an Illumina-sequenced sibling must not drag a HiFi run to
    # the short-read column; the specific platform families are checked first.
    assert _platform_from_meta({
        "Sequencer": "PacBio Revio",
        "Notes": "matched Illumina NovaSeq library exists"}) == "pacbio"


# --- genomeassembler: the read column follows the platform -------------------

@pytest.mark.parametrize("sequencer,column", [
    ("Oxford Nanopore PromethION", "ontreads"),
    ("PacBio Revio", "hifireads"),
])
def test_genomeassembler_puts_reads_in_the_platform_column(tmp_path, sequencer, column):
    out = tmp_path / sequencer.split()[0]
    emit_nfcore_artifacts(
        out, pipeline="genomeassembler", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "Sequencer": sequencer, "Link_PrimaryData": "/net/x/reads.fastq.gz"}})
    header, rows = _read(out)
    assert column in header
    assert rows[0][column] == "/net/x/reads.fastq.gz"
    assert "fastq_1" not in header


def test_genomeassembler_leaves_an_illumina_cohort_visibly_empty(tmp_path):
    # Deliberate: this is a LONG-read assembler. Silently routing short reads into
    # ontreads would assemble them with the wrong error model and not error.
    emit_nfcore_artifacts(
        tmp_path, pipeline="genomeassembler", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "Sequencer": "Illumina NovaSeq 6000", "Link_PrimaryData": "/net/x/r1.fastq.gz"}})
    header, rows = _read(tmp_path)
    assert "ontreads" not in header and "hifireads" not in header
    assert rows[0]["sample"] == "D.SEQ-1-PUB"


def test_genomeassembler_with_unknown_platform_assigns_no_read_column(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="genomeassembler", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {"Link_PrimaryData": "/net/x/reads.fastq.gz"}})
    header, _rows = _read(tmp_path)
    assert "ontreads" not in header and "hifireads" not in header


def test_platform_routing_is_per_row_not_per_run(tmp_path):
    # Two samples, two platforms, one sheet. A per-run answer could not express this,
    # which is the second reason the value comes from metadata rather than the user.
    emit_nfcore_artifacts(
        tmp_path, pipeline="genomeassembler",
        samplesheet_rows=[{"sample": "ONT-1"}, {"sample": "HIFI-1"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={
            "ONT-1": {"Sequencer": "MinION", "Link_PrimaryData": "/net/x/ont.fastq.gz"},
            "HIFI-1": {"Sequencer": "PacBio Sequel II", "Link_PrimaryData": "/net/x/hifi.fastq.gz"}})
    _header, rows = _read(tmp_path)
    by_sample = {r["sample"]: r for r in rows}
    assert by_sample["ONT-1"]["ontreads"] == "/net/x/ont.fastq.gz"
    assert by_sample["ONT-1"]["hifireads"] == ""
    assert by_sample["HIFI-1"]["hifireads"] == "/net/x/hifi.fastq.gz"
    assert by_sample["HIFI-1"]["ontreads"] == ""


# --- pathogensurveillance: platform as a VALUE, plus renames -----------------

def test_pathogensurveillance_renames_and_stamps_sequence_type(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="pathogensurveillance", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "Sequencer": "Illumina MiSeq",
            "Link_PrimaryData": "/net/x/r1.fastq.gz",
            "Link_SecondaryData": "/net/x/r2.fastq.gz"}})
    header, rows = _read(tmp_path)
    assert header[:3] == ["sample_id", "path", "path_2"]
    for gone in ("sample", "fastq_1", "fastq_2"):
        assert gone not in header
    assert rows[0]["path"] == "/net/x/r1.fastq.gz"
    assert rows[0]["path_2"] == "/net/x/r2.fastq.gz"
    # sequence_type is an enum the pipeline uses to pick tooling; derived, not asked.
    assert rows[0]["sequence_type"] == "illumina"


def test_pathogensurveillance_does_not_overwrite_an_explicit_sequence_type(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="pathogensurveillance",
        samplesheet_rows=[{"sample": "D.SEQ-1-PUB", "sequence_type": "bgiseq"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {"Sequencer": "Illumina MiSeq"}})
    _header, rows = _read(tmp_path)
    assert rows[0]["sequence_type"] == "bgiseq"


def test_pathogensurveillance_sequence_type_stays_in_the_pipelines_enum():
    from chat_nextseek.seqera.emitter import _PLATFORM_HINTS
    allowed = {"illumina", "nanopore", "pacbio", "bgiseq"}
    assert {p for p, _ in _PLATFORM_HINTS} <= allowed


# --- detaxizer renames -------------------------------------------------------

def test_detaxizer_uses_short_reads_prefixed_columns(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="detaxizer", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "Link_PrimaryData": "/net/x/r1.fastq.gz", "Link_SecondaryData": "/net/x/r2.fastq.gz"}})
    header, rows = _read(tmp_path)
    assert header[:3] == ["sample", "short_reads_fastq_1", "short_reads_fastq_2"]
    assert "fastq_1" not in header and "fastq_2" not in header
    assert rows[0]["short_reads_fastq_1"] == "/net/x/r1.fastq.gz"


# --- the plain ones still behave ---------------------------------------------

@pytest.mark.parametrize("key", ["denovotranscript", "fastqrepair"])
def test_plain_fastq_pipelines_are_unaffected_by_the_platform_mechanism(tmp_path, key):
    out = tmp_path / key
    emit_nfcore_artifacts(
        out, pipeline=key, samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "Sequencer": "PacBio Revio", "Link_PrimaryData": "/net/x/r1.fastq.gz"}})
    header, rows = _read(out)
    assert header[:2] == ["sample", "fastq_1"]
    assert rows[0]["fastq_1"] == "/net/x/r1.fastq.gz"
