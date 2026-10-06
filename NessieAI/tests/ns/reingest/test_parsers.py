from pathlib import Path

import pytest

from NessieAI.ns.reingest import parsers

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nfcore_rnaseq_run"

# Applied per-test (not module-wide) so tests that use only inline text --
# like the collision test below -- still RUN where the fixture is absent
# (e.g. CI), instead of being skipped along with the fixture-reading ones.
needs_fixture = pytest.mark.skipif(
    not FIXTURE.is_dir(),
    reason="nf-core run fixture is local-only; see the fixture README")


def _params_path():
    # nf-core writes params_<timestamp>.json, not a fixed "params.json"; glob
    # so this survives a differently-timestamped run.
    return next(iter(sorted((FIXTURE / "pipeline_info").glob("params*.json"))))


@needs_fixture
def test_parse_params_returns_every_resolved_param():
    params = parsers.parse_params(_params_path().read_text())
    assert params["aligner"] == "star_salmon"
    assert "genome" in params


@needs_fixture
def test_parse_software_versions_flattens_process_nesting():
    # 3.22 names this nf_core_<pipeline>_software_mqc_versions.yml, NOT
    # software_versions.yml, and nests PROCESS -> {tool: version}.
    versions = parsers.parse_software_versions(
        (FIXTURE / "pipeline_info"
         / "nf_core_rnaseq_software_mqc_versions.yml").read_text())
    # STAR_ALIGN nests "star" (lowercase) in the real file; assert the exact
    # key and value rather than hedging across spellings.
    assert versions["star"] == "2.7.11b"
    assert versions["fastqc"] == "0.12.1"
    assert all(isinstance(v, str) for v in versions.values())


@needs_fixture
def test_parse_software_versions_flattens_the_workflow_block_too():
    # The Workflow block is itself PROCESS -> {tool: version} shaped in this
    # file (nf-core/rnaseq and Nextflow nested under "Workflow"), so it
    # flattens through the same branch as every process block.
    versions = parsers.parse_software_versions(
        (FIXTURE / "pipeline_info"
         / "nf_core_rnaseq_software_mqc_versions.yml").read_text())
    assert versions["Nextflow"] == "25.10.2"
    assert versions["nf-core/rnaseq"] == "v3.22.2-g3816d48"


def test_parse_software_versions_collision_across_processes():
    # Inline text, NOT the fixture, so this runs even where the fixture is
    # absent (e.g. CI): two processes legitimately report the same tool
    # ("star") at two different versions, mirroring MAKE_TRANSCRIPTS_FASTA
    # (2.7.10a) vs STAR_ALIGN (2.7.11b) in the real 3.22 fixture.
    text = (
        "MAKE_TRANSCRIPTS_FASTA:\n"
        "  star: 2.7.10a\n"
        "STAR_ALIGN:\n"
        "  star: 2.7.11b\n"
        "  samtools: 1.21\n")

    # The flat map documents its winner: last in file (dict-iteration) order.
    flat = parsers.parse_software_versions(text)
    assert flat["star"] == "2.7.11b"
    assert flat["samtools"] == "1.21"

    # The by-process map keeps both versions intact -- nothing discarded.
    by_process = parsers.parse_software_versions_by_process(text)
    assert by_process["MAKE_TRANSCRIPTS_FASTA"]["star"] == "2.7.10a"
    assert by_process["STAR_ALIGN"]["star"] == "2.7.11b"

    # The conflicts helper names the collision, in first-seen order, and
    # says nothing about a tool that never disagreed with itself.
    conflicts = parsers.software_version_conflicts(text)
    assert conflicts == {"star": ["2.7.10a", "2.7.11b"]}
    assert "samtools" not in conflicts


def test_software_versions_ignore_surrounding_whitespace():
    # nf-core/scrnaseq 4.1.0 writes CELLRANGER_COUNT's version quoted with a
    # trailing space ('9.0.1 ') and the other CELLRANGER_* processes without
    # one. Padding is not a version: it must not raise a conflict, and every
    # view must report the bare value, whichever pipeline wrote the padding.
    text = (
        "CELLRANGER_COUNT:\n"
        "  cellranger: '9.0.1 '\n"
        "CELLRANGER_MKGTF:\n"
        "  cellranger: 9.0.1\n"
        "' PADDED_PROCESS ':\n"
        "  ' samtools\t': \"\t1.21\\n\"\n"
        "Workflow:\n"
        "  nf-core/scrnaseq: 'v4.1.0 '\n")

    assert parsers.software_version_conflicts(text) == {}

    flat = parsers.parse_software_versions(text)
    assert flat["cellranger"] == "9.0.1"
    assert flat["samtools"] == "1.21"
    assert flat["nf-core/scrnaseq"] == "v4.1.0"

    by_process = parsers.parse_software_versions_by_process(text)
    assert by_process["CELLRANGER_COUNT"] == {"cellranger": "9.0.1"}
    assert by_process["PADDED_PROCESS"] == {"samtools": "1.21"}
    assert by_process["Workflow"] == {"nf-core/scrnaseq": "v4.1.0"}


def test_parse_samplesheet_reads_sample_and_fastq_columns():
    # nf-core 3.22 writes NO samplesheet into the results directory; the input
    # samplesheet lives beside nextflow.config in the run directory above it.
    rows = parsers.parse_samplesheet(
        "sample,fastq_1,fastq_2,strandedness\n"
        "CONTROL_REP1,/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz,"
        "/net/cluster/fastq/CONTROL_REP1_R2.fastq.gz,auto\n")
    assert [r["sample"] for r in rows] == ["CONTROL_REP1"]
    assert rows[0]["fastq_1"] == "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz"


@needs_fixture
def test_parse_execution_trace_counts_failures():
    trace = parsers.parse_execution_trace(
        (FIXTURE / "pipeline_info" / "execution_trace.txt").read_text())
    assert trace["processes"] > 0
    assert trace["failed"] == 0
    assert trace["non_terminal"] == 0


@needs_fixture
def test_parse_general_stats_keys_by_sample_and_keeps_column_names_verbatim():
    # The data directory is multiqc_report_data (MultiQC names it after the
    # report file), and columns are spelled <module>-<metric>. The sample
    # row itself carries no fastqc_raw-* columns -- those are populated only
    # on the per-read rows (see the next test) -- so verbatim spelling is
    # checked here against columns the real header actually puts on a
    # biological sample's own row.
    stats = parsers.parse_general_stats(
        (FIXTURE / "multiqc" / "star_salmon" / "multiqc_report_data"
         / "multiqc_general_stats.txt").read_text())
    assert "CONTROL_REP1" in stats
    row = stats["CONTROL_REP1"]
    assert "star-uniquely_mapped_percent" in row
    assert "qualimap_rnaseq-5_3_bias" in row
    assert isinstance(row["star-uniquely_mapped_percent"], float)
    assert "fastqc_raw-total_sequences" not in row


@needs_fixture
def test_parse_general_stats_keeps_per_read_rows_distinct_from_samples():
    # MultiQC emits <sample>_1 / <sample>_2 rows carrying only FastQC columns
    # (populated) and every other column blank/omitted. They are real rows
    # and must not be silently merged into the sample. Checking both the
    # presence of a fastqc column AND the absence of a star column on the
    # same row is what actually distinguishes the two kinds of row, rather
    # than just checking that both keys exist in the result.
    stats = parsers.parse_general_stats(
        (FIXTURE / "multiqc" / "star_salmon" / "multiqc_report_data"
         / "multiqc_general_stats.txt").read_text())
    assert "CONTROL_REP1_1" in stats
    per_read_row = stats["CONTROL_REP1_1"]
    assert "fastqc_raw-total_sequences" in per_read_row
    assert "star-uniquely_mapped_percent" not in per_read_row
    assert "star-uniquely_mapped_percent" in stats["CONTROL_REP1"]
    assert "fastqc_raw-total_sequences" not in stats["CONTROL_REP1"]


def test_parse_general_stats_tolerates_blank_cells():
    text = "Sample\tA_x\tB_y\nS1\t1.5\t\nS2\t\t3\n"
    stats = parsers.parse_general_stats(text)
    assert stats["S1"] == {"A_x": 1.5}
    assert stats["S2"] == {"B_y": 3.0}


def _trace(*rows):
    return "task_id\thash\tname\tstatus\texit\n" + "".join(
        f"{i}\t{i:02d}/abcdef\t{name}\t{status}\t{0 if status == 'COMPLETED' else 143}\n"
        for i, (name, status) in enumerate(rows, start=1))


def test_a_task_that_failed_then_passed_on_retry_is_recovered_not_failed():
    trace = parsers.parse_execution_trace(_trace(
        ("STAR_ALIGN (S1)", "FAILED"),
        ("STAR_ALIGN (S1)", "COMPLETED"),
        ("MULTIQC", "COMPLETED")))
    assert trace["failed"] == 0
    assert trace["recovered"] == 1
    assert trace["recovered_tasks"] == ["STAR_ALIGN (S1)"]


def test_a_task_whose_last_row_failed_still_counts():
    trace = parsers.parse_execution_trace(_trace(
        ("STAR_ALIGN (S1)", "COMPLETED"),
        ("STAR_ALIGN (S2)", "FAILED"),
        ("STAR_ALIGN (S2)", "FAILED"),
        ("MULTIQC", "ABORTED")))
    assert trace["failed"] == 3
    assert trace["recovered"] == 0
    assert trace["recovered_tasks"] == []


def test_the_251112_retry_pattern_is_fully_recovered():
    # The real nf-core/scrnaseq 4.1.0 shape: four rows share one name, the
    # two FAILED ones (exit 143) between two COMPLETED ones. A cached row
    # counts as a success as well.
    trace = parsers.parse_execution_trace(_trace(
        ("CELLRANGER_COUNT (S1)", "CACHED"),
        ("ANNDATAR_CONVERT (combined)", "COMPLETED"),
        ("ANNDATAR_CONVERT (combined)", "FAILED"),
        ("ANNDATAR_CONVERT (combined)", "FAILED"),
        ("ANNDATAR_CONVERT (combined)", "COMPLETED")))
    assert (trace["failed"], trace["recovered"]) == (0, 2)
    assert trace["recovered_tasks"] == ["ANNDATAR_CONVERT (combined)"]


def test_a_trace_without_a_name_column_counts_every_failure():
    trace = parsers.parse_execution_trace("task_id\tstatus\n1\tFAILED\n2\tCOMPLETED\n")
    assert (trace["failed"], trace["recovered"]) == (1, 0)


@pytest.mark.parametrize("sentence, kind", [
    ("Workflow execution completed successfully!", parsers.RUN_SUCCEEDED),
    ("Workflow execution completed successfully. One or more tasks reported an error condition.",
     parsers.RUN_SUCCEEDED_IGNORING_ERRORS),
    ("Workflow execution completed unsuccessfully!", parsers.RUN_FAILED),
    ("  Workflow execution completed successfully!\n", parsers.RUN_SUCCEEDED),
    ("something else entirely", None),
    ("", None),
])
def test_nextflow_report_verdicts_are_classified(sentence, kind):
    # The three sentences are Nextflow's own (ReportTemplate.html). The middle
    # one means a task FAILED AND WAS IGNORED, not that a retry happened:
    # retries never trigger it.
    assert parsers.classify_run_verdict(sentence) == kind


# --- nf-core/differentialabundance contrasts files: the direction of every
# contrast (which group is the reference). CSV/TSV in 1.x and 2.0.0, YAML in
# 2.0.0, where a formula-based contrast has no single reference or target. ---

def test_parse_contrasts_reads_the_csv_columns():
    got = parsers.parse_contrasts(
        "id,variable,reference,target,blocking\n"
        "LPS_vs_Ctrl,Condition,Ctrl,LPS,Donor\n"
        "MUC2_vs_Ctrl,Condition,Ctrl,MUC2,\n", "250818Rib_contrasts.csv")
    assert got == {
        "LPS_vs_Ctrl": {"variable": "Condition", "reference": "Ctrl", "target": "LPS", "blocking": "Donor"},
        "MUC2_vs_Ctrl": {"variable": "Condition", "reference": "Ctrl", "target": "MUC2", "blocking": ""},
    }


def test_parse_contrasts_reads_a_tsv():
    got = parsers.parse_contrasts("id\tvariable\treference\ttarget\nKO_vs_WT\tgenotype\tWT\tKO\n",
                                  "contrasts.tsv")
    assert got["KO_vs_WT"]["reference"] == "WT"


def test_parse_contrasts_reads_yaml_comparisons_and_leaves_formula_contrasts_blank():
    got = parsers.parse_contrasts(
        "contrasts:\n"
        "  - id: condition_control_treated_blockrep\n"
        "    comparison: [\"condition\", \"control\", \"treated\"]\n"
        "    blocking_factors: [\"replicate\", \"batch\"]\n"
        "  - id: treatment_mCherry_hND6_sample_number\n"
        "    make_contrasts_str: \"treatmenthND6 - treatmentmCherry\"\n"
        "    formula: \"~ 0 + treatment + sample_number\"\n", "x.contrasts.yaml")
    assert got["condition_control_treated_blockrep"] == {
        "variable": "condition", "reference": "control", "target": "treated",
        "blocking": "replicate;batch"}
    # A formula contrast states no single reference group; nothing is guessed.
    assert got["treatment_mCherry_hND6_sample_number"] == {
        "variable": "", "reference": "", "target": "", "blocking": ""}


def test_a_file_that_is_not_a_contrasts_file_parses_to_nothing():
    assert parsers.parse_contrasts("sample,fastq_1\nA,a.fq.gz\n", "samplesheet.csv") == {}
