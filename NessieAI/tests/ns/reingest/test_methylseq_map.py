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
