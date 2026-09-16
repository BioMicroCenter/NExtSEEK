import json
import shutil

from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, manifest

pytestmark = pytest.mark.django_db
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nfcore_rnaseq_run"
GOLDEN = Path(__file__).parent / "golden_manifest.json"

needs_fixture = pytest.mark.skipif(
    not FIXTURE.is_dir(),
    reason="nf-core run fixture is local-only; see the fixture README")


needs_golden = pytest.mark.skipif(
    not GOLDEN.is_file(),
    reason="golden_manifest.json derives measurements from the uncommitted run "
           "fixture and is local-only; see the fixture README")


@needs_fixture
@needs_golden
def test_harvest_matches_the_golden_manifest():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: []).model_dump()
    got["run_dir"] = "<fixture>"
    assert got == json.loads(GOLDEN.read_text())


@needs_fixture
def test_metric_keys_are_not_renamed():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert sample.metrics, "expected general-stats metrics for CONTROL_REP1"
    # Two MultiQC modules spell a near-identical metric two different ways
    # in the real fixture -- star's own convention is "..._percent" while
    # samtools_flagstat's is "..._pct". Both must survive verbatim: neither
    # gets normalised to match the other, and neither is dropped.
    assert sample.metrics["star-uniquely_mapped_percent"] == pytest.approx(89.16)
    assert sample.metrics["samtools_flagstat-mapped_passed_pct"] == pytest.approx(100.0)


@needs_fixture
def test_sources_names_a_file_for_every_top_level_section():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    assert got.sources["params"].endswith("params_2026-01-26_13-53-31.json")
    assert got.sources["software_versions"].endswith(
        "nf_core_rnaseq_software_mqc_versions.yml")
    assert got.sources["execution"].endswith("execution_trace.txt")
    assert got.sources["metrics"].endswith("multiqc_general_stats.txt")


@needs_fixture
def test_a_finished_run_is_marked_complete():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    assert got.run_status == manifest.RUN_COMPLETE


@needs_fixture
def test_a_missing_software_versions_makes_the_run_incomplete(tmp_path):
    copy = tmp_path / "run"
    shutil.copytree(FIXTURE, copy)
    (copy / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").unlink()
    got = harvest.harvest_local(str(copy), lookup_by_fastq=lambda p: [])
    assert got.run_status == manifest.RUN_INCOMPLETE


@needs_fixture
def test_a_missing_execution_trace_makes_the_run_incomplete(tmp_path):
    # Versions present, trace absent entirely (not staged, deleted, or
    # cap-truncated) must NOT read as "no non-terminal process found" --
    # that would wave through a run whose completion was never observed.
    copy = tmp_path / "run"
    shutil.copytree(FIXTURE, copy)
    (copy / "pipeline_info" / "execution_trace.txt").unlink()
    got = harvest.harvest_local(str(copy), lookup_by_fastq=lambda p: [])
    assert got.run_status == manifest.RUN_INCOMPLETE


@needs_fixture
def test_a_failed_run_is_marked_failed_so_the_op_can_refuse(tmp_path):
    copy = tmp_path / "run"
    shutil.copytree(FIXTURE, copy)
    trace = copy / "pipeline_info" / "execution_trace.txt"
    lines = trace.read_text().splitlines()
    lines[1] = lines[1].replace("COMPLETED", "FAILED")
    trace.write_text("\n".join(lines) + "\n")
    got = harvest.harvest_local(str(copy), lookup_by_fastq=lambda p: [])
    assert got.execution.failed == 1


@needs_fixture
def test_caps_record_what_was_read():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    assert got.caps.files_read > 0
    assert got.caps.bytes_read > 0
    assert got.caps.truncated == []


@needs_fixture
def test_software_version_conflict_is_surfaced_as_a_warning():
    # In the real 3.22.2 fixture, THREE tools are reported at more than one
    # distinct version across processes: "star" (MAKE_TRANSCRIPTS_FASTA
    # 2.7.10a vs STAR_ALIGN 2.7.11b), "python" (CUSTOM_TX2GENE 3.10.4 vs
    # GTF_FILTER/MULTIQC_CUSTOM_BIOTYPE 3.9.5), and "ucsc" (UCSC_BEDCLIP 377
    # vs UCSC_BEDGRAPHTOBIGWIG 469). The flat `software_versions` map keeps
    # only the last-seen value for each; none of the three conflicts may be
    # silently lost.
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    assert got.warnings == [
        "python: conflicting versions across processes (3.10.4, 3.9.5)",
        "star: conflicting versions across processes (2.7.10a, 2.7.11b)",
        "ucsc: conflicting versions across processes (377, 469)",
        "no validated samplesheet; sample names came from multiqc general "
        "stats, so fastq-based D.SEQ UID resolution is unavailable for them",
    ]
    assert got.software_versions["star"] == "2.7.11b"
    assert got.software_versions_by_process["MAKE_TRANSCRIPTS_FASTA"]["star"] == "2.7.10a"
    assert got.software_versions_by_process["STAR_ALIGN"]["star"] == "2.7.11b"


@needs_fixture
def test_samples_source_is_recorded_even_via_the_general_stats_fallback():
    # Samples WERE identified (from multiqc general stats) even though there
    # is no samplesheet to resolve their D.SEQ UIDs from -- sources["samples"]
    # must name the file the names actually came from, not be absent.
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    assert got.sources["samples"] == got.sources["metrics"]
    assert got.sources["samples"].endswith("multiqc_general_stats.txt")


@needs_fixture
def test_per_sample_derived_metrics_include_sense_antisense_ratio():
    # sense_antisense_ratio comes from parse_infer_experiment, fed from
    # <aligner>/rseqc/infer_experiment/<sample>.infer_experiment.txt -- not
    # just read_distribution's four percentages.
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p: [])
    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert "sense_antisense_ratio" in sample.derived
    assert "cds_pct" in sample.derived
    # STAR and counts are not in the harvest allowlist -- these metrics must
    # be omitted, not invented.
    assert "unaligned_reads" not in sample.derived
    assert "genes_detected" not in sample.derived


def test_an_oversized_per_sample_file_is_capped_not_read(tmp_path, monkeypatch):
    # RSeQC writes some per-sample QC files at ~100 MB in real runs. Simulate
    # that here by inflating one sample's read_distribution.txt past the cap
    # and asserting harvest_local records the skip and never reads the file.
    monkeypatch.setattr(harvest, "MAX_FILE_BYTES", 1_000)

    root = tmp_path / "run"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").write_text(
        "FASTQC:\n  fastqc: 0.12.1\nWorkflow:\n  nf-core/rnaseq: v3.22.2\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    stats_dir = root / "multiqc" / "star_salmon" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tstar-uniquely_mapped_percent\n"
        "CONTROL_REP1\t89.16\n")

    oversized_dir = root / "star_salmon" / "rseqc" / "read_distribution"
    oversized_dir.mkdir(parents=True)
    oversized_rel = "star_salmon/rseqc/read_distribution/CONTROL_REP1.read_distribution.txt"
    (root / oversized_rel).write_text("Total Assigned Tags 100\n" + "x" * 2_000)

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p: [])

    assert oversized_rel in got.caps.truncated
    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert sample.derived == {}, "the oversized file must not be read at all"


def test_every_glob_the_harvester_reads_is_in_generic_globs():
    # GENERIC_GLOBS is what the run-harvest op stages off the cluster (it
    # iterates the tuple verbatim over SSH -- see Task 9 in
    # docs/superpowers/plans/2026-09-15-nfcore-reingest-1-harvest.md). Every
    # pattern harvest_local actually globs against locally must be a member,
    # or a real staged run would never contain the file it tries to read.
    used_patterns = {
        harvest._PARAMS_GLOB,
        harvest._SOFTWARE_VERSIONS_GLOB,
        harvest._EXECUTION_TRACE_GLOB,
        harvest._SAMPLESHEET_GLOB,
        harvest._MULTIQC_TXT_GLOB,
        harvest._RSEQC_READ_DISTRIBUTION_GLOB,
        harvest._RSEQC_INFER_EXPERIMENT_GLOB,
    }
    assert used_patterns <= set(harvest.GENERIC_GLOBS)


def test_general_stats_fallback_keeps_a_real_sample_named_with_a_replicate_suffix(tmp_path):
    # "A_1" is a real sample name here (a numeric-replicate samplesheet
    # convention), not MultiQC's per-read row for sample "A" -- it has a
    # populated star- column, which MultiQC never fills in for a per-read
    # row. A name-only heuristic ("ends _1 and A is also a row") would have
    # dropped it silently; _is_per_read_row must not.
    root = tmp_path / "run"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/rnaseq: v3.22.2\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    stats_dir = root / "multiqc" / "star_salmon" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tstar-uniquely_mapped_percent\tfastqc_raw-percent_gc\n"
        "A\t89.16\t\n"
        "A_1\t91.0\t48.0\n"
        # a genuine MultiQC per-read row: only fastqc/cutadapt columns.
        "A_2\t\t47.5\n")

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p: [])

    names = {s.nfcore_sample for s in got.samples}
    assert names == {"A", "A_1"}


def test_extra_globs_are_read_and_recorded_as_outputs(tmp_path):
    root = tmp_path / "run"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/rnaseq: v3.22.2\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    (root / "extra_thing.txt").write_text("hello")

    got = harvest.harvest_local(
        str(root), extra_globs=["*.txt"], lookup_by_fastq=lambda p: [])

    assert [o.path for o in got.outputs] == ["extra_thing.txt"]
    assert got.outputs[0].bytes == len(b"hello")
