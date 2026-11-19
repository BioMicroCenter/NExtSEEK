"""nf-core/rnasplice's map against 2.0.0, the pinned release, and 1.0.4, the one before it.

The general-stats rows below are nf-core's own `-profile test_full` run of 2.0.0 (AWS results
for the release commit aceb47ab, `multiqc/multiqc_data/multiqc_general_stats.txt`, sample
SRR536342, read 2026-11-19), copied cell for cell. 2.0.0 ships MultiQC 1.35, which spells
the columns `<module>-<metric>` where 1.0.4's MultiQC 1.18 wrote
`<Module>_mqc-generalstats-<module>-<metric>`; prints STAR's counts in millions; writes
samtools' error rate as a percentage; and puts featureCounts on the `<sample>_sorted` row
(the BAM's name), which `qc_row_suffixes` folds onto the sample. Trim Galore's FastQC of the
trimmed reads sits on `<sample>_1_val_1` / `_2_val_2` under the same `fastqc-` names, and is
never folded, so the read lengths are the raw reads'.
"""
from NessieAI.ns.reingest import harvest, manifest, mapper, maps

SAMPLE = "SRR536342"

STATS_2_0_0 = {
    SAMPLE: {
        "star-total_reads": 131.330344, "star-mapped": 126.87898799999999,
        "star-mapped_percent": 96.61, "star-uniquely_mapped": 122.299432,
        "star-uniquely_mapped_percent": 93.12, "star-multimapped": 4.579556,
        "salmon-percent_mapped": 87.20896139585228, "salmon-num_mapped": 114.531829,
        "salmon-library_types": "IU", "salmon-compatible_fragment_ratio": 100.0,
        "salmon-strand_mapping_bias": 0.499371431180296,
        "samtools_stats-error_rate": 0.23210309999999998,
        "samtools_stats-non_primary_alignments": 15.288879,
        "samtools_stats-reads_mapped": 253.527896, "samtools_stats-reads_mapped_percent": 100.0,
        "samtools_stats-reads_properly_paired_percent": 99.90924864536406,
        "samtools_stats-reads_MQ0_percent": 0.2667903653489871,
        "samtools_stats-raw_total_sequences": 253.527896,
        "samtools_stats-insert_size_average": 1359.9,
        "samtools_flagstat-flagstat_total": 268.816775,
        "samtools_flagstat-mapped_passed": 268.816775,
        "samtools_flagstat-mapped_passed_pct": 100.0,
    },
    f"{SAMPLE}_1": {
        "fastqc-percent_duplicates": 68.28359983419324, "fastqc-percent_gc": 50.0,
        "fastqc-avg_sequence_length": 101.0, "fastqc-median_sequence_length": 101.0,
        "fastqc-percent_fails": 18.181818181818183, "fastqc-total_sequences": 132.33656,
    },
    f"{SAMPLE}_1_val_1": {
        "fastqc-percent_duplicates": 68.2092180442203, "fastqc-percent_gc": 50.0,
        "fastqc-avg_sequence_length": 98.2819405391948, "fastqc-median_sequence_length": 100.0,
        "fastqc-percent_fails": 18.181818181818183, "fastqc-total_sequences": 131.330344,
    },
    f"{SAMPLE}_2": {
        "fastqc-percent_duplicates": 67.42978914777305, "fastqc-percent_gc": 50.0,
        "fastqc-avg_sequence_length": 101.0, "fastqc-median_sequence_length": 101.0,
        "fastqc-percent_fails": 18.181818181818183, "fastqc-total_sequences": 132.33656,
    },
    f"{SAMPLE}_2_val_2": {
        "fastqc-percent_duplicates": 67.5450729865498, "fastqc-percent_gc": 50.0,
        "fastqc-avg_sequence_length": 97.693162107304, "fastqc-median_sequence_length": 100.0,
        "fastqc-percent_fails": 18.181818181818183, "fastqc-total_sequences": 131.330344,
    },
    f"{SAMPLE}_sorted": {
        "featurecounts-Assigned": 225.106458, "featurecounts-percent_assigned": 83.73973610835857,
    },
}

#: STAR's own count from `star_salmon/log/SRR536342.Log.final.out`, which MultiQC prints as
#: 122.299432 (millions). The harvest parses the log into `$derived.uniquely_mapped_reads`.
UNIQUELY_MAPPED = 122299432

#: Files of the same AWS run the two output rules read.
OUTPUTS_2_0_0 = [
    f"star_salmon/{SAMPLE}_sorted.bam",
    f"star_salmon/{SAMPLE}_sorted.bam.bai",
    "salmon/tximport/salmon.merged.gene_counts.tsv",
]


def _run(version, metrics, derived=None, outputs=()):
    return manifest.RunManifest(
        run_dir="/RUN",
        pipeline=manifest.PipelineInfo(name="nf-core/rnasplice", version=version),
        samples=[manifest.SampleRecord(
            nfcore_sample=SAMPLE, d_seq_uid="D.SEQ-X-1",
            uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD, parent_sample_type="D.SEQ",
            metrics=metrics, derived=derived or {})],
        outputs=[manifest.OutputRecord(path=p, sample=SAMPLE) for p in outputs])


def _folded(pipeline_map):
    return harvest._sample_metrics(
        SAMPLE, STATS_2_0_0, sample_names={SAMPLE},
        claimed=mapper.claimed_raw_keys(pipeline_map),
        row_suffixes=tuple(pipeline_map.qc_row_suffixes))


def _out_of_range(result):
    return [w for w in result.warnings if "outside the checked range" in w]


def test_featurecounts_on_the_sorted_bam_row_is_folded_onto_the_sample():
    metrics = _folded(maps.load("rnasplice"))
    assert metrics["featurecounts-percent_assigned"] == 83.73973610835857
    # The raw reads' FastQC, never the trimmed reads' on <sample>_1_val_1.
    assert metrics["fastqc-avg_sequence_length@R1"] == 101.0
    assert metrics["fastqc-avg_sequence_length@R2"] == 101.0


def test_a_2_0_0_run_fills_every_d_seq_qc_attribute_and_queues_nothing():
    pipeline_map = maps.load("rnasplice")
    run = _run("2.0.0", _folded(pipeline_map), {"uniquely_mapped_reads": UNIQUELY_MAPPED})
    result = mapper.apply(run, pipeline_map)
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    values = {a: v.value for a, v in row.attributes.items()}
    assert values["GCPercent"] == 50.0
    assert values["F_bp"] == 101.0 and values["R_bp"] == 101.0
    assert values["MappedPercent"] == 93.12
    assert values["NumAligned"] == UNIQUELY_MAPPED      # the exact count, not MultiQC's millions
    assert values["MismatchPercent"] == 0.23210309999999998   # already a percentage in 1.35
    assert values["AssignedPercent"] == 83.73973610835857
    assert [u["raw_key"] for u in result.unmapped] == []
    assert _out_of_range(result) == []


def test_a_1_0_4_run_still_maps_its_multiqc_1_18_columns_in_range():
    metrics = {"FastQC_mqc-generalstats-fastqc-percent_gc@R1": 50.0,
               "FastQC_mqc-generalstats-fastqc-avg_sequence_length@R1": 101.0,
               "STAR_mqc-generalstats-star-uniquely_mapped_percent": 90.0,
               "STAR_mqc-generalstats-star-uniquely_mapped": 24155641.0,
               "featureCounts_mqc-generalstats-featurecounts-percent_assigned": 71.0,
               "Samtools_mqc-generalstats-samtools-error_rate": 0.006}
    result = mapper.apply(_run("1.0.4", metrics, {"mismatch_pct": 0.6}), maps.load("rnasplice"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    values = {a: v.value for a, v in row.attributes.items()}
    assert values["NumAligned"] == 24155641.0   # MultiQC 1.18's raw count, when no STAR log
    assert values["MismatchPercent"] == 0.6
    assert values["AssignedPercent"] == 71.0
    assert [u["raw_key"] for u in result.unmapped] == []
    assert _out_of_range(result) == []


def test_the_output_rules_match_the_2_0_0_run():
    result = mapper.apply(_run("2.0.0", {}, outputs=OUTPUTS_2_0_0), maps.load("rnasplice"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert aln.attributes["File_PrimaryData"].value == f"{SAMPLE}_sorted.bam"
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["File_PrimaryData"].value.endswith("salmon.merged.gene_counts.tsv")
    assert result.missed_rules == []


def test_the_range_covers_both_releases():
    spec = maps.load("rnasplice").applies_to_versions
    assert spec == ">=1.0,<3"
