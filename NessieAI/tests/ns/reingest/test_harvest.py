import fnmatch
import json
import shutil

from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, manifest, mapper, maps

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
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: []).model_dump()
    got["run_dir"] = "<fixture>"
    assert got == json.loads(GOLDEN.read_text())


@needs_fixture
def test_metric_keys_are_not_renamed():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
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
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
    assert got.sources["params"].endswith("params_2026-01-26_13-53-31.json")
    assert got.sources["software_versions"].endswith(
        "nf_core_rnaseq_software_mqc_versions.yml")
    assert got.sources["execution"].endswith("execution_trace.txt")
    assert got.sources["metrics"].endswith("multiqc_general_stats.txt")


@needs_fixture
def test_a_finished_run_is_marked_complete():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
    assert got.run_status == manifest.RUN_COMPLETE


@needs_fixture
def test_a_missing_software_versions_makes_the_run_incomplete(tmp_path):
    copy = tmp_path / "run"
    shutil.copytree(FIXTURE, copy)
    (copy / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").unlink()
    got = harvest.harvest_local(str(copy), lookup_by_fastq=lambda p, types=None: [])
    assert got.run_status == manifest.RUN_INCOMPLETE


@needs_fixture
def test_a_missing_execution_trace_makes_the_run_incomplete(tmp_path):
    # Versions present, trace absent entirely (not staged, deleted, or
    # cap-truncated) must NOT read as "no non-terminal process found" --
    # that would wave through a run whose completion was never observed.
    copy = tmp_path / "run"
    shutil.copytree(FIXTURE, copy)
    (copy / "pipeline_info" / "execution_trace.txt").unlink()
    got = harvest.harvest_local(str(copy), lookup_by_fastq=lambda p, types=None: [])
    assert got.run_status == manifest.RUN_INCOMPLETE


@needs_fixture
def test_a_failed_run_is_marked_failed_so_the_op_can_refuse(tmp_path):
    copy = tmp_path / "run"
    shutil.copytree(FIXTURE, copy)
    trace = copy / "pipeline_info" / "execution_trace.txt"
    lines = trace.read_text().splitlines()
    lines[1] = lines[1].replace("COMPLETED", "FAILED")
    trace.write_text("\n".join(lines) + "\n")
    got = harvest.harvest_local(str(copy), lookup_by_fastq=lambda p, types=None: [])
    assert got.execution.failed == 1


@needs_fixture
def test_caps_record_what_was_read():
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
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
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
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
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
    assert got.sources["samples"] == got.sources["metrics"]
    assert got.sources["samples"].endswith("multiqc_general_stats.txt")


@needs_fixture
def test_per_sample_derived_metrics_include_sense_antisense_ratio():
    # sense_antisense_ratio comes from parse_infer_experiment, fed from
    # <aligner>/rseqc/infer_experiment/<sample>.infer_experiment.txt -- not
    # just read_distribution's four percentages.
    got = harvest.harvest_local(str(FIXTURE), lookup_by_fastq=lambda p, types=None: [])
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

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    assert oversized_rel in got.caps.truncated
    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert sample.derived == {}, "the oversized file must not be read at all"


def test_every_glob_the_harvester_reads_is_in_generic_globs(tmp_path, monkeypatch):
    # GENERIC_GLOBS is what the run-harvest op stages off the cluster (it
    # iterates the tuple verbatim over SSH -- see Task 9 in
    # docs/superpowers/plans/2026-09-15-nfcore-reingest-1-harvest.md). Every
    # pattern harvest_local actually globs against locally must be a member
    # (or a narrowing of one -- see below), or a real staged run would never
    # contain the file it tries to read.
    #
    # The previous version of this test read harvest._PARAMS_GLOB and its
    # siblings back into a set and checked that against harvest.GENERIC_GLOBS,
    # built from those SAME module constants -- true by construction. It
    # would stay green even if a future call site passed a raw literal that
    # never went through any of these constants, or a new constant that
    # never made it into GENERIC_GLOBS -- exactly the bug this test exists
    # to catch. This version instead monkeypatches Path.glob for the
    # duration of one harvest_local call and records every pattern actually
    # passed to it, so it watches reality rather than repeating the module's
    # own constants back at itself.
    #
    # A per-sample RSeQC pattern (e.g.
    # "*/rseqc/read_distribution/CONTROL_REP1.read_distribution.txt", built
    # by _rseqc_glob_for_sample) is not byte-identical to its GENERIC_GLOBS
    # template ("*/rseqc/read_distribution/*.read_distribution.txt"), but it
    # is a legitimate narrowing of it: the remote staging side globs the
    # wildcard template, which already covers every per-sample file the
    # narrowed local pattern could match. So an observed pattern counts as
    # covered when it fnmatches some GENERIC_GLOBS entry (fnmatch's `*`
    # matches any substring, including one made of literal sample-name
    # characters), not only on exact string equality.
    root = tmp_path / "run"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/rnaseq: v3.22.2\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    stats_dir = root / "multiqc" / "star_salmon" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tstar-uniquely_mapped_percent\n"
        "CONTROL_REP1\t89.16\n")

    observed: list[str] = []
    original_glob = Path.glob

    def recording_glob(self, pattern, *args, **kwargs):
        observed.append(pattern)
        return original_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", recording_glob)

    harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    assert observed, "harvest_local must have called Path.glob at least once"
    uncovered = [pattern for pattern in set(observed)
                 if not any(fnmatch.fnmatch(pattern, generic)
                            for generic in harvest.GENERIC_GLOBS)]
    assert uncovered == []


def test_general_stats_fallback_keeps_a_real_sample_named_with_a_replicate_suffix(tmp_path):
    # "A_1" is a real sample name here (a numeric-replicate samplesheet
    # convention), not MultiQC's per-read row for sample "A" -- it has a
    # populated star- column, which MultiQC never fills in for a per-read
    # row. A name-only heuristic ("ends _1 and A is also a row") would have
    # dropped it silently; _classify_general_stats_row must not.
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

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    names = {s.nfcore_sample for s in got.samples}
    assert names == {"A", "A_1"}


def test_general_stats_fallback_keeps_a_failed_alignment_sample(tmp_path):
    # FAILED_REP1's alignment step never ran, so star/salmon/samtools_*/
    # qualimap_* -- the columns that would otherwise mark it as a real
    # sample -- were never written for it. What DID run (fastqc on the raw
    # and trimmed reads, cutadapt) still produces per-mate rows
    # "FAILED_REP1_1"/"FAILED_REP1_2", exactly as MultiQC would for a
    # per-read split of a sample that succeeded -- but there is no bare
    # "FAILED_REP1" row here, because no per-sample module ever ran to
    # produce one. That missing sibling is what must keep both rows from
    # being silently classified as MultiQC's own per-read artifacts and
    # dropped: a column-signature-only check (the pre-fix _is_per_read_row)
    # cannot tell this apart from a genuine per-read pair and would drop the
    # only evidence this sample was ever attempted.
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
        "CONTROL_REP1\t89.16\t\n"  # a real, completed sample for contrast
        "FAILED_REP1_1\t\t47.0\n"
        "FAILED_REP1_2\t\t46.5\n")

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    names = {s.nfcore_sample for s in got.samples}
    assert names == {"CONTROL_REP1", "FAILED_REP1_1", "FAILED_REP1_2"}
    failed = next(s for s in got.samples if s.nfcore_sample == "FAILED_REP1_1")
    # No RSeQC files exist for FAILED_REP1_1 (alignment never produced any),
    # so its per-sample derived metrics are empty -- not invented.
    assert failed.derived == {}
    assert any("FAILED_REP1_1" in w for w in got.warnings)
    assert any("FAILED_REP1_2" in w for w in got.warnings)


def _minimal_run(root):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/rnaseq: v3.22.2\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")


def test_a_purely_local_caller_without_an_inventory_still_works(tmp_path):
    # harvest_local stays local-only and cannot gather the inventory itself
    # (it never sees the real run directory, only a staged temp copy) -- a
    # caller that never passes one (an existing local-directory caller, or a
    # test) must keep working, with an empty inventory rather than an error.
    root = tmp_path / "run"
    _minimal_run(root)
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert got.outputs == []
    assert got.named_outputs == {}


def test_inventory_entry_carries_the_real_file_size_not_a_decoded_text_length():
    # The dead extra_globs writer this replaces set `bytes=len(text.encode())`
    # -- the length of a DECODED TEXT read, not the file's real st_size. The
    # inventory never reads file content at all: its `bytes` must be exactly
    # whatever the caller (the remote listing) reports, unrelated to any
    # local text decoding.
    got = harvest.harvest_local(
        "/nonexistent-root-never-read",
        inventory=[{"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 4823019283}],
        lookup_by_fastq=lambda p, types=None: [])
    assert len(got.outputs) == 1
    assert got.outputs[0].path == "star_salmon/CONTROL_REP1.markdup.sorted.bam"
    assert got.outputs[0].bytes == 4823019283


def test_inventory_checksum_folds_into_the_manifests_own_dict():
    """granular.py's `_STAGE_SCRIPT` computes "checksum" on an inventory
    entry (an automatic hash under its own per-file/total-byte ceiling --
    see test_run_harvest_op.py); `harvest_local` is the layer that folds
    that into `RunManifest.checksums`, keyed by the same run-relative path
    `mapper._attach_checksum` looks up."""
    got = harvest.harvest_local(
        "/nonexistent-root-never-read",
        inventory=[
            {"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 100,
             "checksum": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            {"path": "star_salmon/salmon.merged.gene_counts.tsv", "bytes": 200,
             "checksum": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
        ],
        lookup_by_fastq=lambda p, types=None: [])
    assert got.checksums == {
        "star_salmon/CONTROL_REP1.markdup.sorted.bam": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "star_salmon/salmon.merged.gene_counts.tsv": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }


def test_an_inventory_entry_with_no_checksum_key_folds_nothing():
    """An inventory entry from an older caller, or one the remote script
    could not checksum at all (over the ceiling or budget), must not
    fabricate an entry."""
    got = harvest.harvest_local(
        "/nonexistent-root-never-read",
        inventory=[{"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 100}],
        lookup_by_fastq=lambda p, types=None: [])
    assert got.checksums == {}


def test_inventory_entries_are_attributed_to_a_sample_when_derivable(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    stats_dir = root / "multiqc" / "star_salmon" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tstar-uniquely_mapped_percent\n"
        "CONTROL_REP1\t89.16\n")

    got = harvest.harvest_local(
        str(root),
        inventory=[
            {"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 100},
            {"path": "star_salmon/salmon.merged.gene_counts.tsv", "bytes": 200},
        ],
        lookup_by_fastq=lambda p, types=None: [])

    by_path = {o.path: o for o in got.outputs}
    assert by_path["star_salmon/CONTROL_REP1.markdup.sorted.bam"].sample == "CONTROL_REP1"
    # A per-run matrix names no single sample -- nothing to derive, and
    # nothing invented.
    assert by_path["star_salmon/salmon.merged.gene_counts.tsv"].sample is None


def test_an_output_named_only_by_its_folder_is_attributed_to_that_sample(tmp_path):
    """Salmon writes every sample's quantification as `quant.sf` inside a
    folder named after the sample. The basename names no sample, so the
    folder is the only carrier -- matched exactly, never as a prefix."""
    root = tmp_path / "run"
    _minimal_run(root)
    stats_dir = root / "multiqc" / "star_salmon" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tstar-uniquely_mapped_percent\n"
        "CONTROL_REP1\t89.16\n")

    got = harvest.harvest_local(
        str(root),
        inventory=[
            {"path": "salmon/CONTROL_REP1/quant.sf", "bytes": 100},
            # A folder that only STARTS with a sample name is not that sample's.
            {"path": "salmon/CONTROL_REP1_extra/quant.sf", "bytes": 100},
            # A run-level folder names no sample.
            {"path": "salmon/salmon/quant.sf", "bytes": 100},
        ],
        lookup_by_fastq=lambda p, types=None: [])

    by_path = {o.path: o.sample for o in got.outputs}
    assert by_path["salmon/CONTROL_REP1/quant.sf"] == "CONTROL_REP1"
    assert by_path["salmon/CONTROL_REP1_extra/quant.sf"] is None
    assert by_path["salmon/salmon/quant.sf"] is None


def test_the_basename_still_wins_over_the_folder():
    """A per-sample file in a per-sample folder keeps its basename attribution:
    the folder fallback only fills a gap, it never overrides."""
    assert harvest._sample_for_output_path(
        "preprocessing/A/A_1.recal.bam", ["A", "A_1"]) == "A_1"


def test_named_outputs_resolves_the_three_well_known_keys_from_a_synthetic_tree(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)

    got = harvest.harvest_local(
        str(root),
        inventory=[
            {"path": "multiqc/star_salmon/multiqc_data/multiqc_report.html", "bytes": 111},
            {"path": "star_salmon/contaminants/kraken2/kraken_reports/CONTROL_REP1.kraken2.report.txt",
             "bytes": 222},
            {"path": "deseq2_qc/deseq2.dds.RData", "bytes": 333},
            # a decoy that must NOT be picked for any named key.
            {"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 444},
        ],
        lookup_by_fastq=lambda p, types=None: [])

    assert got.named_outputs == {
        "multiqc_report_html": "multiqc/star_salmon/multiqc_data/multiqc_report.html",
        "kraken2_report": "star_salmon/contaminants/kraken2/kraken_reports/CONTROL_REP1.kraken2.report.txt",
        "deseq2_dds_rdata": "deseq2_qc/deseq2.dds.RData",
    }


def test_named_outputs_omits_a_key_with_no_matching_inventory_entry(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    got = harvest.harvest_local(
        str(root),
        inventory=[{"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 1}],
        lookup_by_fastq=lambda p, types=None: [])
    assert got.named_outputs == {}


def _multirun_samplesheet(root, sample="S1"):
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,strandedness\n"
        f"{sample},/net/cluster/fastq/{sample}_L001_R1.fastq.gz,,auto\n"
        f"{sample},/net/cluster/fastq/{sample}_L002_R1.fastq.gz,,auto\n")
    stats_dir = root / "multiqc" / "star_salmon" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tstar-uniquely_mapped_percent\n"
        f"{sample}\t90.0\n")


def test_a_multirun_sample_collapses_to_exactly_one_sample_record(tmp_path):
    # Two samplesheet ROWS share the sample name "S1" (a topped-up library,
    # the ordinary case a multi-run sample comes from) -- harvest_local must
    # not build one SampleRecord per row, or the mapper would emit two
    # identical A.ALN children for what is really one analysis output.
    root = tmp_path / "run"
    _minimal_run(root)
    _multirun_samplesheet(root)
    mapping = {"/net/cluster/fastq/S1_L001_R1.fastq.gz": ["D.SEQ-LANE-1"],
               "/net/cluster/fastq/S1_L002_R1.fastq.gz": ["D.SEQ-LANE-2"]}

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: mapping.get(p, []))

    matches = [s for s in got.samples if s.nfcore_sample == "S1"]
    assert len(matches) == 1
    sample = matches[0]
    assert sample.uid_resolution == manifest.RESOLUTION_MULTIRUN
    # The single-parent field stays None -- there is no one parent -- but
    # both of this sample's own contributing rows resolved, in samplesheet
    # order.
    assert sample.d_seq_uid is None
    assert sample.d_seq_uid_multirun == ["D.SEQ-LANE-1", "D.SEQ-LANE-2"]
    assert not any("S1" in w and "partial" in w for w in got.warnings)


def test_a_multirun_samples_partial_resolution_is_recorded_as_a_warning(tmp_path):
    # Only one of the two contributing rows has a fastq match -- a real,
    # partial parent list. harvest.py is the layer that knows how many rows
    # a sample SHOULD have had (uid_resolve only sees the resolved subset),
    # so it is the one that must notice and say so, rather than the partial
    # list passing through silently.
    root = tmp_path / "run"
    _minimal_run(root)
    _multirun_samplesheet(root)
    mapping = {"/net/cluster/fastq/S1_L001_R1.fastq.gz": ["D.SEQ-LANE-1"]}

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: mapping.get(p, []))

    sample = next(s for s in got.samples if s.nfcore_sample == "S1")
    assert sample.d_seq_uid_multirun == ["D.SEQ-LANE-1"]
    assert any("S1" in w and "1 of 2" in w for w in got.warnings)


def test_a_multirun_sample_with_nothing_resolved_gets_no_partial_warning(tmp_path):
    # Zero of two rows resolving is the ordinary "nothing known" case, not a
    # partial result -- it must not be reported as one.
    root = tmp_path / "run"
    _minimal_run(root)
    _multirun_samplesheet(root)

    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    sample = next(s for s in got.samples if s.nfcore_sample == "S1")
    assert sample.d_seq_uid_multirun == []
    assert not any("S1" in w and "partial" in w for w in got.warnings)


def test_a_multirun_samples_two_rows_resolving_to_the_same_parent_is_not_partial(tmp_path):
    # Realistic whenever one D.SEQ record's File_PrimaryData lists both
    # lanes: BOTH contributing rows resolve, but to the SAME uid, so
    # len(parents) de-duplicates to 1 against row_counts == 2. That must not
    # look partial -- every contributing row did in fact resolve, and Parent
    # is a complete list. This is the case the len(parents)-based check got
    # wrong; comparing resolved ROWS (2) against row_counts (2) gets it right.
    root = tmp_path / "run"
    _minimal_run(root)
    _multirun_samplesheet(root)

    got = harvest.harvest_local(
        str(root), lookup_by_fastq=lambda p, types=None: ["D.SEQ-SAME-RECORD"])

    sample = next(s for s in got.samples if s.nfcore_sample == "S1")
    assert sample.d_seq_uid_multirun == ["D.SEQ-SAME-RECORD"]
    assert not any("S1" in w and "partial" in w for w in got.warnings)


# ---------------------------------------------------------------------------
# accepts_parent_types wiring: harvest_local resolves the run's own pipeline
# map and threads its accepts_parent_types into the lookup it hands to
# uid_resolve -- see maps.PipelineMap.accepts_parent_types and harvest.py's
# comment at the uid_resolve.resolve() call site.
# ---------------------------------------------------------------------------

def _hlatyping_run(root, sample="PATIENT1", primary_path=None):
    """A minimal staged run for a pipeline whose committed map declares a
    wider accepts_parent_types (["D.SEQ", "A.ALN"]) than the default -- see
    NessieAI/ns/reingest_maps/hlatyping.outputs.json. `fastq_1` carries
    whatever path the test wants the lookup to be asked about; hlatyping's
    real samplesheet would put an already-aligned parent's path in its `bam`
    column instead (uid_resolve only ever reads `fastq_1` -- see this
    change's report for that gap), but this test is only about which TYPES
    the lookup is asked to search, not which samplesheet column feeds it.
    """
    primary_path = primary_path or f"/net/cluster/runs/aln/{sample}.markdup.sorted.bam"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_hlatyping_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/hlatyping: 2.1.0\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    (root / "samplesheet.csv").write_text(
        f"sample,fastq_1\n{sample},{primary_path}\n")
    return primary_path


def test_a_wider_map_threads_its_extra_type_into_the_lookup(tmp_path):
    # hlatyping's committed map declares accepts_parent_types = ["D.SEQ",
    # "A.ALN"] (its pinned schema_input.json accepts a `bam` column
    # alongside fastq_1/fastq_2 -- see the report for the source
    # verification). harvest_local must resolve THAT map from the run's own
    # parsed pipeline name and hand BOTH types to the lookup, not just
    # "D.SEQ" -- a stub that never implemented this threading would only
    # ever see ("D.SEQ",) and this test would fail against it.
    root = tmp_path / "run"
    primary_path = _hlatyping_run(root)
    calls = []

    def lookup(path, types):
        calls.append(tuple(types))
        return ["A.ALN-PARENT-1"] if "A.ALN" in types and path == primary_path else []

    got = harvest.harvest_local(str(root), lookup_by_fastq=lookup)

    sample = next(s for s in got.samples if s.nfcore_sample == "PATIENT1")
    assert calls and set(calls[0]) == {"D.SEQ", "A.ALN"}
    assert sample.d_seq_uid == "A.ALN-PARENT-1"
    assert sample.uid_resolution == manifest.RESOLUTION_FASTQ_EXACT


def test_a_d_seq_only_map_threads_exactly_the_historical_scope(tmp_path):
    # rnaseq's committed map declares no wider accepts_parent_types, so the
    # lookup this test's stub records must be handed EXACTLY ("D.SEQ",) --
    # the same scope used before this field existed -- never silently
    # widened to a type this map never declared.
    root = tmp_path / "run"
    _minimal_run(root)
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,strandedness\n"
        "CONTROL_REP1,/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz,,auto\n")
    calls = []

    def lookup(path, types):
        calls.append(tuple(types))
        return ["D.SEQ-UID-1"] if path.endswith("CONTROL_REP1_R1.fastq.gz") else []

    got = harvest.harvest_local(str(root), lookup_by_fastq=lookup)

    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert calls == [("D.SEQ",)]
    assert sample.d_seq_uid == "D.SEQ-UID-1"
    assert sample.uid_resolution == manifest.RESOLUTION_FASTQ_EXACT


def test_an_unrecognised_pipeline_falls_back_to_the_d_seq_only_default(tmp_path):
    # A pipeline with no committed map at all (or one harvest_local cannot
    # yet identify) must not raise out of a read-only harvest step, and must
    # not search wider than the historical default either.
    root = tmp_path / "run"
    root.mkdir(parents=True)
    (root / "pipeline_info").mkdir()
    (root / "pipeline_info" / "nf_core_notreal_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/notreal: 1.0.0\n  Nextflow: 25.10.2\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1\nS1,/net/cluster/fastq/S1_R1.fastq.gz\n")
    calls = []

    def lookup(path, types):
        calls.append(tuple(types))
        return []

    harvest.harvest_local(str(root), lookup_by_fastq=lookup)

    # _by_path tries an exact match and then a basename fallback, so an
    # unresolved lookup is called twice -- both calls must carry the same
    # ("D.SEQ",) scope.
    assert calls == [("D.SEQ",), ("D.SEQ",)]


# ---------------------------------------------------------------------------
# MultiQC per-module tables: OptiType's alleles are in multiqc_optitype.txt,
# never in general stats.
# ---------------------------------------------------------------------------

# A real nf-core/hlatyping 2.2.0 table. MultiQC keeps the "_result" of
# OptiType's file name in the Sample column.
_OPTITYPE_TABLE = (
    "Sample\tA1\tA2\tB1\tB2\tC1\tC2\tReads\tObjective\n"
    "D.SEQ-250409KAM-1-PUB_result\tA*02:01\tA*32:01\tB*40:01\tB*49:01\tC*03:04\tC*07:01"
    "\t413.0\t401.84900000000096\n"
    "D.SEQ-250409KAM-9-PUB_result\tA*01:01\tA*24:02\tB*08:01\tB*38:01\tC*07:01\tC*12:03"
    "\t156.0\t151.78799999999995\n")
_HLA_SAMPLES = ("D.SEQ-250409KAM-1-PUB", "D.SEQ-250409KAM-9-PUB")


def _hlatyping_run_with_optitype(root, table=_OPTITYPE_TABLE, general_stats=None):
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "nf_core_hlatyping_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/hlatyping: v2.2.0-g3237d20\n  Nextflow: 26.04.6\n")
    (root / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1\n" + "".join(f"{s},/net/fq/{s}_1.fastq.gz\n" for s in _HLA_SAMPLES))
    data = root / "multiqc" / "multiqc_data"
    data.mkdir(parents=True)
    (data / "multiqc_general_stats.txt").write_text(
        general_stats or "Sample\tfastqc-percent_gc\n"
        + "".join(f"{s}_1\t48.0\n" for s in _HLA_SAMPLES))
    if table is not None:
        (data / "multiqc_optitype.txt").write_text(table)


def test_optitype_alleles_come_from_the_multiqc_module_table(tmp_path):
    root = tmp_path / "run"
    _hlatyping_run_with_optitype(root)
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    metrics = {s.nfcore_sample: s.metrics for s in got.samples}
    assert metrics["D.SEQ-250409KAM-1-PUB"]["optitype-A1"] == "A*02:01"
    assert metrics["D.SEQ-250409KAM-1-PUB"]["optitype-C2"] == "C*07:01"
    assert metrics["D.SEQ-250409KAM-9-PUB"]["optitype-B2"] == "B*38:01"
    assert metrics["D.SEQ-250409KAM-9-PUB"]["optitype-Reads"] == 156.0
    assert got.sources["metrics_optitype"] == "multiqc/multiqc_data/multiqc_optitype.txt"


def test_a_run_without_the_module_table_gets_no_optitype_metrics(tmp_path):
    root = tmp_path / "run"
    _hlatyping_run_with_optitype(root, table=None)
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert not any(k.startswith("optitype-") for s in got.samples for k in s.metrics)
    assert "metrics_optitype" not in got.sources


def test_general_stats_wins_over_a_module_table_on_the_same_key(tmp_path):
    root = tmp_path / "run"
    _hlatyping_run_with_optitype(
        root, general_stats="Sample\toptitype-Reads\nD.SEQ-250409KAM-1-PUB\t1.0\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    sample = next(s for s in got.samples if s.nfcore_sample == "D.SEQ-250409KAM-1-PUB")
    assert sample.metrics["optitype-Reads"] == 1.0


def test_an_hlatyping_run_maps_to_a_complete_a_hla_row(tmp_path):
    # Harvest and map end to end: the row has its data file and its alleles.
    root = tmp_path / "run"
    _hlatyping_run_with_optitype(root)
    inventory = [{"path": f"optitype/{s}/{s}_{f}", "bytes": 100}
                 for s in _HLA_SAMPLES for f in ("result.tsv", "coverage_plot.pdf")]
    got = harvest.harvest_local(str(root), inventory=inventory,
                                lookup_by_fastq=lambda p, types=None: [])
    result = mapper.apply(got, maps.load("hlatyping"))
    rows = {r.nfcore_sample: r.attributes for r in result.rows if r.rule_id == "hla_class_i"}
    assert set(rows) == set(_HLA_SAMPLES)
    row = rows["D.SEQ-250409KAM-1-PUB"]
    assert row["File_PrimaryData"].value == "D.SEQ-250409KAM-1-PUB_result.tsv"
    assert row["File_SecondaryData"].value == "D.SEQ-250409KAM-1-PUB_coverage_plot.pdf"
    assert [row[f"HLA_{g}"].value for g in ("A1", "A2", "B1", "B2", "C1", "C2")] == [
        "A*02:01", "A*32:01", "B*40:01", "B*49:01", "C*03:04", "C*07:01"]
    assert row["HLAReads"].value == 413.0
    # Every OptiType column is claimed by the map, so none reaches the review queue.
    assert not [u for u in result.unmapped if u["raw_key"].startswith("optitype-")]


# ---------------------------------------------------------------------------
# sample_type_lookup wiring: harvest_local fills SampleRecord.parent_sample_type
# from the batched sample-type lookup, one call for the whole run -- never
# once per sample, and never by parsing the UID's own prefix (see
# manifest.SampleRecord.parent_sample_type and
# nextseek_api.services.reingest_lookups.sample_types_for_uids).
# ---------------------------------------------------------------------------

def _single_sample_run(root, sample="CONTROL_REP1", fastq_name="CONTROL_REP1_R1.fastq.gz"):
    _minimal_run(root)
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,strandedness\n"
        f"{sample},/net/cluster/fastq/{fastq_name},,auto\n")


def test_the_parent_sample_type_is_filled_from_the_batched_lookup(tmp_path):
    root = tmp_path / "run"
    _single_sample_run(root)
    calls = []

    def sample_type_lookup(uids):
        calls.append(tuple(uids))
        return {"D.SEQ-UID-1": "D.SEQ"}

    got = harvest.harvest_local(
        str(root),
        lookup_by_fastq=lambda p, types=None: (
            ["D.SEQ-UID-1"] if p.endswith("CONTROL_REP1_R1.fastq.gz") else []),
        sample_type_lookup=sample_type_lookup)

    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert sample.d_seq_uid == "D.SEQ-UID-1"
    assert sample.parent_sample_type == "D.SEQ"
    assert calls == [("D.SEQ-UID-1",)]


def test_no_sample_type_lookup_leaves_the_parent_type_empty_rather_than_erroring(tmp_path):
    # A harvest with no way to reach the lookup (the default, `None`) must
    # still work, exactly the same degrade-and-carry-on `lookup_by_fastq`
    # already gets -- never a guess, never a crash.
    root = tmp_path / "run"
    _single_sample_run(root)

    got = harvest.harvest_local(
        str(root),
        lookup_by_fastq=lambda p, types=None: (
            ["D.SEQ-UID-1"] if p.endswith("CONTROL_REP1_R1.fastq.gz") else []))

    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert sample.d_seq_uid == "D.SEQ-UID-1"
    assert sample.parent_sample_type == ""


def test_a_uid_the_lookup_cannot_resolve_leaves_the_parent_type_empty_not_guessed(tmp_path):
    # The lookup ran, but this UID is absent from its result (see
    # sample_types_for_uids' own omission contract) -- must not fall back to
    # any default guess such as "D.SEQ".
    root = tmp_path / "run"
    _single_sample_run(root)

    got = harvest.harvest_local(
        str(root),
        lookup_by_fastq=lambda p, types=None: (
            ["D.SEQ-UID-1"] if p.endswith("CONTROL_REP1_R1.fastq.gz") else []),
        sample_type_lookup=lambda uids: {})

    sample = next(s for s in got.samples if s.nfcore_sample == "CONTROL_REP1")
    assert sample.d_seq_uid == "D.SEQ-UID-1"
    assert sample.parent_sample_type == ""


def test_the_lookup_is_called_once_for_the_whole_run_not_once_per_sample(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,strandedness\n"
        "S1,/net/cluster/fastq/S1_R1.fastq.gz,,auto\n"
        "S2,/net/cluster/fastq/S2_R1.fastq.gz,,auto\n")
    mapping = {"/net/cluster/fastq/S1_R1.fastq.gz": ["D.SEQ-1"],
               "/net/cluster/fastq/S2_R1.fastq.gz": ["A.ALN-1"]}
    calls = []

    def sample_type_lookup(uids):
        calls.append(sorted(uids))
        return {"D.SEQ-1": "D.SEQ", "A.ALN-1": "A.ALN"}

    got = harvest.harvest_local(
        str(root), lookup_by_fastq=lambda p, types=None: mapping.get(p, []),
        sample_type_lookup=sample_type_lookup)

    # One call for both resolved parents, not two separate calls.
    assert calls == [["A.ALN-1", "D.SEQ-1"]]
    by_name = {s.nfcore_sample: s for s in got.samples}
    assert by_name["S1"].parent_sample_type == "D.SEQ"
    assert by_name["S2"].parent_sample_type == "A.ALN"


# ── which MultiQC tree the QC numbers come from ────────────────────────────
# A run setting both --aligner and --pseudo_aligner publishes one MultiQC tree
# per quantification route, and they disagree on every number. Selection must
# follow the run's declared aligner, not sort order: "multiqc/salmon" sorts
# before "multiqc/star_salmon", so the old sorted-first pick silently returned
# pseudo-alignment numbers for an alignment-based run.

_SALMON = "multiqc/salmon/multiqc_report_data/multiqc_general_stats.txt"
_STAR_SALMON = "multiqc/star_salmon/multiqc_report_data/multiqc_general_stats.txt"
_BOTH = [_SALMON, _STAR_SALMON]


def test_declared_aligner_beats_sort_order():
    """The regression: star_salmon must win although salmon sorts first."""
    warnings: list[str] = []
    assert harvest._pick_metrics_source(
        _BOTH, {"aligner": "star_salmon"}, warnings) == _STAR_SALMON
    assert len(warnings) == 1
    assert "star_salmon" in warnings[0]


def test_aligner_matches_a_whole_path_component_not_a_substring():
    """'salmon' is a substring of 'star_salmon'; matching must not be fooled.

    A substring test would return the star_salmon tree for an --aligner salmon
    run, since that path contains "salmon" too. Only a whole-component match
    distinguishes them.
    """
    warnings: list[str] = []
    assert harvest._pick_metrics_source(
        _BOTH, {"aligner": "salmon"}, warnings) == _SALMON


def test_a_single_candidate_needs_no_warning():
    warnings: list[str] = []
    assert harvest._pick_metrics_source(
        [_STAR_SALMON], {"aligner": "star_salmon"}, warnings) == _STAR_SALMON
    assert warnings == []


def test_no_candidate_resolves_to_none():
    warnings: list[str] = []
    assert harvest._pick_metrics_source([], {"aligner": "star_salmon"}, warnings) is None


@pytest.mark.parametrize("params, because", [
    ({}, "params.aligner is unset"),
    ({"aligner": ""}, "params.aligner is unset"),
    ({"aligner": "hisat2"}, "no multiqc general-stats file sits under it"),
])
def test_an_unresolvable_choice_falls_back_but_says_so(params, because):
    """Falling back to sort order is allowed; doing it silently is not."""
    warnings: list[str] = []
    got = harvest._pick_metrics_source(_BOTH, params, warnings)
    assert got == _SALMON, "fallback is still sorted-first"
    assert len(warnings) == 1
    assert because in warnings[0]
    assert "wrong quantification route" in warnings[0]


# ── $outputs scoping: per-run vs per-sample ────────────────────────────────
# named_outputs was a single flat dict, so a per-SAMPLE well-known output
# (<sample>.kraken2.report.txt) resolved to the alphabetically first sample's
# file for every row: on a 24-sample run, 23 rows linked to someone else's
# contamination screen.

def _out(path, sample=None):
    return manifest.OutputRecord(path=path, bytes=1, sample=sample)


def test_a_per_sample_named_output_is_indexed_per_sample():
    run_level, per_sample = harvest._resolve_named_outputs([
        _out("star_salmon/contaminants/kraken2/kraken_reports/A.kraken2.report.txt", "A"),
        _out("star_salmon/contaminants/kraken2/kraken_reports/B.kraken2.report.txt", "B"),
    ])
    assert per_sample["A"]["kraken2_report"].endswith("A.kraken2.report.txt")
    assert per_sample["B"]["kraken2_report"].endswith("B.kraken2.report.txt")


def test_a_fully_attributed_key_is_absent_from_the_run_level_dict():
    """No run-level answer exists, so a blank beats an arbitrary sample's file."""
    run_level, _ = harvest._resolve_named_outputs([
        _out("x/A.kraken2.report.txt", "A"),
        _out("x/B.kraken2.report.txt", "B"),
    ])
    assert "kraken2_report" not in run_level


def test_a_genuinely_run_level_output_stays_run_level():
    run_level, per_sample = harvest._resolve_named_outputs([
        _out("multiqc/multiqc_report.html"),
    ])
    assert run_level["multiqc_report_html"] == "multiqc/multiqc_report.html"
    assert per_sample == {}


def test_each_sample_resolves_outputs_to_its_own_copy():
    """The regression: B's row must not carry A's report."""
    run = manifest.RunManifest(
        run_dir="/r",
        samples=[manifest.SampleRecord(nfcore_sample=n) for n in ("A", "B")],
        named_outputs_by_sample={
            "A": {"kraken2_report": "x/A.kraken2.report.txt"},
            "B": {"kraken2_report": "x/B.kraken2.report.txt"},
        })
    a, b = run.samples
    assert maps.resolve_ref("$outputs.kraken2_report", run, a) == "x/A.kraken2.report.txt"
    assert maps.resolve_ref("$outputs.kraken2_report", run, b) == "x/B.kraken2.report.txt"


def test_a_sample_without_its_own_copy_falls_back_to_run_level():
    run = manifest.RunManifest(
        run_dir="/r",
        samples=[manifest.SampleRecord(nfcore_sample="A")],
        named_outputs={"multiqc_report_html": "multiqc/multiqc_report.html"})
    assert maps.resolve_ref("$outputs.multiqc_report_html", run,
                            run.samples[0]) == "multiqc/multiqc_report.html"


def test_a_per_run_rule_gets_nothing_rather_than_one_samples_file():
    run = manifest.RunManifest(
        run_dir="/r",
        named_outputs_by_sample={"A": {"kraken2_report": "x/A.kraken2.report.txt"}})
    assert maps.resolve_ref("$outputs.kraken2_report", run) is None


# ── harvest_globs is now read ──────────────────────────────────────────────
# Each map declares its own per-pipeline QC files, and nothing consumed the
# field: STAR logs, samtools stats, Picard metrics and tx2gene were named in the
# map and never fetched.

def test_the_staged_list_is_the_generic_core_plus_the_maps_text_globs():
    staged = harvest.staged_globs()
    for pattern in harvest.GENERIC_GLOBS:
        assert pattern in staged, "the generic core must survive"
    inventory_names = {p.rsplit("/", 1)[-1] for p in harvest.inventory_globs()}
    for braced in maps.all_harvest_globs():
        for pattern in mapper._expand_braces(braced):
            if pattern.rsplit("/", 1)[-1] not in inventory_names:
                assert pattern in staged, "a map's text pattern must be staged"


def test_no_staged_pattern_still_has_braces():
    """The stage script matches with pathlib's glob, which has no {a,b}
    syntax: a braced pattern reaches it literally and matches nothing, with
    no error. On a real rnaseq run the braced samtools pattern matched 0
    files and its plain form 15. A new braced map pattern fails here, not
    silently on the cluster."""
    for pattern in harvest.staged_globs():
        assert "{" not in pattern and "}" not in pattern, pattern


def test_braced_map_patterns_are_staged_as_every_plain_variant():
    """Two groups in one pattern cross-multiply: 3 aligners x 3 extensions."""
    staged = harvest.staged_globs()
    for aligner in ("star_salmon", "star_rsem", "hisat2"):
        for ext in ("flagstat", "idxstats", "stats"):
            assert f"{aligner}/samtools_stats/*.{ext}" in staged
    assert "star_salmon/picard_metrics/*.MarkDuplicates.metrics.txt" in staged
    assert "kallisto/*.merged.tx2gene.tsv" in staged


def test_braced_patterns_match_real_paths_once_expanded(tmp_path):
    """End to end against pathlib, the matcher the stage script uses."""
    stats = tmp_path / "hisat2" / "samtools_stats"
    stats.mkdir(parents=True)
    (stats / "S1.sorted.bam.flagstat").write_text("")
    (stats / "S1.sorted.bam.idxstats").write_text("")
    matched = {p.relative_to(tmp_path).as_posix()
               for pattern in harvest.staged_globs()
               for p in tmp_path.glob(pattern)}
    assert {"hisat2/samtools_stats/S1.sorted.bam.flagstat",
            "hisat2/samtools_stats/S1.sorted.bam.idxstats"} <= matched


def test_an_inventory_name_is_dropped_after_expansion_too():
    """The inventory check runs on expanded patterns, so a braced filename
    that expands to an inventory-owned one is still never staged."""
    staged = harvest.staged_globs()
    assert not any(p.endswith(".h5ad") for p in staged)


def test_a_map_pattern_inventory_already_owns_is_not_staged():
    """Staging copies; inventory lists in place. A pattern in both would try to
    pull single-cell matrices off the cluster -- the scrnaseq map declares one."""
    staged = harvest.staged_globs()
    assert not any(p.endswith("*.h5ad") for p in staged)
    assert not any(p.endswith("*.kraken2.report.txt") for p in staged)
    # ...while the map does still declare them, so this is a live guard
    assert any(p.endswith("*.h5ad") for p in maps.all_harvest_globs())


def test_the_star_log_is_now_staged():
    """The concrete gap: UnalignedReads needs it and it was never fetched."""
    assert any(".Log.final.out" in p for p in harvest.staged_globs())


def test_the_staged_list_has_no_duplicates():
    """Overlapping patterns are fine remotely (it dedupes by path), but sending
    the same pattern twice is just waste."""
    staged = harvest.staged_globs()
    assert len(staged) == len(set(staged))


def test_the_union_spans_pipelines_not_just_one_map():
    """Staging runs before the pipeline is known, so one map's list will not do."""
    union = set(maps.all_harvest_globs())
    rnaseq = set(maps.load("rnaseq").harvest_globs)
    assert rnaseq <= union
    assert union >= rnaseq


def test_inventory_globs_are_not_folded_into_the_staged_list():
    """Inventory files are LISTED, never copied; conflating them would pull
    every BAM off the cluster."""
    staged = harvest.staged_globs()
    assert "**/*.bam" not in staged
    assert not any(p.endswith("*.bam") for p in staged)


def _restarted_run(root):
    """A run folder holding two attempts, as `nextflow run -resume` leaves it:
    one params file and one execution trace per attempt, timestamped, and one
    versions file the last attempt overwrote."""
    info = root / "pipeline_info"
    info.mkdir(parents=True)
    (info / "nf_core_scrnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/scrnaseq: v4.1.0\n  Nextflow: 25.04.6\n")
    (info / "params_2025-11-13_11-52-01.json").write_text('{"attempt": "first"}')
    (info / "params_2025-11-16_05-29-51.json").write_text('{"attempt": "last"}')
    (info / "execution_trace_2025-11-13_11-51-54.txt").write_text(
        "task_id\tstatus\n1\tFAILED\n2\tABORTED\n")
    (info / "execution_trace_2025-11-16_05-29-45.txt").write_text(
        "task_id\tstatus\n1\tCACHED\n2\tCOMPLETED\n")


def test_a_restarted_run_is_read_from_its_newest_attempt(tmp_path):
    # The timestamps in nf-core's file names sort chronologically, so the
    # newest attempt is the last match. Reading the first one reported the
    # first attempt's parameters and its failures for a run that finished.
    root = tmp_path / "run"
    _restarted_run(root)
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    assert got.sources["params"] == "pipeline_info/params_2025-11-16_05-29-51.json"
    assert got.params == {"attempt": "last"}
    assert got.sources["execution"] == "pipeline_info/execution_trace_2025-11-16_05-29-45.txt"
    assert got.execution.failed == 0
    assert got.run_status == manifest.RUN_COMPLETE


def test_a_restarted_run_says_which_attempts_it_ignored(tmp_path):
    root = tmp_path / "run"
    _restarted_run(root)
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    attempts = [w for w in got.warnings if "attempt" in w]
    assert attempts == [
        "2 params files, one per attempt of a restarted run: using the newest, "
        "pipeline_info/params_2025-11-16_05-29-51.json; ignored "
        "pipeline_info/params_2025-11-13_11-52-01.json",
        "2 execution traces, one per attempt of a restarted run: using the newest, "
        "pipeline_info/execution_trace_2025-11-16_05-29-45.txt; ignored "
        "pipeline_info/execution_trace_2025-11-13_11-51-54.txt",
    ]


def test_a_single_attempt_raises_no_attempt_warning(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    (root / "pipeline_info" / "params_2026-01-26_13-53-31.json").write_text("{}")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert not [w for w in got.warnings if "attempt" in w]


def test_differentialabundance_collated_versions_are_read(tmp_path):
    # differentialabundance 1.5.0 writes its versions with nf-core's own
    # softwareVersionsToYAML, the same format as every other pipeline, under
    # pipeline_info/collated_versions.yml. Missing it left the pipeline
    # unnamed and the run "incomplete", so no map applied.
    root = tmp_path / "run"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "collated_versions.yml").write_text(
        "DESEQ2_DIFFERENTIAL:\n  r-base: 4.3.1\n"
        "Workflow:\n  nf-core/differentialabundance: v1.5.0\n  Nextflow: 24.04.4\n")
    (root / "pipeline_info" / "execution_trace_2025-09-16_11-59-28.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])

    assert got.sources["software_versions"] == "pipeline_info/collated_versions.yml"
    assert got.pipeline.name == "nf-core/differentialabundance"
    assert got.pipeline.version == "v1.5.0"
    assert got.run_status == manifest.RUN_COMPLETE


def test_the_standard_versions_file_wins_over_collated_versions(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    (root / "pipeline_info" / "collated_versions.yml").write_text(
        "Workflow:\n  nf-core/other: v0.0.1\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert got.sources["software_versions"].endswith("nf_core_rnaseq_software_mqc_versions.yml")


@pytest.mark.parametrize("report", [
    "multiqc/M188_multiqc_report.html",                       # scrnaseq: <run>_multiqc_report.html
    "multiqc/star_salmon/multiqc_report.html",                # rnaseq
    "reports/multiqc/250818Rib_multiqc_report.html",          # rnavar layout, prefixed
])
def test_a_prefixed_multiqc_report_is_inventoried_and_named(tmp_path, report):
    # nf-core/scrnaseq names its report after the run (M188_multiqc_report.html);
    # both the inventory glob and the named-output matcher required the bare name.
    root = tmp_path / "run"
    _minimal_run(root)
    (root / report).parent.mkdir(parents=True, exist_ok=True)
    (root / report).write_text("<html></html>")

    listed = {str(p.relative_to(root)) for pattern in harvest.inventory_globs()
              for p in root.glob(pattern)}
    assert report in listed

    got = harvest.harvest_local(
        str(root), inventory=[{"path": report, "bytes": 13}],
        lookup_by_fastq=lambda p, types=None: [])
    assert got.named_outputs["multiqc_report_html"] == report


def test_the_differentialabundance_1x_report_is_inventoried_and_named(tmp_path):
    # 1.x writes its report as report/<study>.html, named after the study, so
    # only its place (directly inside the top-level report/) identifies it.
    # 1.5 also writes GSEA pages under report/gsea/<contrast>/<set>/, which
    # must not be listed (there are hundreds) or taken for the report.
    root = tmp_path / "run"
    _minimal_run(root)
    for rel in ("report/250818Rib_diffabun.html",
                "report/gsea/LPS_vs_Ctrl/LPS_vs_Ctrl.go/index.html"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("<html></html>")

    listed = {str(p.relative_to(root)) for pattern in harvest.inventory_globs()
              for p in root.glob(pattern)}
    assert "report/250818Rib_diffabun.html" in listed
    assert not any(p.startswith("report/gsea/") for p in listed)

    got = harvest.harvest_local(
        str(root),
        inventory=[{"path": "report/gsea/LPS_vs_Ctrl/LPS_vs_Ctrl.go/index.html", "bytes": 13},
                   {"path": "report/250818Rib_diffabun.html", "bytes": 13}],
        lookup_by_fastq=lambda p, types=None: [])
    assert got.named_outputs["differentialabundance_report_html"] == "report/250818Rib_diffabun.html"


def test_an_html_file_elsewhere_is_not_taken_for_the_differentialabundance_report(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    got = harvest.harvest_local(
        str(root), inventory=[{"path": "multiqc/star_salmon/report/x.html", "bytes": 1},
                              {"path": "pipeline_info/execution_report.html", "bytes": 1},
                              {"path": "report/gsea/rnaseq/c1/set/index.html", "bytes": 1},
                              {"path": "report/rnaseq_dream/other.html", "bytes": 1}],
        lookup_by_fastq=lambda p, types=None: [])
    assert "differentialabundance_report_html" not in got.named_outputs


def test_the_differentialabundance_2_report_is_named_inside_its_paramset_folder(tmp_path):
    # 2.0.0 writes report/<paramset>/<study>_differentialabundance_report.html,
    # one per paramset; the first, by path, is the run's QC link.
    root = tmp_path / "run"
    _minimal_run(root)
    got = harvest.harvest_local(
        str(root),
        inventory=[{"path": "report/rnaseq_dream/SRP254919_differentialabundance_report.html", "bytes": 1},
                   {"path": "report/generic_matrix_dream/SRP254919_differentialabundance_report.html", "bytes": 1}],
        lookup_by_fastq=lambda p, types=None: [])
    assert (got.named_outputs["differentialabundance_report_html"]
            == "report/generic_matrix_dream/SRP254919_differentialabundance_report.html")


def test_a_multiqc_side_file_is_not_taken_for_the_report(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    got = harvest.harvest_local(
        str(root), inventory=[{"path": "multiqc/multiqc_report.html.bak", "bytes": 1}],
        lookup_by_fastq=lambda p, types=None: [])
    assert "multiqc_report_html" not in got.named_outputs


def test_a_report_named_after_a_sample_stays_run_level(tmp_path):
    # In a one-sample scrnaseq run the run and the sample share a name (M188),
    # so "M188_multiqc_report.html" starts with "M188_". A MultiQC report
    # covers the whole run: attributing it to the sample would make the key
    # per-sample, and every per_run QC link would resolve to nothing.
    root = tmp_path / "run"
    _minimal_run(root)
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2\nM188,/net/cluster/fastq/M188_R1.fastq.gz,\n")
    got = harvest.harvest_local(
        str(root), inventory=[{"path": "multiqc/M188_multiqc_report.html", "bytes": 13}],
        lookup_by_fastq=lambda p, types=None: [])

    assert got.named_outputs["multiqc_report_html"] == "multiqc/M188_multiqc_report.html"
    assert [o.sample for o in got.outputs] == [None]


_TS = "2025-11-16_05-29-45"
_RETRIED = ("task_id\thash\tname\tstatus\texit\n"
            "103\tdc/5c8a98\tANNDATAR_CONVERT (combined)\tCOMPLETED\t0\n"
            "104\td1/620a12\tANNDATAR_CONVERT (combined)\tFAILED\t143\n"
            "105\t46/134a87\tANNDATAR_CONVERT (combined)\tFAILED\t143\n"
            "106\tfa/87bf8e\tANNDATAR_CONVERT (combined)\tCOMPLETED\t0\n")


def _run_with_verdict(root, trace_text, verdict, *, report_ts=_TS):
    """A staged run as the stage script leaves it: the newest trace, and the
    verdict line it read out of the newest execution report (None: report
    present but no verdict in it; omitted entirely: no report)."""
    info = root / "pipeline_info"
    info.mkdir(parents=True)
    (info / "nf_core_scrnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/scrnaseq: v4.1.0\n")
    (info / f"execution_trace_{_TS}.txt").write_text(trace_text)
    if verdict is not False:
        (root / harvest.RUN_VERDICT_NAME).write_text(json.dumps(
            {"path": f"pipeline_info/execution_report_{report_ts}.html", "verdict": verdict}))


def _harvest(root):
    return harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])


def test_a_clean_nextflow_verdict_clears_retried_failures(tmp_path):
    _run_with_verdict(tmp_path / "run", _RETRIED, "Workflow execution completed successfully!")
    got = _harvest(tmp_path / "run")
    assert (got.execution.failed, got.execution.recovered) == (0, 2)
    assert got.execution.nextflow_verdict == "Workflow execution completed successfully!"
    retried = [w for w in got.warnings if "retried" in w]
    assert retried == [
        "2 failed task attempt(s) were retried and then succeeded, so they are not counted "
        "as failures (ANNDATAR_CONVERT (combined)); Nextflow's report says: "
        "Workflow execution completed successfully!"]


def test_a_clean_verdict_outranks_a_trace_that_looks_failed(tmp_path):
    # Two inputs can share one task name, so the trace alone cannot prove a
    # retry. Nextflow's own verdict can: success with nothing ignored means
    # every failure was retried until it passed.
    trace = ("task_id\tname\tstatus\n1\tCONVERT (a)\tCOMPLETED\n2\tCONVERT (b)\tFAILED\n")
    _run_with_verdict(tmp_path / "run", trace, "Workflow execution completed successfully!")
    got = _harvest(tmp_path / "run")
    assert (got.execution.failed, got.execution.recovered) == (0, 1)


def test_an_ignored_error_verdict_keeps_the_run_failed(tmp_path):
    # "One or more tasks reported an error condition" means a task failed and
    # the pipeline carried on without it: its outputs may be missing, so the
    # op must still refuse without --allow-failed-run, even when the trace
    # rule alone would call every failure recovered.
    _run_with_verdict(
        tmp_path / "run", _RETRIED,
        "Workflow execution completed successfully. One or more tasks reported an error condition.")
    got = _harvest(tmp_path / "run")
    assert got.execution.failed >= 1
    assert any("failed and was ignored" in w for w in got.warnings)


def test_an_unsuccessful_verdict_keeps_the_run_failed(tmp_path):
    _run_with_verdict(tmp_path / "run", _RETRIED, "Workflow execution completed unsuccessfully!")
    got = _harvest(tmp_path / "run")
    assert got.execution.failed >= 1
    assert any("says the run failed" in w for w in got.warnings)


def test_without_a_report_the_trace_rule_decides_and_says_so(tmp_path):
    _run_with_verdict(tmp_path / "run", _RETRIED, False)
    got = _harvest(tmp_path / "run")
    assert (got.execution.failed, got.execution.recovered) == (0, 2)
    assert got.execution.nextflow_verdict is None
    assert any("retried" in w and "judged from the execution trace" in w for w in got.warnings)


def test_a_report_from_another_attempt_is_not_used(tmp_path):
    # A restarted run still in progress has a newer trace than its newest
    # report: that report's verdict describes an earlier attempt.
    _run_with_verdict(tmp_path / "run", _RETRIED, "Workflow execution completed unsuccessfully!",
                      report_ts="2025-11-14_05-59-33")
    got = _harvest(tmp_path / "run")
    assert got.execution.nextflow_verdict is None
    assert (got.execution.failed, got.execution.recovered) == (0, 2)
    assert any("different attempt" in w for w in got.warnings)


def test_a_report_with_no_verdict_falls_back_to_the_trace(tmp_path):
    _run_with_verdict(tmp_path / "run", _RETRIED, None)
    got = _harvest(tmp_path / "run")
    assert (got.execution.failed, got.execution.recovered) == (0, 2)
    assert any("no completion verdict" in w for w in got.warnings)


def test_a_clean_run_gets_no_retry_warning(tmp_path):
    _run_with_verdict(tmp_path / "run", "task_id\tname\tstatus\n1\tA\tCOMPLETED\n",
                      "Workflow execution completed successfully!")
    got = _harvest(tmp_path / "run")
    assert (got.execution.failed, got.execution.recovered) == (0, 0)
    assert not [w for w in got.warnings if "retried" in w or "verdict" in w]


# --- nf-core/differentialabundance: the contrasts file ---

def _da_params(root, **params):
    import json as _json
    (root / "pipeline_info" / "params_2025-09-16_11-59-32.json").write_text(_json.dumps(params))


def test_the_contrasts_file_params_names_is_read_into_the_manifest(tmp_path):
    root = tmp_path / "run"
    _minimal_run(root)
    _da_params(root, contrasts="250818Rib_contrasts.csv", study_type="rnaseq")
    (root / "250818Rib_contrasts.csv").write_text(
        "id,variable,reference,target,blocking\nLPS_vs_Ctrl,Condition,Ctrl,LPS,Donor\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert got.contrasts == {"LPS_vs_Ctrl": {
        "variable": "Condition", "reference": "Ctrl", "target": "LPS", "blocking": "Donor"}}
    assert got.sources["contrasts"] == "250818Rib_contrasts.csv"


def test_a_contrasts_file_outside_the_run_folder_leaves_direction_blank_and_says_so(tmp_path):
    # Older runs keep the contrasts file in the launch folder, which the
    # harvest never reads; the direction is blank, never guessed.
    root = tmp_path / "run"
    _minimal_run(root)
    _da_params(root, contrasts="../launch/240404Kel_diffAbun_contrasts.csv")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    assert got.contrasts == {}
    assert any("240404Kel_diffAbun_contrasts.csv" in w and "not in the run folder" in w
               for w in got.warnings)


_OLD_INVENTORY = {
    "**/*.bam", "**/*.bam.bai", "**/*.cram", "**/*.cram.crai",
    "**/*.merged.gene_counts.tsv", "**/*.merged.gene_tpm.tsv",
    "**/*.merged.transcript_counts.tsv", "**/*.merged.transcript_tpm.tsv",
    "**/*.h5ad", "**/*.mtx", "**/*.mtx.gz", "**/*.rds", "**/*.RData",
    "**/*.vcf", "**/*.vcf.gz",
    "multiqc*/**/*multiqc_report.html", "**/reports/multiqc/*multiqc_report.html",
    "**/report/*.html", "**/report/*/*_differentialabundance_report.html",
    "**/*.kraken2.report.txt",
    "**/mirna_quant/mirtop/mirna.tsv", "**/salmon/*/quant.sf",
    "**/processed_abundance/all.normalised_counts.tsv",
    "**/processed_abundance/*/all.normalised_counts.tsv",
    "**/tables/differential/*.deseq2.results.tsv",
    "**/tables/differential/*.limma.results.tsv",
    "**/tables/differential/*/*.deseq2.results.tsv",
    "**/tables/differential/*/*.limma.results.tsv",
    "**/tables/differential/*/*.dream.results.tsv",
    # hlatyping's A.HLA rule: OptiType's call and its coverage plot
    "**/optitype/*/*_result.tsv", "**/optitype/*/*_coverage_plot.pdf",
    # sarek's rules: CRAMs under preprocessing/<step>/<sample>/, and the
    # per-caller VCFs with their tabix indexes under variant_calling/<caller>/<sample>/
    "**/preprocessing/*/*/*.cram", "**/preprocessing/*/*/*.cram.crai",
    "**/variant_calling/*/*/*.vcf.gz", "**/variant_calling/*/*/*.vcf.gz.tbi",
}


def test_the_inventory_union_lists_exactly_what_the_shared_list_did():
    assert set(harvest.inventory_globs()) == _OLD_INVENTORY


def test_pipeline_specific_patterns_live_in_their_maps_not_the_core():
    core = set(harvest.CORE_INVENTORY_GLOBS)
    assert "**/salmon/*/quant.sf" not in core
    assert not any("differential" in p or "mirtop" in p or ".merged." in p for p in core)


def test_no_inventory_pattern_still_has_braces():
    assert not [p for p in harvest.inventory_globs() if "{" in p or "}" in p]


def _hlatyping_stats(root, text):
    stats_dir = root / "multiqc" / "multiqc_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(text)


def test_per_read_fastqc_rows_fold_into_the_sample_for_keys_the_map_claims(tmp_path):
    # MultiQC writes FastQC per read file (<sample>_1, <sample>_2), never on
    # the sample's own row. The hlatyping map reads GC and read length from
    # those rows, so they arrive on the sample as <key>@R1 / <key>@R2.
    root = tmp_path / "run"
    _hlatyping_run(root)
    _hlatyping_stats(root,
        "Sample\toptitype-A1\tfastqc-percent_gc\tfastqc-avg_sequence_length\tfastqc-percent_fails\n"
        "PATIENT1\tA*02:01\t\t\t\n"
        "PATIENT1_1\t\t48.0\t101.0\t9.0\n"
        "PATIENT1_2\t\t49.0\t100.0\t9.0\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    sample = next(s for s in got.samples if s.nfcore_sample == "PATIENT1")
    assert sample.metrics["fastqc-percent_gc@R1"] == 48.0
    assert sample.metrics["fastqc-avg_sequence_length@R2"] == 100.0
    # No rule reads it, so folding it would only fill the review queue.
    assert "fastqc-percent_fails@R1" not in sample.metrics
    assert [s.nfcore_sample for s in got.samples] == ["PATIENT1"]


def test_a_samplesheet_sample_named_like_a_mate_row_is_not_folded(tmp_path):
    # A real replicate may be named PATIENT1_1 alongside PATIENT1; its row is
    # that sample's own, never PATIENT1's read 1.
    root = tmp_path / "run"
    _hlatyping_run(root)
    (root / "samplesheet.csv").write_text(
        "sample,fastq_1\nPATIENT1,/net/a.fastq.gz\nPATIENT1_1,/net/b.fastq.gz\n")
    _hlatyping_stats(root,
        "Sample\tfastqc-percent_gc\n"
        "PATIENT1\t47.0\n"
        "PATIENT1_1\t48.0\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    sample = next(s for s in got.samples if s.nfcore_sample == "PATIENT1")
    assert "fastqc-percent_gc@R1" not in sample.metrics
    assert sample.metrics["fastqc-percent_gc"] == 47.0


def test_metric_based_derived_values_reach_the_sample(tmp_path):
    # Salmon's library type becomes Strandedness when no RSeQC
    # infer_experiment exists to supply it.
    root = tmp_path / "run"
    _minimal_run(root)
    stats_dir = root / "multiqc" / "multiqc_report_data"
    stats_dir.mkdir(parents=True)
    (stats_dir / "multiqc_general_stats.txt").write_text(
        "Sample\tsalmon-library_types\tstar-uniquely_mapped_percent\n"
        "S1\tISR\t90.0\n")
    got = harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [])
    sample = next(s for s in got.samples if s.nfcore_sample == "S1")
    assert sample.derived["strandedness"] == "reverse (ISR)"
