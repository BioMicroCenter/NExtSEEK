"""Walk an allowlisted file set and build the RunManifest.

`harvest_local` works on a directory already on this filesystem. The op layer
stages the allowlisted files off Luria and then calls this, which is the whole
reason the harvester is testable against a fixture with no cluster access.

multiqc_data.json is deliberately NOT in the allowlist. It carries the same
numbers as the .txt tables at several MB instead of tens of KB, and reading it
would put the harvest outside the CC turn budget.
"""
from __future__ import annotations

import os
from pathlib import Path

from NessieAI.ns.reingest import derived, manifest, parsers

# Verified against a real nf-core/rnaseq 3.22.2 run. Each line here was wrong in
# the version written from documentation; see the fixture README.
GENERIC_GLOBS: tuple[str, ...] = (
    "pipeline_info/params*.json",
    "pipeline_info/*software*versions*.yml",   # nf_core_<pipeline>_software_mqc_versions.yml
    "pipeline_info/execution_trace*.txt",
    "*.csv",                                   # the input samplesheet, beside nextflow.config --
                                               # ABSENT in nf-core/rnaseq 3.22, which writes no
                                               # validated samplesheet into the results directory
                                               # (see the fixture README). When missing, sample
                                               # identity falls back to the general-stats rows.
    "multiqc*/**/*_data/multiqc_*.txt",        # multiqc_report_data, and only the summary
                                               # tables -- the *_plot_*.txt files are per-sample
                                               # series, and multiqc_data.json is 11.8 MB
                                               # against general_stats' 10.5 KB
)

MAX_FILE_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILE_BYTES", 4_000_000))
MAX_TOTAL_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_TOTAL_BYTES", 32_000_000))
MAX_FILES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILES", 500))


def harvest_local(root: str, *, extra_globs=None, lookup_by_fastq=None) -> manifest.RunManifest:
    base = Path(root)
    caps = manifest.CapsInfo()
    sources: dict[str, str] = {}
    warnings: list[str] = []

    def read(rel: str) -> str | None:
        path = base / rel
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_FILE_BYTES or caps.bytes_read + size > MAX_TOTAL_BYTES \
                or caps.files_read >= MAX_FILES:
            caps.truncated.append(rel)
            return None
        caps.files_read += 1
        caps.bytes_read += size
        return path.read_text(encoding="utf-8", errors="replace")

    def first(pattern: str) -> tuple[str, str] | None:
        for path in sorted(base.glob(pattern)):
            rel = str(path.relative_to(base))
            text = read(rel)
            if text is not None:
                return rel, text
        return None

    out = manifest.RunManifest(run_dir=str(base))

    found = first("pipeline_info/params*.json")
    if found:
        sources["params"], text = found[0], found[1]
        out.params = parsers.parse_params(text)

    found = first("pipeline_info/*software*versions*.yml")
    has_versions = found is not None
    if found:
        sources["software_versions"], text = found[0], found[1]
        out.software_versions = parsers.parse_software_versions(text)
        by_process = parsers.parse_software_versions_by_process(text)
        out.software_versions_by_process = by_process

        # Wire up the conflict warning: the flat map above keeps only the
        # last-seen version for a tool reported at several distinct versions
        # across processes (e.g. "star" at 2.7.10a under
        # MAKE_TRANSCRIPTS_FASTA and 2.7.11b under STAR_ALIGN in the 3.22
        # fixture). software_version_conflicts() names exactly what that
        # flattening discarded; surface it so a reader of `software_versions`
        # alone is warned it may not be the whole picture.
        for tool, versions in sorted(parsers.software_version_conflicts(text).items()):
            warnings.append(
                f"{tool}: conflicting versions across processes "
                f"({', '.join(versions)})")

        workflow_block = by_process.get("Workflow", {})
        pipeline_name = next((k for k in workflow_block if k != "Nextflow"), "")
        out.pipeline = manifest.PipelineInfo(
            name=pipeline_name or _pipeline_name(out.params),
            version=str(workflow_block.get(pipeline_name, "")
                        or out.params.get("pipeline_version", "")),
            nextflow_version=str(workflow_block.get("Nextflow", "")),
            run_name=base.name,
        )

    found = first("pipeline_info/execution_trace*.txt")
    if found:
        sources["execution"], text = found[0], found[1]
        out.execution = manifest.ExecutionInfo(**parsers.parse_execution_trace(text))

    stats: dict[str, dict] = {}
    found = first("multiqc*/**/*_data/multiqc_general_stats.txt")
    if found:
        sources["metrics"], text = found[0], found[1]
        stats = parsers.parse_general_stats(text)
    else:
        warnings.append("no multiqc general stats found; QC metrics unavailable")

    found = first("*.csv")
    rows: list[dict] = []
    if found:
        sources["samples"], text = found[0], found[1]
        rows = parsers.parse_samplesheet(text)
    elif stats:
        # No validated samplesheet in this run (nf-core/rnaseq 3.22 writes
        # none into the results directory -- see GENERIC_GLOBS above). Fall
        # back to the general-stats sample rows themselves: MultiQC also
        # emits a per-read row for each mate (`<sample>_1`, `<sample>_2`,
        # populated only in the fastqc columns -- see
        # parsers.parse_general_stats), so a name is a real biological
        # sample only if stripping a trailing "_1"/"_2" does NOT land on
        # another key already in `stats`.
        warnings.append("no validated samplesheet; samples cannot be resolved")
        sample_names = sorted(
            name for name in stats
            if not (name.endswith(("_1", "_2")) and name.rsplit("_", 1)[0] in stats)
        )
        rows = [{"sample": name, "fastq_1": "", "fastq_2": "", "strandedness": ""}
                for name in sample_names]
    else:
        warnings.append("no validated samplesheet; samples cannot be resolved")

    from NessieAI.ns.reingest import uid_resolve
    resolved = uid_resolve.resolve(rows, str(base), lookup_by_fastq or (lambda p: []))
    by_sample = {name: (uid, how) for name, uid, how in resolved}

    for row in rows:
        name = str(row.get("sample") or "")
        uid, how = by_sample.get(name, (None, manifest.RESOLUTION_UNRESOLVED))
        out.samples.append(manifest.SampleRecord(
            nfcore_sample=name,
            fastq_1=str(row.get("fastq_1") or ""),
            fastq_2=str(row.get("fastq_2") or "") or None,
            d_seq_uid=uid,
            uid_resolution=how,
            strandedness_declared=str(row.get("strandedness") or "") or None,
            metrics=stats.get(name, {}),
            derived=_derived_for(base, name, read),
        ))

    out.run_status = (manifest.RUN_COMPLETE
                      if has_versions and out.execution.non_terminal == 0
                      else manifest.RUN_INCOMPLETE)
    out.sources = sources
    out.warnings = warnings
    out.caps = caps
    return out


def _pipeline_name(params: dict) -> str:
    return str(params.get("pipeline") or "")


def _derived_for(base: Path, sample: str, read) -> dict[str, float]:
    """Per-sample derived metrics from whichever per-sample QC files exist.

    STAR and per-gene counts are not in the harvest allowlist (see
    GENERIC_GLOBS), so `star` and `counts` are always None here: `compute()`
    simply omits the metrics that depend on them (unaligned_reads,
    genes_detected*, top30_count_percent). That is the designed behaviour,
    not a gap to fill in this function.
    """
    dist = None
    for path in sorted(base.glob(f"*/rseqc/read_distribution/{sample}.read_distribution.txt")):
        text = read(str(path.relative_to(base)))
        if text:
            dist = derived.parse_read_distribution(text)
        break

    infer = None
    for path in sorted(base.glob(f"*/rseqc/infer_experiment/{sample}.infer_experiment.txt")):
        text = read(str(path.relative_to(base)))
        if text:
            infer = derived.parse_infer_experiment(text)
        break

    return derived.compute(dist, infer, None, None, None)
