"""nf-core/eager 2.5.3, wired end to end: catalog, TSV sheet, template, atlases, row checks, map.

eager 2.5.3 is DSL1 and ships no assets/schema_input.json. Its TSV contract lives in
main.nf's `extract_data` (read at the 2.5.3 tag): the header must hold exactly these eleven
columns (`checkNumberOfItem(row, 11)` refuses any other count), every cell must be filled
("NA" for an optional one), SeqType is PE or SE, Colour_Chemistry 2 or 4, UDG_Treatment none,
half or full, and R1/R2 are FASTQ paths. EAGER_TSV_HEADER records that contract in place of a
schema_input.json fixture; the other four upstream files are committed byte for byte under
fixtures/nfcore/eager.*.
"""
import json
from pathlib import Path

import pytest

from NessieAI.paths import CHAT_NEXTSEEK_DIR
from chat_nextseek.seqera import emitter
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.input_kinds import read_samplesheet

FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"
TEMPLATES = CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore"
SEQERA = CHAT_NEXTSEEK_DIR / "src/chat_nextseek/seqera"

#: main.nf `extract_data` expected_keys at 2.5.3, in order.
EAGER_TSV_HEADER = ["Sample_Name", "Library_ID", "Lane", "Colour_Chemistry", "SeqType", "Organism",
                    "Strandedness", "UDG_Treatment", "R1", "R2", "BAM"]

R1 = "/net/bmc-pub/x/D.SEQ-1_L008_R1.fastq.gz"
R2 = "/net/bmc-pub/x/D.SEQ-1_L008_R2.fastq.gz"


def _entry():
    return NFCORE_PIPELINE_CATALOG["eager"]


# --- catalog ----------------------------------------------------------------------------------

def test_the_catalog_pins_2_5_3_on_the_last_dsl1_nextflow():
    entry = _entry()
    assert entry["default_revision"] == "2.5.3"
    assert entry["nextflow_version"] == "22.10.8"
    assert entry["samplesheet_format"] == "tsv"
    assert entry["samplesheet_input_kind"] == "fastq"
    assert entry["accepted_leaf_sample_types"] == ["D.SEQ"]


def test_required_columns_are_the_tsv_header_main_nf_checks():
    assert _entry()["required_columns"] == EAGER_TSV_HEADER
    template = json.loads((TEMPLATES / "eager.json").read_text())
    assert template["pipeline"]["required_columns"] == EAGER_TSV_HEADER
    assert list(template["samplesheet_template"][0]) == EAGER_TSV_HEADER


def test_the_aliases_rename_the_generic_columns_to_eagers():
    assert emitter.PIPELINE_COLUMN_ALIASES["eager"] == {
        "sample": "Sample_Name", "fastq_1": "R1", "fastq_2": "R2"}


def test_four_upstream_files_are_committed_and_no_schema_input_exists_upstream():
    for suffix in ("nextflow_schema.json", "output.md", "readme.md", "usage.md"):
        assert (FIXTURES / f"eager.{suffix}").is_file(), suffix
    assert not (FIXTURES / "eager.schema_input.json").exists()
    # usage.md documents the same header, in the same order.
    usage = (FIXTURES / "eager.usage.md").read_text()
    assert "| " + " | ".join(EAGER_TSV_HEADER) + " |" in usage


# --- the emitted TSV --------------------------------------------------------------------------

def _emit(tmp_path, rows, meta):
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline="eager", samplesheet_rows=rows, resolutions=[],
        accession_metadata=meta, launch_plan=None, tower_env={})
    return res, Path(res.saved_files["samplesheet"])


ROW = {"sample": "D.SEQ-1", "Lane": "8", "Colour_Chemistry": "2", "Strandedness": "double",
       "UDG_Treatment": "half", "cohort": "a"}


def test_the_sheet_is_samplesheet_tsv_with_exactly_the_eleven_columns(tmp_path):
    _, path = _emit(tmp_path, [ROW], {"D.SEQ-1": {"Link_PrimaryData": f"{R1};{R2}"}})
    assert path.name == "samplesheet.tsv"
    lines = path.read_text().splitlines()
    assert lines[0].split("\t") == EAGER_TSV_HEADER
    row = read_samplesheet(path)[0]
    assert row == {"Sample_Name": "D.SEQ-1", "Library_ID": "D.SEQ-1", "Lane": "8",
                   "Colour_Chemistry": "2", "SeqType": "PE", "Organism": "NA",
                   "Strandedness": "double", "UDG_Treatment": "half", "R1": R1, "R2": R2,
                   "BAM": "NA"}


def test_a_single_end_library_is_se_with_r2_na_whatever_the_row_said(tmp_path):
    _, path = _emit(tmp_path, [{**ROW, "SeqType": "PE"}], {"D.SEQ-1": {"Link_PrimaryData": R1}})
    row = read_samplesheet(path)[0]
    assert row["SeqType"] == "SE" and row["R1"] == R1 and row["R2"] == "NA"


def test_a_library_id_the_agent_gave_is_kept(tmp_path):
    _, path = _emit(tmp_path, [{**ROW, "Library_ID": "D.SEQ-1_libA", "Organism": "Homo_sapiens"}],
                    {"D.SEQ-1": {"Link_PrimaryData": f"{R1};{R2}"}})
    row = read_samplesheet(path)[0]
    assert row["Library_ID"] == "D.SEQ-1_libA" and row["Organism"] == "Homo_sapiens"


def test_no_cell_is_ever_blank(tmp_path):
    _, path = _emit(tmp_path, [ROW], {"D.SEQ-1": {"Link_PrimaryData": R1}})
    assert all(v.strip() for v in read_samplesheet(path)[0].values())


def test_a_sample_with_no_local_fastq_is_refused_not_written_as_na(tmp_path):
    """A TSV sheet cannot be filled by the fetchngs pre-stage, and R1=NA with BAM=NA is a row
    eager rejects, so write_samplesheet must stop instead of emitting it."""
    res, _ = _emit(tmp_path, [ROW], {"D.SEQ-1": {"File_PrimaryData": "SRR123"}})
    assert res.missing_input_files == ["D.SEQ-1"]


def test_the_cohort_sidecar_still_names_the_sample(tmp_path):
    _emit(tmp_path, [ROW], {"D.SEQ-1": {"Link_PrimaryData": R1}})
    cohort = json.loads((tmp_path / "cohort.json").read_text())
    assert "D.SEQ-1" in json.dumps(cohort)


# --- template -----------------------------------------------------------------------------------

def _template():
    return json.loads((TEMPLATES / "eager.json").read_text())


def _schema():
    return json.loads((FIXTURES / "eager.nextflow_schema.json").read_text())


def test_curated_defaults_pass_the_pinned_schema():
    from chat_nextseek.seqera.pipeline_params import build_run_params
    from chat_nextseek.seqera.schema_check import check_params
    merged, errors, _ = build_run_params("eager", {}, "GRCh38")
    assert errors == []
    errors, skipped = check_params("eager", "2.5.3", merged, required=True,
                                   schema_getter=lambda _p, _r: {"nextflow_schema": _schema()})
    assert skipped is None and errors == []


def test_every_menu_param_is_one_the_pinned_schema_declares():
    from chat_nextseek.seqera.schema_check import check_params
    menu = _template()["params"]
    errors, skipped = check_params("eager", "2.5.3", {name: None for name in menu}, required=False,
                                   schema_getter=lambda _p, _r: {"nextflow_schema": _schema()})
    assert skipped is None and errors == []


def test_every_curated_default_is_upstreams_default():
    """The owner's standing rule: the question and the data set a param; upstream's default is
    the fallback, never a curated "off unless asked"."""
    props = {k: v for group in _schema()["definitions"].values()
             for k, v in group["properties"].items()}
    for name, spec in _template()["params"].items():
        upstream = props[name].get("default")
        ours = spec.get("default")
        if upstream is None:
            assert ours in (None, False), (name, ours)
        else:
            assert ours == upstream, (name, ours, upstream)


def test_every_optional_param_says_what_question_or_data_calls_for_it():
    for name, spec in _template()["params"].items():
        text = spec.get("help") or ""
        assert len(text) > 40, name
        assert "unless asked" not in text and "old behaviour" not in text, name


def test_the_metagenomic_screen_is_not_offered_until_a_database_is_staged():
    menu = _template()["params"]
    for name in ("run_metagenomic_screening", "metagenomic_tool", "database", "run_maltextract"):
        assert name not in menu
    assert any("metagenomic" in r and "not staged" in r for r in _template()["validation_rules"])


# --- run-param checks that eager 2.5.3 enforces only by exiting (main.nf) ----------------------

@pytest.mark.parametrize("params,needle", [
    ({"run_genotyping": True}, "genotyping_tool"),
    ({"bam_mapping_quality_threshold": 25}, "run_bam_filtering"),
    ({"run_genotyping": True, "genotyping_tool": "ug", "genotyping_source": "trimmed"}, "run_trim_bam"),
    ({"run_genotyping": True, "genotyping_tool": "ug", "genotyping_source": "pmd"}, "run_pmdtools"),
])
def test_a_combination_eager_rejects_is_refused_before_launch(params, needle):
    from chat_nextseek.seqera.pipeline_params import build_run_params
    _, errors, _ = build_run_params("eager", params, "GRCh38")
    assert errors and any(needle in e for e in errors), errors


def test_a_valid_genotyping_request_builds():
    from chat_nextseek.seqera.pipeline_params import build_run_params
    merged, errors, _ = build_run_params(
        "eager", {"run_genotyping": True, "genotyping_tool": "ug", "run_bam_filtering": True,
                  "bam_mapping_quality_threshold": 25}, "GRCh38")
    assert errors == [] and merged["genotyping_tool"] == "ug"


# --- the param atlas: row columns read from the library's metadata ------------------------------

def _rule(name):
    from chat_nextseek.seqera.param_atlas import data_driven_params
    return data_driven_params("eager")[name]


@pytest.mark.parametrize("sequencer,verdict,value", [
    ("Illumina NovaSeq 6000", "derived_uncorroborated", "2"),
    ("Illumina NextSeq 500", "derived_uncorroborated", "2"),
    ("Illumina HiSeq 4000", "derived_uncorroborated", "4"),
    ("MiSeq", "derived_uncorroborated", "4"),
    ("MiniSeq", "derived_uncorroborated", "2"),
    (None, "defaulted", "4"),
])
def test_colour_chemistry_reads_the_sequencer(sequencer, verdict, value):
    from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param
    signals = {"Sequencer": sequencer} if sequencer else {}
    out = evaluate_data_driven_param(_rule("Colour_Chemistry"), signals)
    assert out["verdict"] == verdict and out["value"] == value


@pytest.mark.parametrize("signals,verdict,value", [
    ({"LibraryType": "half-UDG double-stranded aDNA library"}, "derived_uncorroborated", "half"),
    ({"LibraryType": "non-UDG"}, "derived_uncorroborated", "none"),
    ({"LibraryType": "full UDG", "Protocol": "USER enzyme full UDG treatment"}, "corroborated", "full"),
    ({"LibraryType": "full UDG", "Protocol": "half-UDG"}, "conflict", None),
    ({}, "absent", None),
])
def test_udg_treatment_is_derived_and_asked_when_absent(signals, verdict, value):
    from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param
    out = evaluate_data_driven_param(_rule("UDG_Treatment"), signals)
    assert out["verdict"] == verdict
    if verdict != "conflict":
        assert out["value"] == value


@pytest.mark.parametrize("signals,verdict,value", [
    ({"LibraryType": "single-stranded library (ssDNA)"}, "derived_uncorroborated", "single"),
    ({"LibraryType": "double-stranded"}, "derived_uncorroborated", "double"),
    ({}, "defaulted", "double"),
])
def test_strandedness_is_the_library_build_not_rna_strandedness(signals, verdict, value):
    from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param
    rule = _rule("Strandedness")
    assert rule["derive_from"]["signal"] != "Strandedness"  # D.SEQ Strandedness is RNA-seq's
    out = evaluate_data_driven_param(rule, signals)
    assert out["verdict"] == verdict and out["value"] == value


# --- write_samplesheet's row check --------------------------------------------------------------

def test_a_blank_data_driven_cell_is_refused_and_names_the_metadata_verdict():
    from chat_nextseek.seqera.pipeline_checks import row_errors
    evidence = {"D.SEQ-1": {"Colour_Chemistry": {"value": "4", "verdict": "defaulted"}}}
    errors = row_errors("eager", [{**ROW, "Colour_Chemistry": ""}], evidence)
    assert len(errors) == 1 and "Colour_Chemistry" in errors[0] and "'4'" in errors[0]


@pytest.mark.parametrize("bad", [{"Colour_Chemistry": "3"}, {"UDG_Treatment": "partial"},
                                 {"Strandedness": "ss"}, {"Lane": ""}, {"Lane": "lane 8"}])
def test_a_value_eager_rejects_is_refused(bad):
    from chat_nextseek.seqera.pipeline_checks import row_errors
    assert row_errors("eager", [{**ROW, **bad}], {})


def test_a_complete_row_passes():
    from chat_nextseek.seqera.pipeline_checks import row_errors
    assert row_errors("eager", [ROW], {}) == []


# --- atlas and reingest map ---------------------------------------------------------------------

def test_eager_is_a_dna_atlas_pipeline_at_its_catalog_pin():
    from chat_nextseek.seqera.nfcore_atlas import DNA_ATLAS_PATH, load_atlas
    entry = load_atlas(DNA_ATLAS_PATH)["pipelines"]["eager"]
    assert entry["revision"] == "2.5.3"
    text = " ".join([entry["answers"], entry["assumes_prep"], entry["input_you_must_have"]]).lower()
    for word in ("damage", "authentic", "contamination", "metagenomic"):
        assert word in text, word


def test_eager_and_sarek_point_at_each_other_with_a_question():
    from chat_nextseek.seqera.nfcore_atlas import load_atlas
    pipelines = load_atlas()["pipelines"]
    assert pipelines["eager"]["versus"]["sarek"]["ask_user"]
    assert pipelines["sarek"]["versus"]["eager"]["ask_user"]
    assert "modern" in pipelines["eager"]["versus"]["sarek"]["differs_by"].lower()


def test_the_map_covers_the_pinned_release():
    from NessieAI.ns.reingest import maps
    assert maps.load("eager").covers_revision("2.5.3")
