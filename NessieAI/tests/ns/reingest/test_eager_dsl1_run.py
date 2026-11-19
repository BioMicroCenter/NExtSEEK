"""A Nessie-launched nf-core/eager 2.5.3 run reaches the map (doc-only: no run fixture exists).

eager 2.5.3 is a DSL1 release on the nf-core template of its day, so three things a modern
release gives the harvest are different:

- its samplesheet is `samplesheet.tsv`, whose sample column is `Sample_Name`;
- it writes no params JSON, and run.sh launches it as `NXF_VER=22.10.8 nextflow run ...`, so the
  launch params must be read from a command line that opens with an environment assignment;
- its software versions are `pipeline_info/software_versions.csv` (`name<TAB>version` lines,
  bin/scrape_software_versions.py at 2.5.3), not a software_mqc_versions.yml.
"""
import pytest

from NessieAI.ns.reingest import harvest, mapper, maps, parsers

pytestmark = pytest.mark.django_db

SHEET = ("Sample_Name\tLibrary_ID\tLane\tColour_Chemistry\tSeqType\tOrganism\tStrandedness\t"
         "UDG_Treatment\tR1\tR2\tBAM\n"
         "D.SEQ-1\tD.SEQ-1\t8\t2\tPE\tNA\tdouble\thalf\t/net/x/a_R1.fastq.gz\t/net/x/a_R2.fastq.gz\tNA\n"
         "D.SEQ-2\tD.SEQ-2\t1\t4\tSE\tNA\tdouble\tnone\t/net/x/b_R1.fastq.gz\tNA\tNA\n")

VERSIONS = ("nf-core/eager\tv2.5.3\nNextflow\tv22.10.8\nFastQC\tv0.11.9\n"
            "MultiQC\tv1.13\nkraken\t<span style=\"color:#999999;\">N/A</span>\n")

RUN_SH = ("#!/bin/bash\n"
          "NXF_VER=22.10.8 nextflow run nf-core/eager -r 2.5.3 -profile singularity \\\n"
          "    -c luria.config \\\n"
          "    -params-file params.yml \\\n"
          "    --input samplesheet.tsv \\\n"
          "    --outdir . \\\n"
          "    --genome GRCh38 --fasta /refs/GRCh38.fa.gz --igenomes_ignore \\\n"
          "    -w /work/x \\\n"
          "    -resume\n")

INVENTORY = [
    {"path": "deduplication/D.SEQ-1/D.SEQ-1_rmdup.bam", "bytes": 10},
    {"path": "deduplication/D.SEQ-1/D.SEQ-1_rmdup.bam.bai", "bytes": 1},
    {"path": "deduplication/D.SEQ-2/D.SEQ-2_rmdup.bam", "bytes": 10},
    {"path": "deduplication/D.SEQ-2/D.SEQ-2_rmdup.bam.csi", "bytes": 1},
    {"path": "genotyping/D.SEQ-1.unifiedgenotyper.vcf.gz", "bytes": 5},
    {"path": "genotyping/D.SEQ-2.unifiedgenotyper.vcf.gz", "bytes": 5},
    {"path": "multiqc/multiqc_report.html", "bytes": 5},
]


def _run(root):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "software_versions.csv").write_text(VERSIONS)
    (root / "pipeline_info" / "execution_trace_2026-11-19_10-00-00.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    (root / "samplesheet.tsv").write_text(SHEET)
    (root / "params.yml").write_text("mapper: bwaaln\nrun_genotyping: true\ngenotyping_tool: ug\n")
    (root / "run.sh").write_text(RUN_SH)
    return harvest.harvest_local(str(root), inventory=INVENTORY, run_dir="/RUN",
                                 lookup_by_fastq=lambda p, types=None: [])


def test_the_dsl1_versions_csv_names_the_pipeline_and_release(tmp_path):
    run = _run(tmp_path / "run")
    assert run.pipeline.name == "nf-core/eager"
    assert run.pipeline.version == "v2.5.3"
    assert run.pipeline.nextflow_version == "v22.10.8"
    assert run.software_versions["FastQC"] == "v0.11.9"
    assert "kraken" not in run.software_versions  # N/A placeholders are not versions


def test_the_tsv_samplesheet_names_the_samples(tmp_path):
    run = _run(tmp_path / "run")
    assert sorted(s.nfcore_sample for s in run.samples) == ["D.SEQ-1", "D.SEQ-2"]


def test_a_csv_samplesheet_still_wins_over_a_tsv_one(tmp_path):
    root = tmp_path / "run"
    (root).mkdir()
    (root / "samplesheet.csv").write_text("sample,fastq_1\nD.SEQ-9,/net/x/z_R1.fastq.gz\n")
    (root / "samplesheet.tsv").write_text("sample\tfastq_1\nD.SEQ-8\t/net/x/y_R1.fastq.gz\n")
    run = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert [s.nfcore_sample for s in run.samples] == ["D.SEQ-9"]


def test_params_are_read_from_a_command_that_sets_nxf_ver(tmp_path):
    run = _run(tmp_path / "run")
    assert run.params["genome"] == "GRCh38"
    assert run.params["mapper"] == "bwaaln"


def test_launch_params_read_past_an_environment_assignment():
    params = parsers.parse_launch_params(None, RUN_SH)
    assert params["genome"] == "GRCh38" and params["input"] == "samplesheet.tsv"


def test_the_map_files_each_sample_its_alignment_and_genotypes(tmp_path):
    run = _run(tmp_path / "run")
    result = mapper.apply(run, maps.load("eager"), context={"has_attribute": lambda st, a: True})
    assert not [w for w in result.warnings if "outside" in w and "applies_to_versions" in w]
    aln = sorted((r for r in result.rows if r.rule_id == "aln"), key=lambda r: r.nfcore_sample)
    assert [r.nfcore_sample for r in aln] == ["D.SEQ-1", "D.SEQ-2"]
    attrs = aln[0].attributes
    assert attrs["Genome"].value == "GRCh38" and attrs["Aligner"].value == "bwaaln"
    assert attrs["DataType"].value == "BAM"
    vcf = [r for r in result.rows if r.rule_id == "vcf_unifiedgenotyper"]
    assert sorted(r.nfcore_sample for r in vcf) == ["D.SEQ-1", "D.SEQ-2"]
    assert not [r for r in result.rows if r.rule_id in ("vcf_haplotypecaller", "vcf_freebayes")]
