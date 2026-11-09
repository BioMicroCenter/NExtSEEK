"""atacseq and chipseq QC from MultiQC module tables and per-sample QC files.

Both releases exclude MultiQC's general-stats table and ask for YAML data files
(`data_format: yaml`), so their maps read QC from `module_tables`: Picard MarkDuplicates'
`multiqc_picard_dups` gives DuplicationPercent through `$derived.duplication_pct`. On the
releases' own -profile test runs (fixtures `atacseq/2.1.2-local-test` and
`chipseq/2.1.0-local-test`), atacseq's MultiQC 1.13 writes the table as .txt, and
chipseq's MultiQC 1.23 writes no table files at all, only the bar plot's data
(`picard_deduplication.yaml`, read counts per category), from which the value is computed
instead. The tables here are shaped like those runs' (rows after the configs'
extra_fn_clean_exts, Picard's 0-1 fraction).
"""
import json
import shutil
from pathlib import Path

import pytest

from NessieAI.ns.reingest import derived, harvest, mapper, maps, parsers

pytestmark = pytest.mark.django_db

ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
CONTEXT = {"has_attribute": lambda sample_type, attribute: attribute in ATTRS.get(sample_type, ())}


def _fq(name):
    return f"/net/fq/{name}_R1.fastq.gz"


def _dups(fraction, library):
    return {"LIBRARY": library, "UNPAIRED_READS_EXAMINED": 0, "READ_PAIRS_EXAMINED": 41000,
            "SECONDARY_OR_SUPPLEMENTARY_RDS": 12, "UNMAPPED_READS": 300,
            "UNPAIRED_READ_DUPLICATES": 0, "READ_PAIR_DUPLICATES": int(41000 * fraction),
            "READ_PAIR_OPTICAL_DUPLICATES": 4, "PERCENT_DUPLICATION": fraction,
            "ESTIMATED_LIBRARY_SIZE": 900000}


def _yaml(table: dict) -> str:
    import yaml
    return yaml.safe_dump(table, sort_keys=True)


def _txt(table: dict) -> str:
    columns = sorted({c for row in table.values() for c in row})
    lines = ["\t".join(["Sample", *columns])]
    lines += ["\t".join([s, *(str(row.get(c, "")) for c in columns)]) for s, row in table.items()]
    return "\n".join(lines) + "\n"


def _run(root, pipeline, version, sheet, tables: dict[str, str]):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / f"nf_core_{pipeline}_software_mqc_versions.yml").write_text(
        f"Workflow:\n  nf-core/{pipeline}: v{version}\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "params_2026-10-27_00-00-00.json").write_text(
        json.dumps({"genome": "GRCh38", "aligner": "bwa", "narrow_peak": False}))
    (root / "samplesheet.csv").write_text(sheet)
    data = root / "multiqc" / "broad_peak" / "multiqc_data"
    data.mkdir(parents=True)
    for name, text in tables.items():
        (data / name).write_text(text)


def _apply(root, uids, pipeline):
    run = harvest.harvest_local(
        str(root), inventory=[{"path": "multiqc/broad_peak/multiqc_report.html", "bytes": 1}],
        lookup_by_fastq=lambda path, types=None: [uids[path]] if path in uids else [],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})
    return run, mapper.apply(run, maps.load(pipeline), context=CONTEXT)


def _backfill(result):
    return {r.uid: {k: v.value for k, v in r.attributes.items()} for r in result.rows if r.uid}


CHIP_SHEET = (
    "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate\n"
    f"SPT5,{_fq('spt5_a')},,1,SPT5,INPUT,1\n"
    f"SPT5,{_fq('spt5_b')},,2,SPT5,INPUT,2\n"
    f"INPUT,{_fq('input_a')},,1,,,\n"
    f"INPUT,{_fq('input_b')},,2,,,\n")
CHIP_UIDS = {_fq("spt5_a"): "D.SEQ-1", _fq("spt5_b"): "D.SEQ-2",
             _fq("input_a"): "D.SEQ-3", _fq("input_b"): "D.SEQ-4"}
CHIP_DUPS = {"SPT5_REP1": _dups(0.150851, "SPT5_REP1_T1"), "SPT5_REP2": _dups(0.062656, "SPT5_REP2_T1"),
             "INPUT_REP1": _dups(0.2, "INPUT_REP1_T1"), "INPUT_REP2": _dups(0.25, "INPUT_REP2_T1")}


@pytest.mark.parametrize("name, render", [("multiqc_picard_dups.yaml", _yaml),
                                          ("multiqc_picard_dups.txt", _txt)])
def test_chipseq_duplication_is_read_per_replicate_as_a_percentage(tmp_path, name, render):
    _run(tmp_path, "chipseq", "2.1.0", CHIP_SHEET, {name: render(CHIP_DUPS)})
    run, result = _apply(tmp_path, CHIP_UIDS, "chipseq")
    backfill = _backfill(result)
    assert backfill["D.SEQ-1"]["DuplicationPercent"] == pytest.approx(15.0851)
    assert backfill["D.SEQ-2"]["DuplicationPercent"] == pytest.approx(6.2656)
    assert backfill["D.SEQ-4"]["DuplicationPercent"] == pytest.approx(25.0)
    assert run.sources["metrics_picard_dups"].endswith(name)
    # The value's provenance names the module table it came from.
    rows = {r.uid: r for r in result.rows if r.uid}
    assert rows["D.SEQ-1"].attributes["DuplicationPercent"].source_file == \
        run.sources["metrics_picard_dups"]
    # Every other column of the table is ruled out, so nothing reaches the review queue.
    assert result.unmapped == []


ATAC_SHEET = (
    "sample,fastq_1,fastq_2,replicate\n"
    f"WT,{_fq('wt_a')},,1\n"
    f"WT,{_fq('wt_b')},,2\n")
ATAC_UIDS = {_fq("wt_a"): "D.SEQ-1", _fq("wt_b"): "D.SEQ-2"}


def test_atacseq_reads_the_merged_library_table_not_the_merged_replicate_one(tmp_path):
    """MultiQC 1.13 appends a module's anchor to its data file name, so the merged-replicate
    MarkDuplicates (anchor mrep_picard) is a table of its own, whose rows are the groups."""
    _run(tmp_path, "atacseq", "2.1.2", ATAC_SHEET, {
        "multiqc_picard_dups.yaml": _yaml({"WT_REP1": _dups(0.1, "WT_REP1_T1"),
                                           "WT_REP2": _dups(0.3, "WT_REP2_T1")}),
        "multiqc_picard_dups_mrep_picard.yaml": _yaml({"WT": _dups(0.9, "WT")})})
    _, result = _apply(tmp_path, ATAC_UIDS, "atacseq")
    backfill = _backfill(result)
    assert backfill["D.SEQ-1"]["DuplicationPercent"] == pytest.approx(10.0)
    assert backfill["D.SEQ-2"]["DuplicationPercent"] == pytest.approx(30.0)
    assert result.unmapped == []


def test_without_the_table_there_is_no_duplication_and_no_guess(tmp_path):
    _run(tmp_path, "atacseq", "2.1.2", ATAC_SHEET, {})
    _, result = _apply(tmp_path, ATAC_UIDS, "atacseq")
    assert all("DuplicationPercent" not in attrs for attrs in _backfill(result).values())


def test_another_pipelines_picard_table_is_not_read(tmp_path):
    """module_tables are the map's own: an rnaseq run's multiqc_picard_dups.txt (whose
    numbers rnaseq reads from general stats) adds no picard_dups-* key to its samples."""
    _run(tmp_path, "rnaseq", "3.22.2", "sample,fastq_1,fastq_2,strandedness\n"
         f"S1,{_fq('s1')},,auto\n", {"multiqc_picard_dups.txt": _txt({"S1": _dups(0.1, "S1")})})
    run, _ = _apply(tmp_path, {_fq("s1"): "D.SEQ-1"}, "rnaseq")
    assert not [k for s in run.samples for k in s.metrics if k.startswith("picard_dups-")]


# SPT5_T0_REP2's MarkDuplicates on chipseq 2.1.0's test run: the bar plot's read counts,
# and the PERCENT_DUPLICATION Picard wrote for the same library (multiqc_data.json).
PLOT_ROW = {"Duplicate Pairs Nonoptical": 10364, "Duplicate Unpaired": 251, "Unique Pairs": 174538,
            "Unique Unpaired": 270, "Unmapped": 13997}
PLOT_FRACTION = 0.057247


def test_chipseq_duplication_comes_from_the_bar_plot_when_no_table_is_written(tmp_path):
    """MultiQC 1.23 with data_format: yaml writes no multiqc_picard_dups file; the plot's
    counts give Picard's own rate (duplicate over duplicate plus unique reads)."""
    plot = {row: PLOT_ROW for row in ("SPT5_REP1", "SPT5_REP2", "INPUT_REP1", "INPUT_REP2")}
    _run(tmp_path, "chipseq", "2.1.0", CHIP_SHEET, {"picard_deduplication.yaml": _yaml(plot)})
    run, result = _apply(tmp_path, CHIP_UIDS, "chipseq")
    backfill = _backfill(result)
    assert backfill["D.SEQ-1"]["DuplicationPercent"] == pytest.approx(PLOT_FRACTION * 100, abs=1e-4)
    rows = {r.uid: r for r in result.rows if r.uid}
    assert rows["D.SEQ-1"].attributes["DuplicationPercent"].source_file == \
        run.sources["metrics_picard_dedup_plot"]
    assert run.sources["metrics_picard_dedup_plot"].endswith("picard_deduplication.yaml")
    # The plot's categories are ruled out, so nothing reaches the review queue.
    assert result.unmapped == []


def test_the_table_wins_over_the_bar_plot(tmp_path):
    _run(tmp_path, "chipseq", "2.1.0", CHIP_SHEET, {
        "multiqc_picard_dups.yaml": _yaml(CHIP_DUPS),
        "picard_deduplication.yaml": _yaml({row: PLOT_ROW for row in CHIP_DUPS})})
    run, result = _apply(tmp_path, CHIP_UIDS, "chipseq")
    assert _backfill(result)["D.SEQ-1"]["DuplicationPercent"] == pytest.approx(15.0851)
    rows = {r.uid: r for r in result.rows if r.uid}
    assert rows["D.SEQ-1"].attributes["DuplicationPercent"].source_file == \
        run.sources["metrics_picard_dups"]


def test_duplication_pct_from_the_bar_plot_counts():
    plot = {f"{derived.PICARD_DEDUP_PLOT}-{k}": v for k, v in PLOT_ROW.items()}
    assert derived.from_metrics(plot)["duplication_pct"] == pytest.approx(PLOT_FRACTION * 100, abs=1e-4)
    # Optical duplicates count as duplicates; unmapped reads count for nothing.
    assert derived.from_metrics({f"{derived.PICARD_DEDUP_PLOT}-Duplicate Pairs Optical": 10,
                                 f"{derived.PICARD_DEDUP_PLOT}-Unique Pairs": 30,
                                 f"{derived.PICARD_DEDUP_PLOT}-Unmapped": 500})["duplication_pct"] == 25.0
    assert "duplication_pct" not in derived.from_metrics({f"{derived.PICARD_DEDUP_PLOT}-Unmapped": 5})


def test_chipseq_stages_the_bar_plot_data():
    assert "multiqc/*/multiqc_data/picard_deduplication.yaml" in maps.load("chipseq").harvest_globs
    assert maps.load("chipseq").module_tables["picard_deduplication.yaml"].namespace == \
        derived.PICARD_DEDUP_PLOT


@pytest.mark.parametrize("pipeline", ["atacseq", "chipseq"])
def test_the_yaml_table_is_staged(pipeline):
    """.txt tables are staged for every pipeline; the .yaml one only by the map's harvest_globs."""
    assert "multiqc/*/multiqc_data/multiqc_picard_dups.yaml" in harvest.staged_globs()
    assert "multiqc_picard_dups.yaml" in maps.load(pipeline).module_tables


def test_the_yaml_reader_keeps_scalar_cells_only():
    table = parsers.parse_multiqc_yaml_table(
        "A_REP1:\n  PERCENT_DUPLICATION: 0.25\n  LIBRARY: A_REP1_T1\n  blank: ''\n"
        "  series: {1: 2}\n  flag: true\nnot_a_row: 3\n")
    assert table == {"A_REP1": {"PERCENT_DUPLICATION": 0.25, "LIBRARY": "A_REP1_T1"}}
    assert parsers.parse_multiqc_yaml_table("- a\n- b\n") == {}
    # YAML's not-a-number is no measurement: left out like a blank.
    assert parsers.parse_multiqc_yaml_table("A:\n  x: .nan\n  y: .NaN\n  z: 1\n") == {"A": {"z": 1.0}}


def test_duplication_pct_is_picards_fraction_times_a_hundred():
    assert derived.from_metrics({derived.PICARD_DUPLICATION_FRACTION: 0.150851})["duplication_pct"] \
        == pytest.approx(15.0851)
    assert "duplication_pct" not in derived.from_metrics({derived.PICARD_DUPLICATION_FRACTION: "n/a"})
    assert "duplication_pct" not in derived.from_metrics({})


# ---- FRiP and peak count, from the custom-content bar graphs ------------------------------------

ATAC_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "atacseq" / "2.1.2-local-test"


def _fixture_peak_rows(folder, pipeline="atacseq"):
    inventory = json.loads((folder / "inventory.json").read_text())
    uids: dict[str, str] = {}
    run = harvest.harvest_local(
        str(folder), inventory=inventory, run_dir="/RUN",
        lookup_by_fastq=lambda path, types=None: [uids.setdefault(path, f"D.SEQ-TEST-{len(uids) + 1}")],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})
    result = mapper.apply(run, maps.load(pipeline), context=CONTEXT)
    return run, {r.nfcore_sample: {k: v.value for k, v in r.attributes.items()}
                 for r in result.rows if r.rule_id.startswith("peaks")}


def test_a_bar_graph_table_reads_each_rows_one_value():
    table = parsers.parse_general_stats(
        "Sample\tA\tB.mRp.clN_peaks\n"
        "A\t0.385186\t\n"
        "B\t\t1134.0\n"
        "C\t\t\n"
        "D\t1\t2\n")
    assert parsers.bargraph_values(table) == {"A": {"value": 0.385186}, "B": {"value": 1134.0}}


def test_frip_is_a_percentage_and_the_peak_count_a_whole_number():
    values = derived.from_metrics({"mrep_frip-value": 0.448815, "mrep_peak_count-value": 1134.0})
    assert values["frip_pct"] == 44.8815 and values["peak_count"] == 1134


def test_atacseq_peak_rows_carry_their_own_peak_sets_frip_and_count():
    """On the 2.1.2 test run: a merged replicate's row reads the group's own mrep values,
    which no replicate has; a single replicate's row reads its mlib values."""
    run, rows = _fixture_peak_rows(ATAC_FIXTURE)
    assert run.group_metrics["OSMOTIC_STRESS_T0_PE"] == {
        "mrep_frip-value": 0.448815, "mrep_peak_count-value": 1134.0}
    assert {name: (row["FRiPPercent"], row["PeakCount"]) for name, row in rows.items()} == {
        "OSMOTIC_STRESS_T0_PE": (44.8815, 1134),
        "OSMOTIC_STRESS_T100_SE": (41.6439, 1291),
        "OSMOTIC_STRESS_T15_PE_REP1": (43.5881, 1409),
        "OSMOTIC_STRESS_T150_SE_REP1": (41.3034, 1549),
    }


def test_a_peak_row_without_the_bar_graphs_has_no_frip_or_count(tmp_path):
    root = tmp_path / "run"
    shutil.copytree(ATAC_FIXTURE, root)
    for name in ("multiqc_mlib_frip_score-plot.txt", "multiqc_mrep_frip_score-plot.txt",
                 "multiqc_mlib_peak_count-plot.txt", "multiqc_mrep_peak_count-plot.txt"):
        (root / "multiqc" / "broad_peak" / "multiqc_data" / name).unlink()
    _run, rows = _fixture_peak_rows(root)
    assert rows and not any({"FRiPPercent", "PeakCount"} & set(row) for row in rows.values())


def test_atacseq_mapped_percent_is_each_runs_own_flagstat_before_merging():
    """MultiQC's first flagstat section, one row per sequencing run (`<replicate>_T<run>`),
    read only for MappedPercent. A replicate sequenced over two runs is a multi-run sample
    and gets no backfill, so neither run's value lands on a guessed D.SEQ."""
    folder = ATAC_FIXTURE
    inventory = json.loads((folder / "inventory.json").read_text())
    uids: dict[str, str] = {}
    run = harvest.harvest_local(
        str(folder), inventory=inventory, run_dir="/RUN",
        lookup_by_fastq=lambda path, types=None: [uids.setdefault(path, f"D.SEQ-TEST-{len(uids) + 1}")],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})
    result = mapper.apply(run, maps.load("atacseq"), context=CONTEXT)
    mapped = {r.nfcore_sample: r.attributes["MappedPercent"].value
              for r in result.rows if r.uid and "MappedPercent" in r.attributes}
    assert mapped == {"OSMOTIC_STRESS_T0_PE_REP1": 95.4, "OSMOTIC_STRESS_T0_PE_REP2": 92.98,
                      "OSMOTIC_STRESS_T100_SE_REP1": 94.81, "OSMOTIC_STRESS_T100_SE_REP2": 92.2}
    sample = next(s for s in run.samples if s.nfcore_sample == "OSMOTIC_STRESS_T0_PE_REP1")
    assert {k for k in sample.metrics if k.startswith("samtools_flagstat_lib-")} == {
        "samtools_flagstat_lib-mapped_passed_pct"}


# ---- per-sample QC files (maps.SampleFile) ------------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs"


def test_a_one_value_custom_content_file_reads_its_samples_line():
    text = "#id: 'nsc_coefficient'\n#plot_type: 'bargraph'\nSPT5_T0_REP1\t1.465785\n"
    assert parsers.parse_mqc_value(text, "SPT5_T0_REP1") == {"value": 1.465785}
    assert parsers.parse_mqc_value(text, "OTHER") == {"value": 1.465785}  # the file's only line
    assert parsers.parse_mqc_value("A\t1\nB\t2\n", "C") == {}            # neither named nor alone


def test_a_fingerprint_table_reads_the_samples_own_row_not_its_controls():
    text = ("Sample\tAUC\tSynthetic JS Distance\n"
            "SPT5_T0_REP1\t0.10\t0.596\n"
            "SPT5_INPUT_REP1\t0.31\t0.261\n")
    assert parsers.parse_sample_row(text, "SPT5_T0_REP1") == {"AUC": 0.10, "Synthetic JS Distance": 0.596}
    atac = "Sample\tSynthetic JS Distance\nOSMOTIC_STRESS_T0_PE_REP1.mLb.clN\t0.589\n"
    assert parsers.parse_sample_row(atac, "OSMOTIC_STRESS_T0_PE_REP1") == {"Synthetic JS Distance": 0.589}
    assert parsers.parse_sample_row(atac, "OSMOTIC_STRESS_T0_PE") == {}


def test_ataqv_metrics_keep_scalars_only():
    text = json.dumps([{"ataqv_version": "1.3.1", "metrics": {
        "name": "S", "tss_enrichment": 1.41, "peaks": [[1, 2]], "tss_coverage": [1, 2],
        "paired": True}}])
    assert parsers.parse_ataqv_metrics(text) == {"name": "S", "tss_enrichment": 1.41}
    assert parsers.parse_ataqv_metrics("not json") == {}


def test_a_sample_file_glob_must_name_the_sample_and_is_staged_with_a_wildcard():
    with pytest.raises(ValueError, match="<sample>"):
        maps.SampleFile(glob="*/qc/frip.tsv", reader="value")
    spec = maps.SampleFile(glob="*/qc/<sample>.FRiP_mqc.tsv", reader="value")
    assert spec.staged_glob() == "*/qc/*.FRiP_mqc.tsv"
    assert "*/merged_library/phantompeakqualtools/*.spp_nsc_mqc.tsv" in harvest.staged_globs()
    assert "*/merged_library/ataqv/*/*.ataqv.json" in harvest.staged_globs()


def test_chipseq_narrow_run_gives_peak_rows_frip_and_count_and_libraries_their_coefficients():
    run, rows = _fixture_peak_rows(FIXTURES / "chipseq" / "2.1.0-local-narrow", "chipseq")
    assert {name: (row["FRiPPercent"], row["PeakCount"]) for name, row in rows.items()} == {
        "SPT5_T0_REP1": (34.4185, 779), "SPT5_T0_REP2": (34.5988, 568),
        "SPT5_T15_REP1": (30.3395, 713), "SPT5_T15_REP2": (29.9154, 772)}
    result = mapper.apply(run, maps.load("chipseq"), context=CONTEXT)
    backfill = {r.nfcore_sample: {k: v.value for k, v in r.attributes.items()}
                for r in result.rows if r.uid}
    assert backfill["SPT5_T0_REP1"]["NSC"] == 1.465785
    assert backfill["SPT5_T0_REP1"]["RSC"] == 0.9126109
    assert backfill["SPT5_T0_REP1"]["FingerprintJSD"] == 0.596225904658976
    # An input is fingerprinted only as its IP's control: NSC and RSC, no fingerprint row.
    assert {"NSC", "RSC"} <= set(backfill["SPT5_INPUT_REP1"])
    assert "FingerprintJSD" not in backfill["SPT5_INPUT_REP1"]
    assert result.unmapped == []


def test_atacseq_narrow_run_gives_libraries_fingerprint_and_paired_end_tss_enrichment():
    run, _rows = _fixture_peak_rows(FIXTURES / "atacseq" / "2.1.2-local-narrow")
    result = mapper.apply(run, maps.load("atacseq"), context=CONTEXT)
    backfill = {r.nfcore_sample: {k: v.value for k, v in r.attributes.items()}
                for r in result.rows if r.uid}
    assert backfill["OSMOTIC_STRESS_T0_PE_REP1"]["TSSEnrichment"] == 1.40932363769605
    assert backfill["OSMOTIC_STRESS_T0_PE_REP1"]["FingerprintJSD"] == 0.5894747764691485
    # ataqv writes tss_enrichment as null for a single-end library: no value, not zero.
    assert "TSSEnrichment" not in backfill["OSMOTIC_STRESS_T100_SE_REP1"]
    assert "FingerprintJSD" in backfill["OSMOTIC_STRESS_T100_SE_REP1"]
    assert result.unmapped == []
