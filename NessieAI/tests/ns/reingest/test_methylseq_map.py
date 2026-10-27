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


# --- The map against the 4.2.0 variant fixtures: one nf-test snapshot test each,
# imported with scripts/reingest/import_evidence.py --test/--variant (G-7). ---

import pytest

from NessieAI.ns.reingest import harvest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "methylseq"


def _fixture_rows(folder: str):
    path = FIXTURES / folder
    inventory = json.loads((path / "inventory.json").read_text())
    run = harvest.harvest_local(str(path), inventory=inventory, run_dir="/RUN")
    result = mapper.apply(run, maps.load("methylseq"),
                          context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})
    return result, {(r.rule_id, r.nfcore_sample): r for r in result.rows}


def _value(row, attribute):
    found = row.attributes.get(attribute)
    return found.value if found is not None else None


@pytest.mark.django_db
@pytest.mark.parametrize("folder, aln_file, calls_rule, calls_file, protocol", [
    ("4.2.0", "deduplicated.sorted.bam", "mseq_bismark",
     "_1_val_1_bismark_bt2_pe.deduplicated.bismark.cov.gz", "WGBS"),
    ("4.2.0-bismark-rrbs", "sorted.bam", "mseq_bismark", "_1_val_1_bismark_bt2_pe.bismark.cov.gz", "RRBS"),
    ("4.2.0-bismark-skip_deduplication", "sorted.bam", "mseq_bismark",
     "_1_val_1_bismark_bt2_pe.bismark.cov.gz", "WGBS"),
    ("4.2.0-bwameth", "markdup.sorted.bam", "mseq_methyldackel", ".markdup.sorted_CpG.bedGraph", "WGBS"),
    ("4.2.0-bwameth-rrbs", "sorted.bam", "mseq_methyldackel", ".sorted_CpG.bedGraph", "RRBS"),
    ("4.2.0-bwameth-skip_deduplication", "sorted.bam", "mseq_methyldackel", ".sorted_CpG.bedGraph", "WGBS"),
])
def test_each_tier_registers_its_own_alignment_and_calls(folder, aln_file, calls_rule, calls_file, protocol):
    """--rrbs and --skip_deduplication skip deduplication, so the sorted BAM under
    alignments/ is the alignment; each aligner's calls come from its own rule."""
    result, rows = _fixture_rows(folder)
    sample = "Ecoli_10K_methylated"
    assert _value(rows[("aln", sample)], "File_PrimaryData") == f"{sample}.{aln_file}"
    calls = rows[(calls_rule, sample)]
    assert _value(calls, "File_PrimaryData") == f"{sample}{calls_file}"
    assert _value(calls, "MethylationProtocol") == protocol
    assert _value(calls, "ConversionMethod") == "Bisulfite"
    other = {"mseq_bismark": "mseq_methyldackel", "mseq_methyldackel": "mseq_bismark"}[calls_rule]
    assert not [r for r in result.rows if r.rule_id == other]
    assert [w for w in result.warnings if "matched no file" in w] == []


@pytest.mark.django_db
def test_bwameths_unsorted_bam_is_ruled_out_and_the_pre_dedup_bam_rides_on_the_row():
    """--save_align_intermeds publishes bwa-meth's unsorted <sample>.bam (ruled out, as
    Bismark's is) and the sorted BAM before deduplication, a losing candidate of the aln
    rule: A.ALN's one secondary slot holds the index, so it reaches the build report."""
    result, rows = _fixture_rows("4.2.0-bwameth-save_align_intermeds")
    row = rows[("aln", "SRR389222_sub1")]
    assert _value(row, "File_PrimaryData") == "SRR389222_sub1.markdup.sorted.bam"
    seen = " ".join(str(a.value) for a in row.attributes.values()) + " ".join(result.warnings)
    assert "bwameth/alignments/SRR389222_sub1.bam" not in seen
    assert ("SRR389222_sub1: no Notes on A.ALN; also in run: "
            "bwameth/alignments/SRR389222_sub1.sorted.bam.bai, "
            "bwameth/alignments/SRR389222_sub1.sorted.bam") in result.warnings
    ruling = next(u for u in maps.load("methylseq").unmapped_files if u.except_globs)
    assert ruling.rules_out("bwameth/alignments/SRR389222_sub1.bam")
    assert not ruling.rules_out("bwameth/alignments/SRR389222_sub1.sorted.bam")


@pytest.mark.django_db
def test_the_bwameth_rows_name_their_aligner():
    _, rows = _fixture_rows("4.2.0-bwameth")
    assert _value(rows[("aln", "SRR389222_sub2")], "Aligner") == "bwameth"
