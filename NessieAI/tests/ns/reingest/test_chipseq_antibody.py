"""chipseq peak rows carry their antibody, and each antibody's consensus is its own row.

nf-core/chipseq calls peaks per antibody: its samplesheet's `antibody` column names the
mark or factor, and it writes one consensus peak set per antibody under
`consensus/<antibody>/`. The harvest keeps a sample's own samplesheet cells
(`SampleRecord.samplesheet`), the map reads `$samplesheet.antibody` as a row default, and
the consensus rule is parented only by its antibody's samples (`parents_by_samplesheet`).

A.CHRM's Antibody, Name, File_SecondaryData, Link_SecondaryData and Notes exist on the
local stack only (README merge checklist): with a catalog that lacks them, as dev and prod
are today, the row defaults are dropped and companions go to the build report, as before.
"""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, mapper, maps

pytestmark = pytest.mark.django_db

ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
WIDENED = ("Name", "Antibody", "File_SecondaryData", "Link_SecondaryData", "Notes")
# next_name_ordinal stands in for the catalog lookup the build injects for @row_name.
LOCAL = {"has_attribute": lambda st, a: a in ATTRS.get(st, ()),
         "next_name_ordinal": lambda sample_type, prefix: 1}
# What dev and prod hold until A.CHRM is widened there.
NOT_WIDENED = {**LOCAL, "has_attribute": lambda st, a: a in ATTRS.get(st, ()) and not (
    st == "A.CHRM" and a in WIDENED)}


def _fq(name):
    return f"/net/fq/{name}_R1.fastq.gz"


SHEET = (
    "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate\n"
    f"SPT5,{_fq('spt5_a')},,1,SPT5,INPUT,1\n"
    f"SPT5,{_fq('spt5_b')},,2,SPT5,INPUT,2\n"
    f"K27AC,{_fq('k27_a')},,1,H3K27ac,INPUT,1\n"
    f"K27AC,{_fq('k27_b')},,2,H3K27ac,INPUT,2\n"
    f"INPUT,{_fq('input_a')},,1,,,\n"
    f"INPUT,{_fq('input_b')},,2,,,\n")
UIDS = {_fq("spt5_a"): "D.SEQ-1", _fq("spt5_b"): "D.SEQ-2", _fq("k27_a"): "D.SEQ-3",
        _fq("k27_b"): "D.SEQ-4", _fq("input_a"): "D.SEQ-5", _fq("input_b"): "D.SEQ-6"}
LIB = "bwa/merged_library"
CONSENSUS = f"{LIB}/macs3/broad_peak/consensus"


def _paths():
    paths = []
    for name in ("SPT5_REP1", "SPT5_REP2", "K27AC_REP1", "K27AC_REP2", "INPUT_REP1", "INPUT_REP2"):
        paths += [f"{LIB}/{name}.mLb.clN.sorted.bam", f"{LIB}/{name}.mLb.clN.sorted.bam.bai",
                  f"{LIB}/bigwig/{name}.mLb.clN.bigWig"]
    for name in ("SPT5_REP1", "SPT5_REP2", "K27AC_REP1", "K27AC_REP2"):
        paths.append(f"{LIB}/macs3/broad_peak/{name}_peaks.broadPeak")
    for antibody in ("SPT5", "H3K27ac"):
        paths += [f"{CONSENSUS}/{antibody}/{antibody}.consensus_peaks.bed",
                  f"{CONSENSUS}/{antibody}/{antibody}.consensus_peaks.featureCounts.txt",
                  f"{CONSENSUS}/{antibody}/{antibody}.consensus_peaks.saf"]
    return paths + ["multiqc/broad_peak/multiqc_report.html"]


def _harvest(root):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_chipseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/chipseq: v2.1.0\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "params_2026-10-27_00-00-00.json").write_text(
        json.dumps({"genome": "GRCh38", "aligner": "bwa", "narrow_peak": False,
                    "trace_report_suffix": "2026-10-27_09-00-00"}))
    (root / "samplesheet.csv").write_text(SHEET)
    return harvest.harvest_local(
        str(root), inventory=[{"path": p, "bytes": 1} for p in _paths()],
        lookup_by_fastq=lambda path, types=None: [UIDS[path]] if path in UIDS else [],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})


def _by(result, rule_id):
    return {(r.nfcore_sample or r.attributes["File_PrimaryData"].value):
            {k: v.value for k, v in r.attributes.items()}
            for r in result.rows if r.rule_id == rule_id}


def test_the_harvest_keeps_each_replicates_own_samplesheet_cells_but_no_paths(tmp_path):
    run = _harvest(tmp_path)
    cells = {s.nfcore_sample: s.samplesheet for s in run.samples}
    assert cells["K27AC_REP2"]["antibody"] == "H3K27ac"
    assert cells["K27AC_REP2"]["control"] == "INPUT"
    assert "antibody" not in cells["INPUT_REP1"]  # blank cells are left out
    assert not any(v.startswith("/") for c in cells.values() for v in c.values())


def test_each_peak_row_names_its_antibody(tmp_path):
    result = mapper.apply(_harvest(tmp_path), maps.load("chipseq"), context=LOCAL)
    peaks = _by(result, "peaks_broad")
    assert {name: row["Antibody"] for name, row in peaks.items()} == {
        "SPT5_REP1": "SPT5", "SPT5_REP2": "SPT5", "K27AC_REP1": "H3K27ac", "K27AC_REP2": "H3K27ac"}
    bigwig = _by(result, "bigwig")
    assert bigwig["K27AC_REP1"]["Antibody"] == "H3K27ac"
    # The input controls were sequenced without an antibody: no value, never a guess.
    assert "Antibody" not in bigwig["INPUT_REP1"]
    assert all(row["Name"].startswith("chrm_2026-10-27_") for row in [*peaks.values(), *bigwig.values()])
    # A.ALN has no Antibody: the row default is dropped there.
    assert all("Antibody" not in row for row in _by(result, "aln").values())


def test_each_antibodys_consensus_is_parented_by_its_own_samples_only(tmp_path):
    result = mapper.apply(_harvest(tmp_path), maps.load("chipseq"), context=LOCAL)
    consensus = _by(result, "consensus")
    assert sorted(consensus) == ["H3K27ac.consensus_peaks.bed", "SPT5.consensus_peaks.bed"]
    spt5, k27 = consensus["SPT5.consensus_peaks.bed"], consensus["H3K27ac.consensus_peaks.bed"]
    assert spt5["Parent"] == "D.SEQ-1;D.SEQ-2"
    assert k27["Parent"] == "D.SEQ-3;D.SEQ-4"
    assert (spt5["Antibody"], k27["Antibody"]) == ("SPT5", "H3K27ac")
    # Its read counts are a companion: A.CHRM now has a secondary slot and its link.
    assert k27["File_SecondaryData"] == "H3K27ac.consensus_peaks.featureCounts.txt"
    assert k27["Link_SecondaryData"].endswith(
        f"{CONSENSUS}/H3K27ac/H3K27ac.consensus_peaks.featureCounts.txt")
    assert k27["DataType"] == "BED"
    assert not [w for w in result.warnings if "consensus" in w]


def test_a_consensus_no_sample_names_gets_no_parent_and_a_warning(tmp_path):
    run = _harvest(tmp_path)
    for sample in run.samples:
        sample.samplesheet.pop("antibody", None)
    result = mapper.apply(run, maps.load("chipseq"), context=LOCAL)
    assert all("Parent" not in row for row in _by(result, "consensus").values())
    assert any("no sample's samplesheet antibody is 'SPT5'" in w for w in result.warnings)


def test_where_a_chrm_is_not_widened_nothing_it_lacks_is_sent(tmp_path):
    """dev and prod today: no Antibody, Name or secondary slot on A.CHRM. The row
    defaults are dropped, and a companion goes to the build report, as before."""
    result = mapper.apply(_harvest(tmp_path), maps.load("chipseq"), context=NOT_WIDENED)
    for rule_id in ("peaks_broad", "bigwig", "consensus"):
        for row in _by(result, rule_id).values():
            assert not set(row) & set(WIDENED), (rule_id, row)
    assert any("no Notes on A.CHRM; also in run: "
               f"{CONSENSUS}/SPT5/SPT5.consensus_peaks.featureCounts.txt" in w
               for w in result.warnings)
    # The consensus rows are still parented by their own antibody's samples.
    assert _by(result, "consensus")["SPT5.consensus_peaks.bed"]["Parent"] == "D.SEQ-1;D.SEQ-2"


def test_a_samplesheet_ref_with_no_sample_reads_the_value_every_sample_shares():
    from NessieAI.ns.reingest import manifest
    run = manifest.RunManifest(run_dir="/RUN", samples=[
        manifest.SampleRecord(nfcore_sample="A_REP1", samplesheet={"antibody": "CTCF"}),
        manifest.SampleRecord(nfcore_sample="A_REP2", samplesheet={"antibody": "CTCF"})])
    assert maps.resolve_ref("$samplesheet.antibody", run) == "CTCF"
    assert maps.resolve_ref("$samplesheet.antibody", run, run.samples[0]) == "CTCF"
    assert maps.resolve_ref("$samplesheet", run, run.samples[0]) is None
    run.samples[1].samplesheet["antibody"] = "H3K4me3"
    assert maps.resolve_ref("$samplesheet.antibody", run) is None


def test_parents_by_samplesheet_is_for_per_file_rules_only():
    with pytest.raises(ValueError, match="parents_by_samplesheet is for per_file rules"):
        maps.OutputRule.model_validate({
            "id": "x", "candidates": ["a/*.bed"], "sample_type": "A.CHRM",
            "cardinality": "per_sample", "primary_data": True,
            "parents_by_samplesheet": "antibody"})
