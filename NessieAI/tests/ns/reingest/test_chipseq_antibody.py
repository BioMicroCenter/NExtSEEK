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
import os
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
    # A run-wide row's File_PrimaryData carries its run folder ("<run>/SPT5.consensus_peaks.bed");
    # key it by its file's own name.
    return {(r.nfcore_sample or os.path.basename(r.attributes["File_PrimaryData"].source_file)):
            {k: v.value for k, v in r.attributes.items()}
            for r in result.rows if r.rule_id == rule_id}


def test_the_harvest_keeps_each_replicates_antibody_but_no_blank_cell(tmp_path):
    run = _harvest(tmp_path)
    cells = {s.nfcore_sample: s.samplesheet for s in run.samples}
    assert cells["K27AC_REP2"] == {"antibody": "H3K27ac"}
    assert cells["INPUT_REP1"] == {}  # blank cells are left out


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
    consensus = _by(result, "consensus_broad")
    assert sorted(consensus) == ["H3K27ac.consensus_peaks.bed", "SPT5.consensus_peaks.bed"]
    spt5, k27 = consensus["SPT5.consensus_peaks.bed"], consensus["H3K27ac.consensus_peaks.bed"]
    assert spt5["File_PrimaryData"] == f"{tmp_path.name}/SPT5.consensus_peaks.bed"
    assert spt5["Parent"] == "D.SEQ-1;D.SEQ-2"
    assert k27["Parent"] == "D.SEQ-3;D.SEQ-4"
    assert (spt5["Antibody"], k27["Antibody"]) == ("SPT5", "H3K27ac")
    # Its read counts are a companion: A.CHRM now has a secondary slot and its link.
    assert k27["File_SecondaryData"] == "H3K27ac.consensus_peaks.featureCounts.txt"
    assert k27["Link_SecondaryData"].endswith(
        f"{CONSENSUS}/H3K27ac/H3K27ac.consensus_peaks.featureCounts.txt")
    assert k27["DataType"] == "BED"
    assert not [w for w in result.warnings if "consensus" in w]


def test_a_consensus_no_sample_names_is_left_out(tmp_path):
    run = _harvest(tmp_path)
    for sample in run.samples:
        sample.samplesheet.pop("antibody", None)
    result = mapper.apply(run, maps.load("chipseq"), context=LOCAL)
    assert _consensus_rows(result) == []
    assert sorted(f["value"] for f in result.stale_files) == ["H3K27ac", "SPT5"]


def test_where_a_chrm_is_not_widened_nothing_it_lacks_is_sent(tmp_path):
    """dev and prod today: no Antibody, Name or secondary slot on A.CHRM. The row
    defaults are dropped, and a companion goes to the build report, as before."""
    result = mapper.apply(_harvest(tmp_path), maps.load("chipseq"), context=NOT_WIDENED)
    for rule_id in ("peaks_broad", "bigwig", "consensus_broad"):
        for row in _by(result, rule_id).values():
            assert not set(row) & set(WIDENED), (rule_id, row)
    assert any("no Notes on A.CHRM; also in run: "
               f"{CONSENSUS}/SPT5/SPT5.consensus_peaks.featureCounts.txt" in w
               for w in result.warnings)
    # The consensus rows are still parented by their own antibody's samples.
    assert _by(result, "consensus_broad")["SPT5.consensus_peaks.bed"]["Parent"] == "D.SEQ-1;D.SEQ-2"


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


# --- A run folder reused by two launches: broad SPT5+H3K27ac, then narrow SPT5 only. ---

NARROW_SHEET = (
    "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate\n"
    f"SPT5,{_fq('spt5_a')},,1,SPT5,INPUT,1\n"
    f"SPT5,{_fq('spt5_b')},,2,SPT5,INPUT,2\n"
    f"INPUT,{_fq('input_a')},,1,,,\n"
    f"INPUT,{_fq('input_b')},,2,,,\n")


def _two_launch_run(root, sheet=NARROW_SHEET):
    """The second (narrow, SPT5 only) launch's samplesheet over a folder that still
    holds the first (broad, SPT5 and H3K27ac) launch's peaks and consensus sets."""
    _harvest(root)
    (root / "samplesheet.csv").write_text(sheet)
    narrow = f"{LIB}/macs3/narrow_peak"
    extra = [f"{narrow}/SPT5_REP1_peaks.narrowPeak", f"{narrow}/SPT5_REP2_peaks.narrowPeak",
             f"{narrow}/consensus/SPT5/SPT5.consensus_peaks.bed",
             f"{narrow}/consensus/SPT5/SPT5.consensus_peaks.featureCounts.txt"]
    return harvest.harvest_local(
        str(root), inventory=[{"path": p, "bytes": 1} for p in _paths() + extra],
        lookup_by_fastq=lambda path, types=None: [UIDS[path]] if path in UIDS else [],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})


def _consensus_rows(result):
    return [r for r in result.rows if (r.rule_id or "").startswith("consensus")]


def test_a_consensus_for_an_antibody_this_run_never_named_is_left_out_and_reported(tmp_path):
    result = mapper.apply(_two_launch_run(tmp_path), maps.load("chipseq"), context=LOCAL)
    files = sorted(r.attributes["File_PrimaryData"].source_file for r in _consensus_rows(result))
    assert not any("H3K27ac" in f for f in files)
    [stale] = result.stale_files
    assert stale["path"] == f"{CONSENSUS}/H3K27ac/H3K27ac.consensus_peaks.bed"
    assert (stale["column"], stale["value"]) == ("antibody", "H3K27ac")


def test_both_launches_spt5_consensus_rows_are_kept_named_apart_and_flagged(tmp_path):
    result = mapper.apply(_two_launch_run(tmp_path), maps.load("chipseq"), context=LOCAL)
    spt5 = sorted(r.attributes["File_PrimaryData"].value for r in _consensus_rows(result))
    assert spt5 == [f"{tmp_path.name}/broad_peak/consensus/SPT5/SPT5.consensus_peaks.bed",
                    f"{tmp_path.name}/narrow_peak/consensus/SPT5/SPT5.consensus_peaks.bed"]
    flagged = {(e["sample"], e["sample_type"]) for e in result.alternative_rows}
    assert ("SPT5", "A.CHRM") in flagged
    entry = next(e for e in result.alternative_rows
                 if e["sample"] == "SPT5" and {f["rule_id"] for f in e["files"]}
                 == {"consensus_broad", "consensus_narrow"})
    assert len(entry["files"]) == 2


def test_the_report_tells_the_curator_which_files_were_left_out(tmp_path):
    from NessieAI.ns.reingest import report
    result = mapper.apply(_two_launch_run(tmp_path), maps.load("chipseq"), context=LOCAL)
    text = report.render_qa_for_user({}, {}, "run", stale_files=result.stale_files)
    assert "NOT FROM THIS RUN" in text
    assert f"{CONSENSUS}/H3K27ac/H3K27ac.consensus_peaks.bed" in text
    assert "antibody H3K27ac" in text


def test_a_consensus_whose_samples_did_not_resolve_is_kept_and_said(tmp_path):
    run = _harvest(tmp_path)
    for sample in run.samples:
        if sample.samplesheet.get("antibody") == "H3K27ac":
            sample.uid_resolution, sample.d_seq_uid = "unresolved", None
    result = mapper.apply(run, maps.load("chipseq"), context=LOCAL)
    k27 = _by(result, "consensus_broad")["H3K27ac.consensus_peaks.bed"]
    assert "Parent" not in k27
    assert result.stale_files == []
    assert any("K27AC_REP1, K27AC_REP2" in w and "pending ruling" in w for w in result.warnings)


def test_a_consensus_says_which_of_its_samples_it_leaves_out(tmp_path):
    run = _harvest(tmp_path)
    for sample in run.samples:
        if sample.nfcore_sample == "SPT5_REP2":
            sample.uid_resolution, sample.d_seq_uid = "ambiguous", None
    result = mapper.apply(run, maps.load("chipseq"), context=LOCAL)
    assert _by(result, "consensus_broad")["SPT5.consensus_peaks.bed"]["Parent"] == "D.SEQ-1"
    assert any("SPT5_REP2" in w and "only the others as Parent" in w for w in result.warnings)


# --- Only the columns a map reads are kept. ---

def _harvest_sheet(root, pipeline, version, sheet):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / f"nf_core_{pipeline}_software_mqc_versions.yml").write_text(
        f"Workflow:\n  nf-core/{pipeline}: v{version}\n  Nextflow: 25.10.2\n")
    (root / "samplesheet.csv").write_text(sheet)
    return harvest.harvest_local(str(root), inventory=[])


def test_chipseq_keeps_only_the_antibody_column(tmp_path):
    run = _harvest(tmp_path)
    assert {s.nfcore_sample: s.samplesheet for s in run.samples}["K27AC_REP1"] == {"antibody": "H3K27ac"}


@pytest.mark.parametrize("pipeline, version, sheet", [
    ("sarek", "3.10.0", "patient,sex,status,sample,lane,fastq_1,fastq_2\n"
                        "P1,XX,1,T1,L1,/x/t1_1.fq.gz,/x/t1_2.fq.gz\n"),
    ("differentialabundance", "1.5.0", "sample,fastq_1,fastq_2,condition,batch,age\n"
                                       "S1,/x/s1.fq.gz,,treated,b1,64\n"),
])
def test_a_map_that_reads_no_samplesheet_column_keeps_none(tmp_path, pipeline, version, sheet):
    run = _harvest_sheet(tmp_path, pipeline, version, sheet)
    assert run.samples and all(s.samplesheet == {} for s in run.samples)
    assert maps.load(pipeline).samplesheet_columns() == set()


def test_an_alternative_group_may_be_per_file_but_not_mixed():
    def rule(rule_id, cardinality):
        return {"id": rule_id, "candidates": [f"{rule_id}/*.bed"], "sample_type": "A.CHRM",
                "cardinality": cardinality, "primary_data": True, "optional": True,
                "alternative_group": "g"}
    maps.PipelineMap.model_validate({"pipeline": "nf-core/x", "map_version": 2,
                                     "outputs": [rule("a", "per_file"), rule("b", "per_file")]})
    with pytest.raises(ValueError, match="mix per_sample and per_file"):
        maps.PipelineMap.model_validate({"pipeline": "nf-core/x", "map_version": 2,
                                         "outputs": [rule("a", "per_file"), rule("b", "per_sample")]})


def test_chipseqs_two_consensus_rules_are_alternatives():
    groups = {r.id: r.alternative_group for r in maps.load("chipseq").outputs
              if r.cardinality == "per_file"}
    assert groups == {"consensus_narrow": "consensus", "consensus_broad": "consensus"}
