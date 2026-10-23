"""sarek's QC rules against nf-core's AWS evidence (G-7).

The committed fixtures have an empty cohort, so their snapshots never show the
D.SEQ backfill. Here the AWS run is harvested with a fake lookup that resolves
both samples to a D.SEQ parent, so MappedPercent, MismatchPercent and
DuplicationPercent are read from the rows the map means: samtools from
`<sample>.recal`, GATK MarkDuplicates from `<sample>.md`.
"""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, mapper, maps

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "sarek"
AWS = ROOT / "3.10.0-aws-test_full_germline_ncbench_agilent"
ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
UIDS = {"NA12878_200M": "D.SEQ-TEST-200M", "NA12878_75M": "D.SEQ-TEST-75M"}


def _harvest(folder, lookup=None, types=None):
    inventory = json.loads((folder / "inventory.json").read_text())
    return harvest.harvest_local(str(folder), inventory=inventory, run_dir="/RUN",
                                 lookup_by_fastq=lookup, sample_type_lookup=types)


def _apply(run):
    return mapper.apply(run, maps.load("sarek"),
                        context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})


def _resolved_aws_run():
    def lookup(path, types=None):
        return [uid for name, uid in UIDS.items() if f"_{name}_R" in path]
    return _harvest(AWS, lookup=lookup, types=lambda uids: {u: "D.SEQ" for u in uids})


def test_the_backfill_reads_samtools_from_recal_and_duplication_from_md():
    result = _apply(_resolved_aws_run())
    backfill = {r.uid: r for r in result.rows if r.sample_type == "D.SEQ"}
    assert sorted(backfill) == sorted(UIDS.values())

    big = backfill["D.SEQ-TEST-200M"].attributes
    small = backfill["D.SEQ-TEST-75M"].attributes
    # multiqc_general_stats.txt: <sample>.recal has 99.976..., <sample>.md 99.932...
    assert big["MappedPercent"].value == pytest.approx(99.97601640754037)
    assert small["MappedPercent"].value == pytest.approx(99.97605166868944)
    assert big["MappedPercent"].raw_key == "samtools_flagstat_stats-reads_mapped_percent"
    # PERCENT_DUPLICATION exists only on <sample>.md, as a percentage.
    assert big["DuplicationPercent"].value == pytest.approx(15.0851)
    assert small["DuplicationPercent"].value == pytest.approx(6.2656)
    # samtools' error rate from <sample>.recal (<sample>.md has 0.216...), already
    # a percentage in general stats: multiqc_samtools_stats.txt has 0.001871683.
    assert big["MismatchPercent"].value == pytest.approx(0.1871683)
    assert small["MismatchPercent"].value == pytest.approx(0.1871112)
    assert big["MismatchPercent"].raw_key == "samtools_flagstat_stats-error_rate"
    assert big["Genome"].value == "GATK.GRCh38"


def test_the_aln_rows_name_their_d_seq_parent_once_resolved():
    result = _apply(_resolved_aws_run())
    parents = {r.nfcore_sample: r.attributes["Parent"].value
               for r in result.rows if r.rule_id == "aln"}
    assert parents == UIDS


@pytest.mark.parametrize("folder", sorted(p for p in ROOT.iterdir() if (p / "inventory.json").is_file()),
                         ids=lambda p: p.name)
def test_every_folded_qc_key_is_read_or_ruled_out(folder):
    # deliberately_unmapped is complete: nothing reaches the review queue.
    assert _apply(_harvest(folder)).unmapped == []


def _notes(result, rule_id, sample):
    (row,) = [r for r in result.rows if r.rule_id == rule_id and r.nfcore_sample == sample]
    return row.attributes["File_PrimaryData"].value, row.attributes["Notes"].value


@pytest.mark.parametrize("rule_id, primary, companions", [
    # G-4: the unfiltered VCF is the second candidate, so it rides on the row.
    ("vcf_haplotypecaller", "NA12878_75M.haplotypecaller.filtered.vcf.gz",
     ["NA12878_75M.haplotypecaller.filtered.vcf.gz.tbi", "NA12878_75M.haplotypecaller.vcf.gz",
      "NA12878_75M.haplotypecaller.vcf.gz.tbi"]),
    ("vcf_freebayes", "NA12878_75M.freebayes.filtered.vcf.gz",
     ["NA12878_75M.freebayes.filtered.vcf.gz.tbi", "NA12878_75M.freebayes.vcf.gz",
      "NA12878_75M.freebayes.vcf.gz.tbi"]),
    # G-3: a gVCF is a companion of its caller's VCF row.
    ("vcf_deepvariant", "NA12878_75M.deepvariant.vcf.gz",
     ["NA12878_75M.deepvariant.vcf.gz.tbi", "NA12878_75M.deepvariant.g.vcf.gz",
      "NA12878_75M.deepvariant.g.vcf.gz.tbi"]),
    ("vcf_strelka", "NA12878_75M.strelka.variants.vcf.gz",
     ["NA12878_75M.strelka.variants.vcf.gz.tbi", "NA12878_75M.strelka.genome.vcf.gz",
      "NA12878_75M.strelka.genome.vcf.gz.tbi"]),
])
def test_each_callers_row_carries_its_other_files(rule_id, primary, companions):
    file, notes = _notes(_apply(_harvest(AWS)), rule_id, "NA12878_75M")
    assert file == primary
    for name in companions:
        assert f"/{name}" in notes, (name, notes)


def test_the_unfiltered_vcf_wins_when_the_filtered_one_is_absent():
    run = _harvest(AWS)
    run.outputs = [o for o in run.outputs if ".haplotypecaller.filtered." not in o.path]
    file, _ = _notes(_apply(run), "vcf_haplotypecaller", "NA12878_75M")
    assert file == "NA12878_75M.haplotypecaller.vcf.gz"


def test_the_aln_rule_takes_only_crams_as_its_datatype_says():
    (aln,) = [r for r in maps.load("sarek").outputs if r.id == "aln"]
    assert aln.attributes["DataType"] == "CRAM"
    globs = [*aln.candidates, *(c.glob for c in aln.companions)]
    assert not [g for g in globs if ".bam" in g]
