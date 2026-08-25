"""The eight pipelines added 2026-08-05 from the full schema census.

All eight are config-only against their PINNED schemas but have no matching data in
NExtSEEK today. They are catalogued so the capability exists when such a cohort is
registered — which makes these tests unusually important: nobody is going to notice
a mistake by running one. Every fact asserted here was read off the pinned
nextflow_schema.json / assets/schema_input.json on 2026-08-05.

If a default_revision is bumped, re-derive reference_cli_flags and required_columns
from the NEW revision. A stale gtf flag aborts the run at nf-schema validation, and
a stale column name fails samplesheet validation after the job has queued.
"""
import csv
import json
from pathlib import Path

import pytest

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG, get_pipeline_entry
from chat_nextseek.seqera.emitter import emit_nfcore_artifacts
from chat_nextseek.seqera.user_params import missing_user_params, validate_user_params

TEMPLATES = Path(__file__).resolve().parent.parent / "src/chat_nextseek/reports/templates/nfcore"

# key: (revision, reference_cli_flags, required_columns, samplesheet_input_kind)
CENSUS_BATCH = {
    "bacass":          ("2.6.1", [],                          ["ID", "R1", "R2"],            "fastq"),
    "bactmap":         ("1.0.0", [],                          ["sample", "fastq_1", "fastq_2"], "fastq"),
    "pacvar":          ("1.1.0", ["genome", "fasta"],         ["sample", "bam"],             "bam"),
    "viralrecon":      ("3.0.0", ["genome", "fasta"],         ["sample", "fastq_1", "fastq_2"], "fastq"),
    "viralmetagenome": ("1.1.3", [],                          ["sample", "fastq_1"],         "fastq"),
    "viralintegration": ("0.1.1", ["genome", "fasta", "gtf"], ["sample", "fastq_1"],         "fastq"),
    "magmap":          ("1.1.0", [],                          ["sample", "fastq_1"],         "fastq"),
    "metatdenovo":     ("1.4.0", [],                          ["sample", "fastq_1"],         "fastq"),
}


def _template(key):
    return json.loads((TEMPLATES / f"{key}.json").read_text())


@pytest.mark.parametrize("key,expected", list(CENSUS_BATCH.items()))
def test_catalog_entry_matches_the_pinned_schema(key, expected):
    rev, flags, cols, kind = expected
    entry = get_pipeline_entry(key)
    assert entry["default_revision"] == rev, key
    assert entry["reference_cli_flags"] == flags, key
    assert entry["required_columns"] == cols, key
    assert entry["samplesheet_input_kind"] == kind, key


@pytest.mark.parametrize("key", list(CENSUS_BATCH))
def test_template_loads_and_agrees_with_the_catalog(key):
    doc = _template(key)
    entry = get_pipeline_entry(key)
    assert doc["pipeline"]["required_columns"] == entry["required_columns"], key
    assert doc["reference_resources"] == []
    for name, spec in doc["params"].items():
        assert "type" in spec and "default" in spec and "steerable" in spec, f"{key}.{name}"


@pytest.mark.parametrize("key", list(CENSUS_BATCH))
def test_template_declares_no_param_the_schema_rejects(key):
    """A gtf entry in the params menu for a pipeline whose schema has no gtf would be
    emitted and abort the run — the exact bug that made 3 pipelines unlaunchable."""
    doc = _template(key)
    flags = set(get_pipeline_entry(key)["reference_cli_flags"])
    for ref in ("genome", "fasta", "gtf"):
        if ref in doc["params"]:
            assert ref in flags, f"{key} template offers {ref!r} but its schema does not declare it"


@pytest.mark.parametrize("key", list(CENSUS_BATCH))
def test_report_type_aliases_resolve(key):
    from chat_nextseek.reports.templates_meta import (
        get_report_template_basename, nfcore_pipeline_from_report_type, normalize_report_type,
    )
    canonical = f"NFCORE_{key.upper()}"
    for alias in (canonical, f"nf-core {key}", f"{canonical}_SAMPLESHEET"):
        assert normalize_report_type(alias) == canonical, alias
        assert nfcore_pipeline_from_report_type(alias) == key
        assert get_report_template_basename(alias) == f"nfcore/{key}"


# --- elicitation: the values nothing can derive ------------------------------

def test_viralrecon_asks_for_platform_then_protocol_then_primer_set():
    # Staged: primer_set is gated on protocol=amplicon, so it must NOT be demanded
    # up front alongside a question the user has not answered yet.
    first = {s["name"] for s in missing_user_params("viralrecon", {})}
    assert first == {"platform", "protocol"}
    metagenomic = {s["name"] for s in missing_user_params(
        "viralrecon", {"platform": "illumina", "protocol": "metagenomic"})}
    assert metagenomic == set(), "metagenomic libraries have no primer scheme to trim"
    amplicon = {s["name"] for s in missing_user_params(
        "viralrecon", {"platform": "illumina", "protocol": "amplicon"})}
    assert amplicon == {"primer_set"}


def test_viralrecon_rejects_an_out_of_enum_platform():
    errs = validate_user_params("viralrecon", {"platform": "pacbio", "protocol": "amplicon"})
    assert any("platform" in e for e in errs)


@pytest.mark.parametrize("key,param", [
    ("bactmap", "reference"), ("magmap", "genomeinfo"), ("metatdenovo", "orf_caller"),
])
def test_pipelines_that_must_ask_the_user_do_ask(key, param):
    assert param in {s["name"] for s in missing_user_params(key, {})}
    assert missing_user_params(key, {param: "something"}) == []


def test_metatdenovo_orf_caller_is_enum_checked():
    # prodigal vs transdecoder is prokaryote vs eukaryote; a typo must not pass through.
    assert validate_user_params("metatdenovo", {"orf_caller": "prodigel"})
    assert validate_user_params("metatdenovo", {"orf_caller": "transdecoder"}) == []


@pytest.mark.parametrize("key", [k for k in CENSUS_BATCH
                                 if k not in ("bactmap", "magmap", "metatdenovo", "viralrecon")])
def test_the_rest_need_no_elicitation(key):
    assert missing_user_params(key, {}) == []


# --- emitted samplesheets ----------------------------------------------------

def _read(tmp_path):
    with (tmp_path / "samplesheet.csv").open(newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def test_bacass_renames_every_column_it_needs_to(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="bacass", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "Link_PrimaryData": "/net/x/iso_R1.fastq.gz",
            "Link_SecondaryData": "/net/x/iso_R2.fastq.gz"}})
    header, rows = _read(tmp_path)
    assert header[:3] == ["ID", "R1", "R2"]
    # The standard names must be gone, or bacass fails validation on unknown columns.
    for gone in ("sample", "fastq_1", "fastq_2"):
        assert gone not in header
    assert rows[0]["ID"] == "D.SEQ-1-PUB"
    assert rows[0]["R1"] == "/net/x/iso_R1.fastq.gz"
    assert rows[0]["R2"] == "/net/x/iso_R2.fastq.gz"


def test_pacvar_uses_the_bam_path_with_its_own_column_names(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="pacvar", samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {
            "File_PrimaryData": "/net/x/m84001_hifi_reads.bam",
            "File_SecondaryData": "/net/x/m84001_hifi_reads.bam.bai"}})
    header, rows = _read(tmp_path)
    assert header[:2] == ["sample", "bam"]
    assert "mapped" not in header and "index" not in header
    assert rows[0]["bam"] == "/net/x/m84001_hifi_reads.bam"
    assert rows[0]["pbi"] == "/net/x/m84001_hifi_reads.bam.bai"


def test_pacvar_proves_input_kind_and_sample_type_are_independent():
    # bamtofastq reads a BAM produced by an analysis (A.ALN); pacvar reads a BAM the
    # sequencer produced (D.SEQ). Same input kind, different lineage position.
    assert get_pipeline_entry("pacvar")["samplesheet_input_kind"] == "bam"
    assert get_pipeline_entry("pacvar")["accepted_leaf_sample_types"] == ["D.SEQ"]
    assert get_pipeline_entry("bamtofastq")["samplesheet_input_kind"] == "bam"
    assert get_pipeline_entry("bamtofastq")["accepted_leaf_sample_types"] == ["A.ALN"]


@pytest.mark.parametrize("key", ["viralmetagenome", "magmap", "metatdenovo", "viralintegration"])
def test_plain_fastq_pipelines_emit_standard_columns(tmp_path, key):
    out = tmp_path / key
    emit_nfcore_artifacts(
        out, pipeline=key, samplesheet_rows=[{"sample": "D.SEQ-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"D.SEQ-1-PUB": {"Link_PrimaryData": "/net/x/a_R1.fastq.gz"}})
    header, rows = _read(out)
    assert header[0] == "sample"
    assert "fastq_1" in header
    assert rows[0]["fastq_1"] == "/net/x/a_R1.fastq.gz"


# --- the standing invariant --------------------------------------------------

def test_every_catalog_entry_still_declares_its_reference_flags():
    for key, entry in NFCORE_PIPELINE_CATALOG.items():
        flags = entry.get("reference_cli_flags")
        assert isinstance(flags, list), key
        assert set(flags) <= {"genome", "fasta", "gtf"}, key
