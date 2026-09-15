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
