"""atacseq and chipseq QC from MultiQC module tables, on synthetic tables.

Both releases exclude MultiQC's general-stats table and write their data files as YAML
(`data_format: yaml`), so their maps read QC from `module_tables`: Picard MarkDuplicates'
`multiqc_picard_dups` gives DuplicationPercent through `$derived.duplication_pct`. The
tables here are shaped like MultiQC 1.13's picard module output (rows after the configs'
extra_fn_clean_exts, Picard's 0-1 fraction); no real run of either release has been seen,
so the shape is unverified until one is (README "Maps written from the docs alone").
"""
import json
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
