"""Launching variantbenchmarking 1.6.1, oncoanalyser 3.0.0, createpanelrefs 1.1.0 and rnadnavar 1.0.0:
the samplesheet each one's pinned files ask for, the checks that refuse what the pipeline would,
and their param-atlas rules. Every rule cites the pinned file in fixtures/nfcore/."""
import csv
import json
import warnings

import pytest

from chat_nextseek.pipeline import agent_tools as at
from chat_nextseek.seqera.emitter import (
    _createpanelrefs_rows,
    _oncoanalyser_rows,
    _rnadnavar_rows,
    _vcf_from_meta,
    emit_nfcore_artifacts,
)
from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param, load_param_atlas


def _emit(tmp_path, pipeline, rows, meta):
    emit_nfcore_artifacts(tmp_path, pipeline=pipeline, samplesheet_rows=rows, resolutions=[],
                          launch_plan=None, tower_env=None, accession_metadata=meta)
    with open(tmp_path / "samplesheet.csv", newline="") as fh:
        header = fh.readline().strip().split(",")
    with open(tmp_path / "samplesheet.csv", newline="") as fh:
        return header, list(csv.DictReader(fh))


# --- variantbenchmarking: one row per A.VCF, its own called VCF -------------------------------

def test_vcf_from_meta_takes_the_records_own_called_vcf_never_its_gvcf():
    meta = {"File_PrimaryData": "/net/r/hc/S1.haplotypecaller.filtered.vcf.gz",
            "File_SecondaryData": "/net/r/hc/S1.haplotypecaller.g.vcf.gz;/net/r/hc/S1.vcf.gz.tbi",
            "Link_PrimaryData": ""}
    assert _vcf_from_meta(meta) == "/net/r/hc/S1.haplotypecaller.filtered.vcf.gz"
    assert _vcf_from_meta({"File_PrimaryData": "/net/r/S1.g.vcf.gz"}) == ""
    assert _vcf_from_meta({"File_SecondaryData": "/net/r/S1.vcf.gz"}) == ""


def test_variantbenchmarking_sheet_opens_with_id_test_vcf_caller(tmp_path):
    """docs/usage.md: the first three columns are id,test_vcf,caller; id is unique per test VCF."""
    rows = [{"sample": "A.VCF-1", "caller": "haplotypecaller", "fastq_1": "/x/r1.fastq.gz"},
            {"sample": "A.VCF-2", "caller": "deepvariant"}]
    meta = {"A.VCF-1": {"File_PrimaryData": "/net/r/1.vcf.gz", "Link_PrimaryData": "/net/r/1_R1.fastq.gz"},
            "A.VCF-2": {"File_PrimaryData": "/net/r/2.deepvariant.vcf.gz"}}
    header, out = _emit(tmp_path, "variantbenchmarking", rows, meta)
    assert header[:3] == ["id", "test_vcf", "caller"] and "fastq_1" not in header
    assert [r["test_vcf"] for r in out] == ["/net/r/1.vcf.gz", "/net/r/2.deepvariant.vcf.gz"]


@pytest.mark.parametrize("rows, paths, expect", [
    ([{"id": "A", "caller": "haplotypecaller"}], False, []),
    ([{"id": "A", "caller": "haplotypecaller"}, {"id": "A", "caller": "deepvariant"}], False, ["appears twice"]),
    ([{"id": "A", "caller": "HaplotypeCaller"}], False, ["lowercase"]),
    ([{"id": "A", "caller": "deepvariant", "test_vcf": ""}], True, ["no VCF path"]),
    ([{"id": "A", "caller": "deepvariant", "test_vcf": "/n/a.g.vcf.gz"}], True, ["is a gVCF"]),
    ([{"id": "A", "caller": "deepvariant", "test_vcf": "/n/a.vcf.gz"}], True, []),
])
def test_variantbenchmarking_sheet_errors(rows, paths, expect):
    """assets/schema_input.json: id unique (uniqueEntries), caller ^\\S+$, test_vcf a VCF/BCF."""
    errors = at._variantbenchmarking_sheet_errors(rows, check_paths=paths)
    assert len(errors) == len(expect) and all(e in got for e, got in zip(expect, errors)), errors


# --- oncoanalyser: long format, one row per FASTQ pair ----------------------------------------

def test_oncoanalyser_rows_are_its_long_format():
    """usage.md "FASTQ" and lib/Utils.groovy: info library_id and lane, filepath R1;R2."""
    rows = [{"sample": "D.SEQ-T", "patient": "PAT-1", "sample_type": "Tumor",
             "fastq_1": "/n/T_S1_L002_R1.fastq.gz", "fastq_2": "/n/T_S1_L002_R2.fastq.gz"},
            {"sample": "D.SEQ-N", "patient": "PAT-1", "sample_type": "normal",
             "fastq_1": "/n/N_R1.fastq.gz", "fastq_2": "/n/N_R2.fastq.gz"},
            {"sample": "D.SEQ-N", "patient": "PAT-1", "sample_type": "normal",
             "fastq_1": "/n/N2_R1.fastq.gz", "fastq_2": "/n/N2_R2.fastq.gz"}]
    out, header = _oncoanalyser_rows(rows)
    assert header == ["group_id", "subject_id", "sample_id", "sample_type", "sequence_type",
                      "filetype", "info", "filepath"]
    assert out[0]["info"] == "library_id:D.SEQ-T;lane:002" and out[0]["sample_type"] == "tumor"
    assert [r["info"] for r in out[1:]] == ["library_id:D.SEQ-N;lane:001", "library_id:D.SEQ-N;lane:002"]
    assert out[1]["filepath"] == "/n/N_R1.fastq.gz;/n/N_R2.fastq.gz" and out[1]["subject_id"] == "PAT-1"
    assert at._oncoanalyser_sheet_errors(out) == []


def test_oncoanalyser_sheet_errors_refuse_a_lone_normal_and_single_end():
    out, _ = _oncoanalyser_rows([{"sample": "N", "patient": "P", "sample_type": "normal",
                                  "fastq_1": "/n/N_R1.fastq.gz", "fastq_2": ""}])
    errors = at._oncoanalyser_sheet_errors(out)
    assert any("paired-end FASTQ only" in e for e in errors)
    assert any("no tumor sample" in e for e in errors)


def test_oncoanalyser_is_refused_until_its_reference_bundle_is_staged():
    """conf/hmf_genomes.config: every ref_data_* defaults to data.oncoanalyser.com."""
    from chat_nextseek.luria.run_script import LURIA_GENOME_RESOURCES
    assert "GRCh38_hmf" not in LURIA_GENOME_RESOURCES
    assert "not staged" in at._oncoanalyser_reference_errors()[0]


# --- createpanelrefs: bam/bai or cram/crai by file type ---------------------------------------

def test_createpanelrefs_rows_put_each_file_under_its_own_column():
    """assets/schema_input.json at 1.1.0: oneOf bam | cram."""
    out, header = _createpanelrefs_rows([
        {"sample": "N1", "mapped": "/n/N1.bam", "file_type": "bam", "index": "/n/N1.bam.bai"},
        {"sample": "N2", "mapped": "/n/N2.cram", "file_type": "cram", "index": ""}])
    assert header == ["sample", "bam", "bai", "cram"]
    assert out[0] == {"sample": "N1", "bam": "/n/N1.bam", "bai": "/n/N1.bam.bai"}
    assert out[1] == {"sample": "N2", "cram": "/n/N2.cram"}


def test_createpanelrefs_sheet_has_no_file_type_column(tmp_path):
    header, out = _emit(tmp_path, "createpanelrefs", [{"sample": "N1"}],
                        {"N1": {"Link_PrimaryData": "/net/x/N1.bam", "File_SecondaryData": "/net/x/N1.bam.bai"}})
    assert header == ["sample", "bam", "bai"] and out[0]["bam"] == "/net/x/N1.bam"


@pytest.mark.parametrize("params, rows, expect", [
    ({"tools": "mutect2"}, [{"sample": "N", "bam": "/n/N.bam"}], []),
    ({}, [{"sample": "N", "bam": "/n/N.bam"}], ["tools is required"]),
    ({"tools": "mutect2,gens"}, [{"sample": "N", "bam": "/n/N.bam"}], ["gens is not offered"]),
    ({"tools": "cnvkit", "cnvkit_method": "hybrid"}, [{"sample": "N", "bam": "/n/N.bam"}], ["needs cnvkit_targets"]),
    ({"tools": "mutect2"}, [{"sample": "N"}], ["no BAM or CRAM"]),
])
def test_createpanelrefs_run_errors(params, rows, expect):
    errors = at._createpanelrefs_run_errors(params, rows)
    assert len(errors) == len(expect) and all(e in got for e, got in zip(expect, errors)), errors


# --- rnadnavar: FASTQ or BAM rows, a lane and a status on each ---------------------------------

def test_rnadnavar_fastq_cohort_gets_a_lane_on_every_row(tmp_path):
    """subworkflows/local/samplesheet_to_channel/main.nf takes a row only with a lane."""
    rows = [{"sample": "RNA", "patient": "P", "status": 2}, {"sample": "N", "patient": "P", "status": 0}]
    meta = {u: {"Link_PrimaryData": f"/net/x/{u}_R1.fastq.gz;/net/x/{u}_R2.fastq.gz"} for u in ("RNA", "N")}
    header, out = _emit(tmp_path, "rnadnavar", rows, meta)
    assert header == ["patient", "status", "sample", "lane", "fastq_1", "fastq_2"]
    assert [(r["status"], r["lane"]) for r in out] == [("2", "1"), ("0", "1")]


def test_rnadnavar_takes_a_bam_when_the_library_has_no_fastq(tmp_path):
    """docs/usage.md "Starting from different steps": mapping takes patient,status,sample,lane,bam,bai."""
    header, out = _emit(tmp_path, "rnadnavar", [{"sample": "T", "patient": "P", "status": 1}],
                        {"T": {"Link_PrimaryData": "/net/gdc/T.bam", "File_SecondaryData": "/net/gdc/T.bam.bai"}})
    assert header == ["patient", "status", "sample", "lane", "bam", "bai"]
    assert out[0]["bam"] == "/net/gdc/T.bam" and out[0]["bai"] == "/net/gdc/T.bam.bai"


def test_rnadnavar_rows_number_lanes_within_a_sample():
    out, _ = _rnadnavar_rows([{"sample": "S", "patient": "P", "status": "0", "fastq_1": "a"},
                              {"sample": "S", "patient": "P", "status": "0", "fastq_1": "b"}])
    assert [r["lane"] for r in out] == ["1", "2"]


@pytest.mark.parametrize("rows, expect", [
    ([{"sample": "R", "patient": "P", "status": "2"}, {"sample": "N", "patient": "P", "status": "0"}], []),
    ([{"sample": "R", "patient": "P", "status": "2"}], ["no normal DNA"]),
    ([{"sample": "R", "patient": "P", "status": ""}], ["must be 0"]),
    ([{"sample": "R", "patient": "", "status": "0"}], ["no patient"]),
    ([{"sample": "N", "patient": "P", "status": "0"}], ["no tumour row"]),
])
def test_rnadnavar_status_errors(rows, expect):
    """docs/usage.md: status 0 normal DNA, 1 tumour DNA, 2 tumour RNA; an RNA tumour needs a DNA
    normal (validateSamplesheetComposition)."""
    errors = at._rnadnavar_status_errors(rows)
    assert len(errors) == len(expect) and all(e in got for e, got in zip(expect, errors)), errors


def test_rnadnavar_file_errors_refuse_single_end_and_fileless_rows():
    errors = at._rnadnavar_file_errors([{"sample": "A", "fastq_1": "a.fastq.gz"}, {"sample": "B"}])
    assert "paired-end" in errors[0] and "neither FASTQ nor BAM" in errors[1]


# --- the param-atlas rules ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def atlas():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return load_param_atlas()


def _rule(atlas, pipeline, name):
    return atlas["pipelines"][pipeline]["params"][name]


@pytest.mark.parametrize("pipeline, name, signals, value, verdict", [
    ("variantbenchmarking", "caller", {"Software": "GATK4 HaplotypeCaller"}, "haplotypecaller", "derived_uncorroborated"),
    ("variantbenchmarking", "caller", {}, "unknown", "defaulted"),
    ("variantbenchmarking", "truth_id", {"Name": "NA12878 WGS rep1", "Type": "GM12878 lymphoblastoid cell line"}, "HG001", "corroborated"),
    ("variantbenchmarking", "variant_type", {"Software": "Manta (germline structural variants)"}, "structural", "derived_uncorroborated"),
    ("oncoanalyser", "sample_type", {"Type": "Primary tumour tissue"}, "tumor", "derived_uncorroborated"),
    ("oncoanalyser", "sample_type", {"Type": "Blood (matched normal)"}, "normal", "derived_uncorroborated"),
    ("createpanelrefs", "cnvkit_method", {"LibraryStrategy": "WGS", "LibrarySelection": "RANDOM"}, "wgs", "corroborated"),
    ("createpanelrefs", "cnvkit_method", {"LibraryStrategy": "WXS"}, "hybrid", "derived_uncorroborated"),
    ("rnadnavar", "wes", {"LibraryStrategy": "WXS", "LibrarySource": "GENOMIC"}, "true", "derived_uncorroborated"),
    ("rnadnavar", "wes", {"LibraryStrategy": "RNA-Seq", "LibrarySource": "TRANSCRIPTOMIC"}, None, "not_applicable"),
    ("rnadnavar", "read_length", {"F_bp": "101", "R_bp": "101", "LibrarySource": "TRANSCRIPTOMIC"}, "101", "corroborated"),
])
def test_param_rules(atlas, pipeline, name, signals, value, verdict):
    out = evaluate_data_driven_param(_rule(atlas, pipeline, name), signals)
    assert (out["value"], out["verdict"]) == (value, verdict), out


def test_rnadnavar_status_is_not_a_param_atlas_rule(atlas):
    """status is tissue times molecule (docs/usage.md), which one rule cannot read; the
    samplesheet check refuses a missing or impossible one instead."""
    assert "status" not in atlas["pipelines"]["rnadnavar"]["params"]


# --- the Luria launch profiles -----------------------------------------------------------------

def test_rnadnavar_gets_the_gatk_bundle_and_the_gencode_gtf():
    """rnadnavar 1.0.0 builds its STAR index from the FASTA and a GTF; the GATK bundle has none."""
    from chat_nextseek.luria.run_script import luria_resource_params, luria_run_genome
    genome = luria_run_genome("nf-core/rnadnavar", "GRCh38")
    params = luria_resource_params("nf-core/rnadnavar", genome, "/refs")
    assert genome == "GATK.GRCh38"
    assert params["gtf"] == "/refs/gencode.v46.basic.annotation.gtf.gz"
    assert params["dbsnp"].startswith("/refs/GATK.GRCh38/") and "intervals" not in params
    assert luria_resource_params("nf-core/rnadnavar", genome, "/refs", {"gtf": "/mine.gtf"}).get("gtf") is None


def test_sarek_is_still_given_no_gtf():
    from chat_nextseek.luria.run_script import luria_resource_params
    assert "gtf" not in luria_resource_params("nf-core/sarek", "GATK.GRCh38", "/refs")


@pytest.mark.parametrize("pipeline", ["nf-core/createpanelrefs", "nf-core/variantbenchmarking"])
def test_fai_is_passed_under_the_name_these_pipelines_declare(pipeline):
    """createpanelrefs 1.1.0 and variantbenchmarking 1.6.1 name the FASTA index `fai`."""
    from chat_nextseek.luria.run_script import luria_resource_params
    assert luria_resource_params(pipeline, "GATK.GRCh38", "/refs")["fai"].endswith(".fasta.fai")
