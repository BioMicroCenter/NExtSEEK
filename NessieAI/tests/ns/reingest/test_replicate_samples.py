"""Replicated samplesheets: one sample per replicate, and a group's merged files on the group.

nf-core/atacseq and nf-core/chipseq repeat `sample` across biological replicates and
name each replicate's outputs `<sample>_REP<replicate>.*` (their bin/check_samplesheet.py
builds that id). atacseq also merges a sample's replicates into `<sample>.mRp.*`. The
harvest keys a replicate-keyed map's samples by `<sample>_REP<replicate>`, each with its
own D.SEQ parent, and the mapper gives a group-named file to the group, parented by every
replicate (README "What a map may contain", `replicate_samples`). Every other pipeline
keys by `sample` alone, as before.
"""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, manifest, mapper, maps

pytestmark = pytest.mark.django_db

ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
CONTEXT = {"has_attribute": lambda sample_type, attribute: attribute in ATTRS.get(sample_type, ())}


def _run(root, pipeline, version, sheet):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / f"nf_core_{pipeline}_software_mqc_versions.yml").write_text(
        f"Workflow:\n  nf-core/{pipeline}: v{version}\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text("task_id\tstatus\n1\tCOMPLETED\n")
    (root / "samplesheet.csv").write_text(sheet)


def _harvest(root, paths, uids):
    return harvest.harvest_local(
        str(root), inventory=[{"path": p, "bytes": 1} for p in paths],
        lookup_by_fastq=lambda path, types=None: [uids[path]] if path in uids else [],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})


def _rows(result, rule_id):
    return sorted((r.nfcore_sample, r.attributes["File_PrimaryData"].value,
                   r.attributes["Parent"].value if "Parent" in r.attributes else None)
                  for r in result.rows if r.rule_id == rule_id)


def _fq(name):
    return f"/net/fq/{name}_R1.fastq.gz"


CHIP_SHEET = (
    "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate\n"
    f"SPT5,{_fq('spt5_a')},,1,SPT5,INPUT,1\n"
    f"SPT5,{_fq('spt5_b')},,2,SPT5,INPUT,2\n"
    f"INPUT,{_fq('input_a')},,1,,,\n"
    f"INPUT,{_fq('input_b')},,2,,,\n")
CHIP_UIDS = {_fq("spt5_a"): "D.SEQ-1", _fq("spt5_b"): "D.SEQ-2",
             _fq("input_a"): "D.SEQ-3", _fq("input_b"): "D.SEQ-4"}


def _chip_paths():
    lib = "bwa/merged_library"
    paths = []
    for name in ("SPT5_REP1", "SPT5_REP2", "INPUT_REP1", "INPUT_REP2"):
        paths += [f"{lib}/{name}.mLb.clN.sorted.bam", f"{lib}/{name}.mLb.clN.sorted.bam.bai",
                  f"{lib}/bigwig/{name}.mLb.clN.bigWig"]
    paths += [f"{lib}/macs3/broad_peak/SPT5_REP1_peaks.broadPeak",
              f"{lib}/macs3/broad_peak/SPT5_REP2_peaks.broadPeak",
              f"{lib}/macs3/broad_peak/consensus/SPT5/SPT5.consensus_peaks.bed",
              "multiqc/broad_peak/multiqc_report.html"]
    return paths


def test_chipseq_replicates_are_their_own_samples_each_with_its_own_parent(tmp_path):
    _run(tmp_path, "chipseq", "2.1.0", CHIP_SHEET)
    got = _harvest(tmp_path, _chip_paths(), CHIP_UIDS)
    assert [(s.nfcore_sample, s.d_seq_uid, s.uid_resolution) for s in got.samples] == [
        ("SPT5_REP1", "D.SEQ-1", manifest.RESOLUTION_FASTQ_EXACT),
        ("SPT5_REP2", "D.SEQ-2", manifest.RESOLUTION_FASTQ_EXACT),
        ("INPUT_REP1", "D.SEQ-3", manifest.RESOLUTION_FASTQ_EXACT),
        ("INPUT_REP2", "D.SEQ-4", manifest.RESOLUTION_FASTQ_EXACT)]
    assert got.replicate_groups == {"SPT5": ["SPT5_REP1", "SPT5_REP2"],
                                    "INPUT": ["INPUT_REP1", "INPUT_REP2"]}


def test_chipseq_every_replicate_file_is_its_own_row_and_inputs_get_no_peaks(tmp_path):
    _run(tmp_path, "chipseq", "2.1.0", CHIP_SHEET)
    result = mapper.apply(_harvest(tmp_path, _chip_paths(), CHIP_UIDS),
                          maps.load("chipseq"), context=CONTEXT)
    assert _rows(result, "aln") == [
        ("INPUT_REP1", "INPUT_REP1.mLb.clN.sorted.bam", "D.SEQ-3"),
        ("INPUT_REP2", "INPUT_REP2.mLb.clN.sorted.bam", "D.SEQ-4"),
        ("SPT5_REP1", "SPT5_REP1.mLb.clN.sorted.bam", "D.SEQ-1"),
        ("SPT5_REP2", "SPT5_REP2.mLb.clN.sorted.bam", "D.SEQ-2")]
    assert _rows(result, "peaks_broad") == [
        ("SPT5_REP1", "SPT5_REP1_peaks.broadPeak", "D.SEQ-1"),
        ("SPT5_REP2", "SPT5_REP2_peaks.broadPeak", "D.SEQ-2")]
    assert _rows(result, "bigwig") == [
        ("INPUT_REP1", "INPUT_REP1.mLb.clN.bigWig", "D.SEQ-3"),
        ("INPUT_REP2", "INPUT_REP2.mLb.clN.bigWig", "D.SEQ-4"),
        ("SPT5_REP1", "SPT5_REP1.mLb.clN.bigWig", "D.SEQ-1"),
        ("SPT5_REP2", "SPT5_REP2.mLb.clN.bigWig", "D.SEQ-2")]
    # Each replicate's own D.SEQ now takes the QC backfill (Link_QC).
    assert sorted((r.uid, sorted(r.attributes)) for r in result.rows if r.uid) == [
        (f"D.SEQ-{i}", ["Link_QC"]) for i in range(1, 5)]


ATAC_SHEET = (
    "sample,fastq_1,fastq_2,replicate\n"
    f"WT,{_fq('wt_a')},,1\n"
    f"WT,{_fq('wt_b')},,2\n"
    f"KO,{_fq('ko_a')},,1\n"
    f"KO,{_fq('ko_b')},,2\n"
    f"HET,{_fq('het_a')},,1\n")
ATAC_UIDS = {_fq("wt_a"): "D.SEQ-1", _fq("wt_b"): "D.SEQ-2", _fq("ko_a"): "D.SEQ-3",
             _fq("ko_b"): "D.SEQ-4", _fq("het_a"): "D.SEQ-5"}


def _atac_paths(merged_replicates=True):
    lib, rep = "bwa/merged_library", "bwa/merged_replicate"
    paths = []
    for name in ("WT_REP1", "WT_REP2", "KO_REP1", "KO_REP2", "HET_REP1"):
        paths += [f"{lib}/{name}.mLb.clN.sorted.bam", f"{lib}/{name}.mLb.clN.sorted.bam.bai",
                  f"{lib}/bigwig/{name}.mLb.clN.bigWig",
                  f"{lib}/macs2/broad_peak/{name}.mLb.clN_peaks.broadPeak"]
    if merged_replicates:
        for name in ("WT", "KO"):
            paths += [f"{rep}/{name}.mRp.clN.sorted.bam", f"{rep}/{name}.mRp.clN.sorted.bam.bai",
                      f"{rep}/bigwig/{name}.mRp.clN.bigWig",
                      f"{rep}/macs2/broad_peak/{name}.mRp.clN_peaks.broadPeak"]
    paths.append(f"{lib}/macs2/broad_peak/consensus/consensus_peaks.mLb.clN.bed")
    return paths


def test_atacseq_merged_replicate_files_land_on_their_group(tmp_path):
    _run(tmp_path, "atacseq", "2.1.2", ATAC_SHEET)
    result = mapper.apply(_harvest(tmp_path, _atac_paths(), ATAC_UIDS),
                          maps.load("atacseq"), context=CONTEXT)
    assert _rows(result, "aln") == [
        ("HET_REP1", "HET_REP1.mLb.clN.sorted.bam", "D.SEQ-5"),
        ("KO", "KO.mRp.clN.sorted.bam", "D.SEQ-3;D.SEQ-4"),
        ("WT", "WT.mRp.clN.sorted.bam", "D.SEQ-1;D.SEQ-2")]
    assert _rows(result, "peaks_broad") == [
        ("HET_REP1", "HET_REP1.mLb.clN_peaks.broadPeak", "D.SEQ-5"),
        ("KO", "KO.mRp.clN_peaks.broadPeak", "D.SEQ-3;D.SEQ-4"),
        ("WT", "WT.mRp.clN_peaks.broadPeak", "D.SEQ-1;D.SEQ-2")]
    assert _rows(result, "bigwig") == [
        ("HET_REP1", "HET_REP1.mLb.clN.bigWig", "D.SEQ-5"),
        ("KO", "KO.mRp.clN.bigWig", "D.SEQ-3;D.SEQ-4"),
        ("WT", "WT.mRp.clN.bigWig", "D.SEQ-1;D.SEQ-2")]
    # The replicates' own merged-library files lose to the group's (G-4): the
    # BAM row takes one as File_SecondaryData, and none is dropped unseen.
    wt = next(r for r in result.rows if r.rule_id == "aln" and r.nfcore_sample == "WT")
    assert wt.attributes["File_SecondaryData"].value == "WT.mRp.clN.sorted.bam.bai"
    lost = " ".join(result.warnings)
    for name in ("WT_REP1", "WT_REP2", "KO_REP1", "KO_REP2"):
        assert f"{name}.mLb.clN.sorted.bam" in lost
        assert f"{name}.mLb.clN_peaks.broadPeak" in lost
    assert _rows(result, "consensus") == [
        ("", "consensus_peaks.mLb.clN.bed", "D.SEQ-1;D.SEQ-2;D.SEQ-3;D.SEQ-4;D.SEQ-5")]


def test_atacseq_without_merged_replicates_registers_every_replicate(tmp_path):
    _run(tmp_path, "atacseq", "2.1.2", ATAC_SHEET)
    result = mapper.apply(_harvest(tmp_path, _atac_paths(merged_replicates=False), ATAC_UIDS),
                          maps.load("atacseq"), context=CONTEXT)
    assert _rows(result, "aln") == [
        ("HET_REP1", "HET_REP1.mLb.clN.sorted.bam", "D.SEQ-5"),
        ("KO_REP1", "KO_REP1.mLb.clN.sorted.bam", "D.SEQ-3"),
        ("KO_REP2", "KO_REP2.mLb.clN.sorted.bam", "D.SEQ-4"),
        ("WT_REP1", "WT_REP1.mLb.clN.sorted.bam", "D.SEQ-1"),
        ("WT_REP2", "WT_REP2.mLb.clN.sorted.bam", "D.SEQ-2")]
    assert [r for r, _, _ in _rows(result, "peaks_broad")] == [
        "HET_REP1", "KO_REP1", "KO_REP2", "WT_REP1", "WT_REP2"]


def test_lanes_of_one_replicate_stay_one_multirun_sample(tmp_path):
    sheet = ("sample,fastq_1,fastq_2,replicate\n"
             f"WT,{_fq('wt_l1')},,1\nWT,{_fq('wt_l2')},,1\nWT,{_fq('wt_b')},,2\n")
    uids = {_fq("wt_l1"): "D.SEQ-1", _fq("wt_l2"): "D.SEQ-9", _fq("wt_b"): "D.SEQ-2"}
    _run(tmp_path, "atacseq", "2.1.2", sheet)
    got = _harvest(tmp_path, [], uids)
    assert [(s.nfcore_sample, s.uid_resolution, s.d_seq_uid, s.d_seq_uid_multirun)
            for s in got.samples] == [
        ("WT_REP1", manifest.RESOLUTION_MULTIRUN, None, ["D.SEQ-1", "D.SEQ-9"]),
        ("WT_REP2", manifest.RESOLUTION_FASTQ_EXACT, "D.SEQ-2", [])]


def test_a_map_that_does_not_ask_keeps_keying_by_sample(tmp_path):
    # rnaseq's samplesheet may carry a `replicate` column of its own; only a map with
    # replicate_samples changes the keying.
    sheet = ("sample,fastq_1,fastq_2,strandedness,replicate\n"
             f"WT,{_fq('wt_a')},,auto,1\nWT,{_fq('wt_b')},,auto,2\n")
    _run(tmp_path, "rnaseq", "3.22.2", sheet)
    got = _harvest(tmp_path, [], {_fq("wt_a"): "D.SEQ-1", _fq("wt_b"): "D.SEQ-2"})
    assert [(s.nfcore_sample, s.uid_resolution) for s in got.samples] == [
        ("WT", manifest.RESOLUTION_MULTIRUN)]
    assert got.replicate_groups == {}


def test_a_replicate_keyed_map_says_so_when_the_sheet_has_no_replicate_column(tmp_path):
    _run(tmp_path, "chipseq", "2.1.0", f"sample,fastq_1\nSPT5,{_fq('spt5_a')}\n")
    got = _harvest(tmp_path, [], {_fq("spt5_a"): "D.SEQ-1"})
    assert [s.nfcore_sample for s in got.samples] == ["SPT5"]
    assert any("replicate" in w for w in got.warnings)


def test_the_launch_record_resolves_each_replicate_by_its_own_fastq(tmp_path):
    from nextseek_api.assistant.models_db import PipelineRun
    _run(tmp_path, "chipseq", "2.1.0", CHIP_SHEET)
    PipelineRun.objects.create(run_dir=str(tmp_path), run_name="r", pipeline="nf-core/chipseq", cohort=[
        {"nfcore_sample": "SPT5", "fastq_1": _fq("spt5_a"), "d_seq_uid": "D.SEQ-11"},
        {"nfcore_sample": "SPT5", "fastq_1": _fq("spt5_b"), "d_seq_uid": "D.SEQ-12"},
        {"nfcore_sample": "INPUT", "fastq_1": _fq("input_a"), "d_seq_uid": None}])
    got = _harvest(tmp_path, [], CHIP_UIDS)
    assert [(s.nfcore_sample, s.d_seq_uid, s.uid_resolution) for s in got.samples] == [
        ("SPT5_REP1", "D.SEQ-11", manifest.RESOLUTION_LAUNCH_RECORD),
        ("SPT5_REP2", "D.SEQ-12", manifest.RESOLUTION_LAUNCH_RECORD),
        ("INPUT_REP1", None, manifest.RESOLUTION_UNRESOLVED),
        ("INPUT_REP2", "D.SEQ-4", manifest.RESOLUTION_FASTQ_EXACT)]
