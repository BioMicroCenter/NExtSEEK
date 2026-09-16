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

# Each of these is used verbatim below -- never retyped as a second, separate
# literal -- so the allowlist and what harvest_local actually reads can never
# drift apart. That drift is exactly what let the two RSeQC patterns go
# missing from GENERIC_GLOBS for a while: the run-harvest op (Task 9) stages
# files off the cluster by iterating GENERIC_GLOBS over SSH, so a pattern
# harvest_local reads but GENERIC_GLOBS omits is a pattern that is never
# staged, and every real run's per-sample `derived` would silently come back
# empty. test_every_glob_the_harvester_reads_is_in_generic_globs guards this.
#
# Verified against a real nf-core/rnaseq 3.22.2 run. Each line here was wrong
# in the version written from documentation; see the fixture README.
_PARAMS_GLOB = "pipeline_info/params*.json"
_SOFTWARE_VERSIONS_GLOB = "pipeline_info/*software*versions*.yml"  # nf_core_<pipeline>_software_mqc_versions.yml
_EXECUTION_TRACE_GLOB = "pipeline_info/execution_trace*.txt"
# the input samplesheet, beside nextflow.config -- ABSENT in nf-core/rnaseq
# 3.22, which writes no validated samplesheet into the results directory
# (see the fixture README). When missing, sample identity falls back to the
# general-stats rows.
_SAMPLESHEET_GLOB = "*.csv"
# multiqc_report_data's flat text tables. A single "*" here does NOT
# distinguish MultiQC's per-module summary tables from its `*_plot_*.txt`
# series files -- this also matches e.g. multiqc_featurecounts_biotype_plot.txt.
# That is fine: harvest_local below only ever picks out multiqc_general_stats.txt
# by name (first_named), so the breadth here just means the allowlist stages
# (and this could, in future, read) the other per-module tables too, per the
# fixture README's note that they are the preferred source over the
# per-sample RSeQC files. multiqc_data.json is excluded separately -- 11.8 MB
# against general_stats' 10.5 KB.
_MULTIQC_TXT_GLOB = "multiqc*/**/*_data/multiqc_*.txt"
_RSEQC_READ_DISTRIBUTION_GLOB = "*/rseqc/read_distribution/*.read_distribution.txt"
_RSEQC_INFER_EXPERIMENT_GLOB = "*/rseqc/infer_experiment/*.infer_experiment.txt"

GENERIC_GLOBS: tuple[str, ...] = (
    _PARAMS_GLOB,
    _SOFTWARE_VERSIONS_GLOB,
    _EXECUTION_TRACE_GLOB,
    _SAMPLESHEET_GLOB,
    _MULTIQC_TXT_GLOB,
    _RSEQC_READ_DISTRIBUTION_GLOB,
    _RSEQC_INFER_EXPERIMENT_GLOB,
)

MAX_FILE_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILE_BYTES", 4_000_000))
MAX_TOTAL_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_TOTAL_BYTES", 32_000_000))
MAX_FILES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILES", 500))

# MultiQC's per-read rows (`<sample>_1`, `<sample>_2`) populate only these
# modules' columns; every other module (star, salmon, samtools_*, qualimap_*,
# ...) is per-sample and leaves them blank. See _is_per_read_row.
_PER_READ_ROW_PREFIXES = ("fastqc_raw-", "fastqc_trimmed-", "cutadapt-")


def harvest_local(root: str, *, extra_globs=None, lookup_by_fastq=None) -> manifest.RunManifest:
    base = Path(root)
    # extra_globs extends the allowlist for this call only -- e.g. a pipeline
    # variant with an additional generic file worth capturing. Matches are
    # read (subject to the same caps as everything else) and recorded as
    # `outputs`; they are not assigned to any of the named sections below,
    # since nothing here knows what role an arbitrary extra pattern plays.
    allowed_globs = GENERIC_GLOBS + tuple(extra_globs or ())
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

    def first_named(pattern: str, filename: str) -> tuple[str, str] | None:
        """Like `first`, but only among candidates named exactly `filename`.

        `_MULTIQC_TXT_GLOB` is deliberately broader than "just general
        stats" (see its comment above), so the specific file this stage
        wants is picked out by name from that same allowlisted glob, rather
        than a second, narrower glob pattern typed separately that could
        drift from GENERIC_GLOBS.
        """
        for path in sorted(base.glob(pattern)):
            if path.name != filename:
                continue
            rel = str(path.relative_to(base))
            text = read(rel)
            if text is not None:
                return rel, text
        return None

    out = manifest.RunManifest(run_dir=str(base))

    found = first(_PARAMS_GLOB)
    if found:
        sources["params"], text = found[0], found[1]
        out.params = parsers.parse_params(text)

    found = first(_SOFTWARE_VERSIONS_GLOB)
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

    found = first(_EXECUTION_TRACE_GLOB)
    has_execution = found is not None
    if found:
        sources["execution"], text = found[0], found[1]
        out.execution = manifest.ExecutionInfo(**parsers.parse_execution_trace(text))

    stats: dict[str, dict] = {}
    stats_source: str | None = None
    found = first_named(_MULTIQC_TXT_GLOB, "multiqc_general_stats.txt")
    if found:
        stats_source, text = found[0], found[1]
        sources["metrics"] = stats_source
        stats = parsers.parse_general_stats(text)
    else:
        warnings.append("no multiqc general stats found; QC metrics unavailable")

    found = first(_SAMPLESHEET_GLOB)
    rows: list[dict] = []
    if found:
        sources["samples"], text = found[0], found[1]
        rows = parsers.parse_samplesheet(text)
    elif stats:
        # No validated samplesheet in this run (nf-core/rnaseq 3.22 writes
        # none into the results directory -- see _SAMPLESHEET_GLOB above).
        # Fall back to the general-stats sample rows themselves: MultiQC also
        # emits a per-read row for each mate (`<sample>_1`, `<sample>_2`), so
        # a row is excluded only when its populated columns are EXCLUSIVELY
        # ones MultiQC fills for those per-read rows (see
        # _is_per_read_row) -- not merely because its name ends "_1"/"_2" and
        # a same-named row exists, which would also wrongly drop a real
        # replicate sample legitimately named e.g. "A_1" alongside "A".
        if stats_source:
            sources["samples"] = stats_source
        warnings.append(
            "no validated samplesheet; sample names came from multiqc "
            "general stats, so fastq-based D.SEQ UID resolution is "
            "unavailable for them")
        sample_names = sorted(
            name for name, columns in stats.items()
            if not _is_per_read_row(columns))
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

    # allowed_globs is GENERIC_GLOBS followed by whatever extra_globs added;
    # slicing off the GENERIC_GLOBS prefix is what's left to scan here -- the
    # GENERIC_GLOBS patterns themselves were already read above under their
    # own named section, and must not be read a second time as a generic
    # "output".
    for pattern in allowed_globs[len(GENERIC_GLOBS):]:
        for path in sorted(base.glob(pattern)):
            rel = str(path.relative_to(base))
            text = read(rel)
            if text is None:
                continue
            out.outputs.append(manifest.OutputRecord(
                path=rel, bytes=len(text.encode("utf-8"))))

    # Complete requires BOTH the versions file present AND the trace showing
    # no non-terminal process. A trace that never arrived (not staged,
    # deleted, or cap-truncated) must not read as "no non-terminal processes
    # found" -- that would wave through a run whose completion was never
    # actually observed.
    out.run_status = (manifest.RUN_COMPLETE
                      if has_versions and has_execution and out.execution.non_terminal == 0
                      else manifest.RUN_INCOMPLETE)
    out.sources = sources
    out.warnings = warnings
    out.caps = caps
    return out


def _pipeline_name(params: dict) -> str:
    return str(params.get("pipeline") or "")


def _is_per_read_row(columns: dict) -> bool:
    """True for a multiqc_general_stats.txt row that is one of MultiQC's own
    per-read rows (`<sample>_1`, `<sample>_2`), not a real biological sample.

    Naming alone is not a safe signal: a samplesheet can legitimately name a
    real replicate "A_1" alongside a sample "A" -- a common numeric-replicate
    convention -- and a check based only on "name ends _1/_2 and the
    stripped name is also a row" would silently drop that real sample. What
    actually characterises MultiQC's per-read rows is which modules populate
    them: only the per-read fastqc/cutadapt columns are ever filled in for
    those rows (see _PER_READ_ROW_PREFIXES); every other module (star,
    salmon, samtools_*, qualimap_*, ...) is per-sample and leaves them blank.
    Confirmed against the real CONTROL_REP1_1/_2 and TREATED_REP1_1/_2 rows
    in the fixture's multiqc_general_stats.txt.
    """
    return bool(columns) and all(
        column.startswith(_PER_READ_ROW_PREFIXES) for column in columns)


def _rseqc_glob_for_sample(pattern: str, sample: str) -> str:
    """Substitute `sample` for the trailing wildcard in a GENERIC_GLOBS RSeQC
    pattern (e.g. "*/rseqc/read_distribution/*.read_distribution.txt" ->
    "*/rseqc/read_distribution/CONTROL_REP1.read_distribution.txt"), so
    _derived_for's per-sample search is grounded in the same allowlist entry
    the run-harvest op stages against, not a second literal typed here that
    could drift from it.
    """
    directory, _, filename_glob = pattern.rpartition("/")
    suffix = filename_glob.lstrip("*")
    return f"{directory}/{sample}{suffix}"


def _derived_for(base: Path, sample: str, read) -> dict[str, float]:
    """Per-sample derived metrics from whichever per-sample QC files exist.

    STAR and per-gene counts are not in the harvest allowlist (see
    GENERIC_GLOBS), so `star` and `counts` are always None here: `compute()`
    simply omits the metrics that depend on them (unaligned_reads,
    genes_detected*, top30_count_percent). That is the designed behaviour,
    not a gap to fill in this function.
    """
    dist = None
    dist_pattern = _rseqc_glob_for_sample(_RSEQC_READ_DISTRIBUTION_GLOB, sample)
    match = next(iter(sorted(base.glob(dist_pattern))), None)
    if match is not None:
        # Exactly one candidate is ever tried -- a cap hit (read() returning
        # None) is recorded in caps.truncated, not retried against another
        # match.
        text = read(str(match.relative_to(base)))
        if text:
            dist = derived.parse_read_distribution(text)

    infer = None
    infer_pattern = _rseqc_glob_for_sample(_RSEQC_INFER_EXPERIMENT_GLOB, sample)
    match = next(iter(sorted(base.glob(infer_pattern))), None)
    if match is not None:
        text = read(str(match.relative_to(base)))
        if text:
            infer = derived.parse_infer_experiment(text)

    return derived.compute(dist, infer, None, None, None)
