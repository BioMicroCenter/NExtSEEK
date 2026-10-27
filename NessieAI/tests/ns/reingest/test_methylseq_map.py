"""The methylseq map's A.MSEQ rules on the bwa-meth path, where no committed fixture has the
file: MethylDackel writes `<bam stem>_CpG.methylKit` INSTEAD of its bedGraph when the run
sets `--methyl_kit` (conf/modules/methyldackel_extract.config passes --methylKit)."""
import json
from pathlib import Path

from NessieAI.ns.reingest import manifest, mapper, maps

ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]


def _run(calls_file: str, params: dict):
    return manifest.RunManifest(
        run_dir="/RUN",
        params={"aligner": "bwameth", **params},
        pipeline=manifest.PipelineInfo(name="nf-core/methylseq", version="4.2.0"),
        samples=[manifest.SampleRecord(nfcore_sample="S1", fastq_1="/RUN/inputs/S1_R1.fastq.gz",
                                       d_seq_uid="D.SEQ-EXAMPLE-1",
                                       uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD)],
        outputs=[manifest.OutputRecord(path="bwameth/deduplicated/S1.markdup.sorted.bam", sample="S1"),
                 manifest.OutputRecord(path=calls_file, sample="S1")])


def _mseq_rows(run):
    result = mapper.apply(run, maps.load("methylseq"),
                          context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})
    assert [w for w in result.warnings if "matched no file" in w] == []
    return [r for r in result.rows if r.sample_type == "A.MSEQ"]


def test_methyl_kit_output_is_registered_as_methylkit():
    [row] = _mseq_rows(_run("methyldackel/S1.markdup.sorted_CpG.methylKit", {"methyl_kit": True}))
    assert row.attributes["DataType"].value == "METHYLKIT"
    assert row.attributes["File_PrimaryData"].value == "S1.markdup.sorted_CpG.methylKit"
    assert row.attributes["Software"].value == "MethylDackel"


def test_the_bedgraph_is_still_registered_as_bedgraph():
    [row] = _mseq_rows(_run("methyldackel/S1.markdup.sorted_CpG.bedGraph", {}))
    assert row.attributes["DataType"].value == "BEDGRAPH"
    assert row.attributes["File_PrimaryData"].value == "S1.markdup.sorted_CpG.bedGraph"


def test_the_methylkit_file_is_inventoried():
    assert "**/methyldackel/*_CpG.methylKit" in maps.load("methylseq").inventory_globs


def _apply(outputs: list[str]):
    run = _run(outputs[0], {})
    run = run.model_copy(update={"outputs": run.outputs + [
        manifest.OutputRecord(path=p, sample="S1") for p in outputs[1:]]})
    return mapper.apply(run, maps.load("methylseq"),
                        context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})


def test_a_folder_reused_by_two_launches_keeps_both_rows_and_says_why():
    # nf-core's publishDir never deletes a stale file, so a run folder reused by a
    # launch with --methyl_kit keeps the earlier launch's bedGraph beside the new
    # methylKit file. Both rows stay; the build report says which files and why.
    result = _apply(["methyldackel/S1.markdup.sorted_CpG.bedGraph",
                     "methyldackel/S1.markdup.sorted_CpG.methylKit"])
    assert sorted(r.rule_id for r in result.rows if r.sample_type == "A.MSEQ") == [
        "mseq_methyldackel", "mseq_methyldackel_methylkit"]
    [warning] = [w for w in result.warnings if "earlier launch" in w]
    assert warning.startswith("S1: 2 A.MSEQ rows")
    assert "methyldackel/S1.markdup.sorted_CpG.bedGraph" in warning
    assert "methyldackel/S1.markdup.sorted_CpG.methylKit" in warning


def test_two_aligners_in_one_folder_are_flagged_too():
    result = _apply(["methyldackel/S1.markdup.sorted_CpG.bedGraph",
                     "bismark/methylation_calls/methylation_coverage/"
                     "S1.deduplicated.bismark.cov.gz"])
    [warning] = [w for w in result.warnings if "earlier launch" in w]
    assert "mseq_bismark" in warning and "mseq_methyldackel" in warning


def test_one_launchs_calls_raise_no_reused_folder_warning():
    result = _apply(["methyldackel/S1.markdup.sorted_CpG.bedGraph"])
    assert [w for w in result.warnings if "earlier launch" in w] == []


def test_every_a_mseq_rule_is_one_alternative_group():
    groups = {r.id: r.alternative_group for r in maps.load("methylseq").outputs}
    assert groups == {"aln": None, "mseq_bismark": "methylation_calls",
                      "mseq_methyldackel": "methylation_calls",
                      "mseq_methyldackel_methylkit": "methylation_calls"}


def test_the_alternative_rows_are_carried_for_the_curator():
    result = _apply(["methyldackel/S1.markdup.sorted_CpG.bedGraph",
                     "methyldackel/S1.markdup.sorted_CpG.methylKit"])
    assert result.alternative_rows == [{
        "sample": "S1", "sample_type": "A.MSEQ",
        "files": [{"rule_id": "mseq_methyldackel",
                   "path": "methyldackel/S1.markdup.sorted_CpG.bedGraph"},
                  {"rule_id": "mseq_methyldackel_methylkit",
                   "path": "methyldackel/S1.markdup.sorted_CpG.methylKit"}],
        "other_picks": []}]


def test_a_reused_folder_also_names_the_alignment_rows_pick():
    # Bismark then bwa-meth in one folder: the aln rule's tier order takes Bismark's
    # BAM, which may be the stale one, so the curator is told to check it too.
    result = _apply(["methyldackel/S1.markdup.sorted_CpG.bedGraph",
                     "bismark/deduplicated/S1.deduplicated.sorted.bam",
                     "bismark/methylation_calls/methylation_coverage/"
                     "S1.deduplicated.bismark.cov.gz"])
    [entry] = result.alternative_rows
    assert entry["other_picks"] == [{
        "rule_id": "aln", "sample_type": "A.ALN",
        "used": "bismark/deduplicated/S1.deduplicated.sorted.bam",
        "also": ["bwameth/deduplicated/S1.markdup.sorted.bam"]}]
    [warning] = [w for w in result.warnings if "earlier launch" in w]
    assert ("A.ALN row (aln) used bismark/deduplicated/S1.deduplicated.sorted.bam over "
            "bwameth/deduplicated/S1.markdup.sorted.bam") in warning


def test_one_launch_carries_no_alternative_rows():
    assert _apply(["methyldackel/S1.markdup.sorted_CpG.bedGraph"]).alternative_rows == []
