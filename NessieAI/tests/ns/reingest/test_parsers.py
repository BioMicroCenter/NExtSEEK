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
