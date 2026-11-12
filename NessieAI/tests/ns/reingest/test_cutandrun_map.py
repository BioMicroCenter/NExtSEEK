"""nf-core/cutandrun: a `group` samplesheet, `<group>_R<n>` replicates, MultiQC under
04_reporting/, consensus peaks per group or over every target, and FRiP and peak count on
the primary caller's rows only.

cutandrun 3.x names a sample by `group` (assets/schema_input.json), and its replicates'
files `<group>_R<replicate>` (`replicate_name`). It publishes MultiQC under
`04_reporting/multiqc/` (`multiqc_data_dirs`). With `--peakcaller seacr,macs2` the first
caller is primary: only its peaks get the FRiP score and peak count (PEAK_QC), so the
other caller's rows carry neither (`pipelines/cutandrun.py`). Its consensus is one file per
group, or with `--consensus_peak_mode all` one `all_samples` file over every target
(`every_sample_stem`, `controls_by_samplesheet`).
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from NessieAI.ns.reingest import harvest, manifest, mapper, maps, uid_resolve
from NessieAI.ns.reingest.pipelines import cutandrun as tokens

pytestmark = pytest.mark.django_db

ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
CONTEXT = {"has_attribute": lambda sample_type, attribute: attribute in ATTRS.get(sample_type, ())}


def _fq(name, read=1):
    return f"/net/fq/{name}_R{read}.fastq.gz"


SHEET = (
    "group,replicate,fastq_1,fastq_2,control\n"
    f"h3k27me3,1,{_fq('k27_a')},{_fq('k27_a', 2)},igg\n"
    f"h3k27me3,2,{_fq('k27_b')},{_fq('k27_b', 2)},igg\n"
    f"igg,1,{_fq('igg_a')},{_fq('igg_a', 2)},\n")
UIDS = {_fq("k27_a"): "D.SEQ-1", _fq("k27_b"): "D.SEQ-2", _fq("igg_a"): "D.SEQ-3"}
GENERAL_STATS = (
    "Sample\tBowtie2 (target)_mqc-generalstats-bowtie2_target-overall_alignment_rate\n"
    "h3k27me3_R1\t88.1\nh3k27me3_R2\t87.0\nigg_R1\t90.2\n")
FRIP = "Sample\th3k27me3_R1\th3k27me3_R2\nh3k27me3_R1\t0.42\t\nh3k27me3_R2\t\t0.38\n"
PEAKS = "Sample\tPeak Count\nh3k27me3_R1\t2001\nh3k27me3_R2\t1800\n"


def _run(root, params=None, consensus="h3k27me3", callers=("seacr",)):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_cutandrun_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/cutandrun: v3.2.2\n  Nextflow: 24.04.4\n")
    if params is not None:
        (root / "pipeline_info" / "params_2026-11-12_10-00-00.json").write_text(json.dumps(params))
    (root / "samplesheet.csv").write_text(SHEET)
    data = root / "04_reporting" / "multiqc" / "multiqc_data"
    data.mkdir(parents=True)
    (data / "multiqc_general_stats.txt").write_text(GENERAL_STATS)
    (data / "multiqc_primary_fripscore_plot.txt").write_text(FRIP)
    (data / "multiqc_primary_peakcounts_plot.txt").write_text(PEAKS)
    paths = ["04_reporting/multiqc/multiqc_report.html"]
    for name in ("h3k27me3_R1", "h3k27me3_R2", "igg_R1"):
        paths += [f"02_alignment/bowtie2/target/markdup/{name}.target.markdup.sorted.bam",
                  f"02_alignment/bowtie2/target/markdup/{name}.target.markdup.sorted.bam.bai",
                  f"03_peak_calling/03_bed_to_bigwig/{name}.bigWig"]
    for name in ("h3k27me3_R1", "h3k27me3_R2"):
        if "seacr" in callers:
            paths.append(f"03_peak_calling/04_called_peaks/seacr/{name}.seacr.peaks.stringent.bed")
        if "macs2" in callers:
            paths.append(f"03_peak_calling/04_called_peaks/macs2/{name}.macs2_peaks.narrowPeak")
    primary = callers[0]
    paths += [f"03_peak_calling/05_consensus_peaks/{consensus}.{primary}.consensus.peaks.awk.bed",
              f"03_peak_calling/05_consensus_peaks/{consensus}.{primary}.consensus.peak_counts.bed"]
    return paths


def _harvest(root, paths):
    return harvest.harvest_local(
        str(root), inventory=[{"path": p, "bytes": 1} for p in paths],
        lookup_by_fastq=lambda path, types=None: [UIDS[path]] if path in UIDS else [],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})


def _value(row, attribute):
    found = row.attributes.get(attribute)
    return None if found is None else found.value


def test_the_group_column_names_samples_and_replicates_are_group_R_n(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "seacr", "genome": "GRCh38"}))
    assert [(s.nfcore_sample, s.d_seq_uid, s.uid_resolution) for s in run.samples] == [
        ("h3k27me3_R1", "D.SEQ-1", manifest.RESOLUTION_FASTQ_EXACT),
        ("h3k27me3_R2", "D.SEQ-2", manifest.RESOLUTION_FASTQ_EXACT),
        ("igg_R1", "D.SEQ-3", manifest.RESOLUTION_FASTQ_EXACT)]
    assert run.replicate_groups == {"h3k27me3": ["h3k27me3_R1", "h3k27me3_R2"], "igg": ["igg_R1"]}
    assert run.samples[0].samplesheet == {"group": "h3k27me3", "control": "igg"}


def test_multiqc_under_04_reporting_is_read(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "seacr"}))
    by_name = {s.nfcore_sample: s for s in run.samples}
    assert by_name["h3k27me3_R1"].metrics[
        "Bowtie2 (target)_mqc-generalstats-bowtie2_target-overall_alignment_rate"] == 88.1
    assert by_name["h3k27me3_R1"].derived["frip_pct"] == 42.0
    assert by_name["h3k27me3_R1"].derived["peak_count"] == 2001
    assert run.sources["metrics"] == "04_reporting/multiqc/multiqc_data/multiqc_general_stats.txt"


def test_mapped_percent_lands_on_each_replicates_d_seq(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "seacr"}))
    result = mapper.apply(run, maps.load("cutandrun"), context=CONTEXT)
    backfill = {r.uid: r for r in result.rows if r.sample_type == "D.SEQ" and not r.rule_id}
    assert _value(backfill["D.SEQ-1"], "MappedPercent") == 88.1
    assert _value(backfill["D.SEQ-3"], "MappedPercent") == 90.2


def test_only_the_primary_callers_rows_get_frip_and_peak_count(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "seacr,macs2"}, callers=("seacr", "macs2")))
    result = mapper.apply(run, maps.load("cutandrun"), context=CONTEXT)
    seacr = [r for r in result.rows if r.rule_id == "peaks_seacr"]
    macs2 = [r for r in result.rows if r.rule_id == "peaks_macs2_narrow"]
    assert sorted(_value(r, "FRiPPercent") for r in seacr) == [38.0, 42.0]
    assert sorted(_value(r, "PeakCount") for r in seacr) == [1800, 2001]
    assert [(_value(r, "FRiPPercent"), _value(r, "PeakCount")) for r in macs2] == [(None, None)] * 2


def test_macs2_first_puts_frip_on_the_macs2_rows(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "MACS2,seacr"}, callers=("macs2", "seacr")))
    result = mapper.apply(run, maps.load("cutandrun"), context=CONTEXT)
    assert sorted(_value(r, "FRiPPercent") for r in result.rows if r.rule_id == "peaks_macs2_narrow") == [38.0, 42.0]
    assert [_value(r, "FRiPPercent") for r in result.rows if r.rule_id == "peaks_seacr"] == [None, None]


def test_primary_caller_falls_back_to_the_default_then_to_the_files():
    outputs = [manifest.OutputRecord(path="03_peak_calling/04_called_peaks/macs2/a_R1.macs2_peaks.narrowPeak")]
    run = manifest.RunManifest(run_dir="/RUN", params={"genome": "GRCh38"}, outputs=outputs)
    assert tokens.primary_caller(run, {}) == "seacr"
    assert tokens.primary_caller(run.model_copy(update={"params": {}}), {}) == "macs2"
    both = outputs + [manifest.OutputRecord(path="03_peak_calling/04_called_peaks/seacr/a_R1.seacr.peaks.stringent.bed")]
    assert tokens.primary_caller(run.model_copy(update={"params": {}, "outputs": both}), {}) is None


def test_peak_rows_skip_the_igg_control_and_each_group_gets_its_consensus(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "seacr"}))
    result = mapper.apply(run, maps.load("cutandrun"), context=CONTEXT)
    assert sorted(r.nfcore_sample for r in result.rows if r.rule_id == "peaks_seacr") == [
        "h3k27me3_R1", "h3k27me3_R2"]
    assert len([r for r in result.rows if r.rule_id == "bigwig"]) == 3
    [consensus] = [r for r in result.rows if r.rule_id == "consensus_seacr"]
    assert _value(consensus, "Parent") == "D.SEQ-1;D.SEQ-2"
    assert result.stale_files == []


def test_the_all_samples_consensus_is_parented_by_every_target(tmp_path):
    run = _harvest(tmp_path, _run(tmp_path, {"peakcaller": "seacr", "consensus_peak_mode": "all"},
                                  consensus="all_samples"))
    result = mapper.apply(run, maps.load("cutandrun"), context=CONTEXT)
    [consensus] = [r for r in result.rows if r.rule_id == "consensus_seacr"]
    assert _value(consensus, "Parent") == "D.SEQ-1;D.SEQ-2"
    assert result.stale_files == []


def test_key_by_replicate_takes_the_maps_name_format():
    rows = [{"sample": "A B", "replicate": "2"}]
    keyed, groups = uid_resolve.key_by_replicate(rows, "{sample}_R{replicate}")
    assert keyed[0]["sample"] == "A_B_R2" and groups == {"A_B": ["A_B_R2"]}
    assert uid_resolve.key_by_replicate(rows)[0][0]["sample"] == "A_B_REP2"


BASE = {"map_version": 2, "pipeline": "nf-core/x",
        "outputs": [{"id": "r", "sample_type": "A.CHRM", "candidates": ["p/*.bed"],
                     "cardinality": "per_file", "primary_data": True}]}


@pytest.mark.parametrize("field, value", [
    ("replicate_name", "{sample}_R"),
    ("replicate_name", "{sample}_{replicate}_{lane}"),
    ("multiqc_data_dirs", ["04_reporting/multiqc/multiqc_data"]),
    ("multiqc_data_dirs", ["/abs/multiqc_data/"]),
    ("multiqc_data_dirs", ["../up/multiqc_data/"]),
    ("samplesheet_sample_column", "two words"),
])
def test_bad_map_fields_fail_to_load(field, value):
    with pytest.raises(ValidationError):
        maps.PipelineMap.model_validate({**BASE, field: value})


def test_every_sample_stem_needs_parents_by_samplesheet_and_controls_need_it():
    rule = dict(BASE["outputs"][0])
    with pytest.raises(ValidationError):
        maps.PipelineMap.model_validate({**BASE, "outputs": [{**rule, "every_sample_stem": "all"}]})
    with pytest.raises(ValidationError):
        maps.PipelineMap.model_validate({**BASE, "outputs": [
            {**rule, "parents_by_samplesheet": "group", "controls_by_samplesheet": "control"}]})
    ok = maps.PipelineMap.model_validate({**BASE, "outputs": [
        {**rule, "parents_by_samplesheet": "group", "every_sample_stem": "all",
         "controls_by_samplesheet": "control"}]})
    assert ok.samplesheet_columns() == {"group", "control"}


def test_the_multiqc_folder_is_staged():
    assert "04_reporting/multiqc/multiqc_data/multiqc_*.txt" in harvest.staged_globs()
