"""Walk an allowlisted file set and build the RunManifest.

`harvest_local` works on a directory already on this filesystem. The op layer
stages the allowlisted files off Luria and then calls this, which is the whole
reason the harvester is testable against a fixture with no cluster access.

multiqc_data.json is deliberately NOT in the allowlist. It carries the same
numbers as the .txt tables at several MB instead of tens of KB, and reading it
would put the harvest outside the CC turn budget.
"""
from __future__ import annotations

import csv
import fnmatch
import io
import os
from collections import Counter
from pathlib import Path
from typing import Callable

from NessieAI.ns.reingest import derived, manifest, maps, parsers

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
#
# A bare "*.csv" glob is not enough by itself: with `--outdir .` the results
# root IS the run dir, and the fetchngs pre-stage can write its own `ids.csv`
# there too. `sorted(["samplesheet.csv", "ids.csv"])[0]` is `ids.csv` --
# picked silently, ahead of the real samplesheet, by a `first()` that only
# sorts alphabetically. `_find_samplesheet` below is the guard: it prefers an
# exact `samplesheet.csv`, then any `samplesheet*.csv`, before falling back to
# whatever else this glob matched, and it rejects any candidate whose header
# has no `sample` column -- `ids.csv` (empty, or one bare accession per line)
# never has one, so it is never mistaken for the real thing even when it
# sorts first.
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

# A sibling to GENERIC_GLOBS, but for a completely different purpose:
# GENERIC_GLOBS is small QC/metadata TEXT that gets staged (copied) and read
# for content; INVENTORY_GLOBS is never staged and never read -- it is only
# LISTED (name + real size) by granular.py's _STAGE_SCRIPT, which walks the
# run directory remotely and emits one inventory entry per match, subject to
# the SAME containment guards (no symlinks, no escapes, no hardlinks) as
# staging, plus its own file-count cap (MAX_INVENTORY_FILES).
#
# Covers the file classes that can become primary data (A.ALN, A.GEX, ...)
# or a named single-file link (multiqc_report_html, kraken2_report,
# deseq2_dds_rdata -- see _NAMED_OUTPUT_MATCHERS below): alignment files and
# their indexes, merged per-run count/abundance matrices, single-cell/R
# objects, VCFs, the MultiQC html report, and contaminant-screening reports.
# Deliberately broad ("**") rather than per-pipeline: this layer is generic
# across every nf-core pipeline, the same as GENERIC_GLOBS -- a per-pipeline
# map (docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md appendix
# A) narrows further downstream, against this inventory.
INVENTORY_GLOBS: tuple[str, ...] = (
    # Alignment files and their indexes.
    "**/*.bam",
    "**/*.bam.bai",
    "**/*.cram",
    "**/*.cram.crai",
    # Merged per-run count / abundance matrices.
    "**/*.merged.gene_counts.tsv",
    "**/*.merged.gene_tpm.tsv",
    "**/*.merged.transcript_counts.tsv",
    "**/*.merged.transcript_tpm.tsv",
    # Single-cell / R objects that become primary data or a named link.
    "**/*.h5ad",
    "**/*.mtx",
    "**/*.mtx.gz",
    "**/*.rds",
    "**/*.RData",
    # Variant calls.
    "**/*.vcf",
    "**/*.vcf.gz",
    # The MultiQC html report -- named link $outputs.multiqc_report_html.
    "multiqc*/**/multiqc_report.html",
    # Contaminant screening reports -- named link $outputs.kraken2_report.
    "**/*.kraken2.report.txt",
)

MAX_FILE_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILE_BYTES", 4_000_000))
MAX_TOTAL_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_TOTAL_BYTES", 32_000_000))
MAX_FILES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILES", 500))
# The inventory lists names and sizes only -- nothing is transferred or read
# -- so it needs no byte ceiling, only a count cap, the same idiom as the
# other caps above.
MAX_INVENTORY_FILES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_INVENTORY_FILES", 500))

# MultiQC's per-read rows (`<sample>_1`, `<sample>_2`) populate only these
# modules' columns; every other module (star, salmon, samtools_*, qualimap_*,
# ...) is per-sample and leaves them blank. See _classify_general_stats_row.
_PER_READ_ROW_PREFIXES = ("fastqc_raw-", "fastqc_trimmed-", "cutadapt-")


def harvest_local(root: str, *, inventory=None, lookup_by_fastq=None,
                   run_dir: str | None = None) -> manifest.RunManifest:
    """`root` stays the filesystem read root -- it is where every glob below
    actually looks for files, local disk only, no SSH, no network.

    `run_dir` is a LABEL for provenance and lookup, not a path this function
    reads from: it is what the caller staged `root` FROM (e.g. the cluster
    run directory), used for `RunManifest.run_dir`, `PipelineInfo.run_name`,
    and as the key `uid_resolve.resolve` looks up `PipelineRun.run_dir`
    against. It defaults to `str(root)` so an existing local-directory caller
    (or test) that never passes it keeps today's behavior unchanged. A
    caller that stages into a `tempfile.TemporaryDirectory()` and forgets to
    pass the real `run_dir` gets a manifest labeled with the temp path --
    which never matches any `PipelineRun` row, so the launch record (the
    primary UID source) silently resolves nothing.

    `inventory` is the OUTPUT-file listing (`[{"path", "bytes"}, ...]`) a
    caller gathered remotely by matching INVENTORY_GLOBS against the real
    run directory -- see granular.py's `_STAGE_SCRIPT`. This function stays
    local-only and never sees that real directory (only `root`, a staged
    temp copy of the small GENERIC_GLOBS text files), so it cannot gather
    the inventory itself; it only turns the given listing into
    `RunManifest.outputs` and, from that, `RunManifest.named_outputs`.
    Defaults to `None` (treated as empty) so a purely local caller -- one
    with no remote listing to pass, e.g. an existing test or a
    local-directory caller -- still works, just with no outputs recorded.

    `lookup_by_fastq(path, types)` is the fastq-path fallback UID lookup
    (see `uid_resolve`'s module docstring for when it is tried at all); its
    contract now takes a second argument, the tuple of sample-type codes to
    search, because a run's pipeline map may declare a wider
    `accepts_parent_types` than the default `("D.SEQ",)` (see
    `maps.PipelineMap.accepts_parent_types`). This function is the one that
    resolves the map: it knows `out.pipeline.name` (parsed from
    software_versions, above) and `uid_resolve` does not, so `uid_resolve`
    itself keeps its original one-argument `Callable[[str], list[str]]`
    contract unchanged -- what it receives below is a closure that has
    already bound in the resolved `types` for this run. Defaults to `None`
    (treated as never matching anything) so a caller with nothing to look up
    against -- an existing test, or a local-directory caller with no D.SEQ
    catalog at hand -- still works.
    """
    base = Path(root)
    resolved_run_dir = run_dir if run_dir is not None else str(base)
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

    out = manifest.RunManifest(run_dir=resolved_run_dir)

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
            run_name=Path(resolved_run_dir).name,
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

    sheet = _find_samplesheet(base, read)
    rows: list[dict] = []
    if sheet:
        candidate_rel, text = sheet
        candidate_rows = parsers.parse_samplesheet(text)
        if candidate_rows:
            sources["samples"], rows = candidate_rel, candidate_rows
        else:
            # Found and named right (it passed _find_samplesheet's header
            # check), but parsed to zero data rows. A "successful" read of an
            # empty samplesheet must not look identical to a genuinely absent
            # one -- that would leave `manifest.samples` empty with no
            # warning at all. Fall through to the general-stats fallback
            # below exactly as if no samplesheet had matched, but say why.
            warnings.append(
                f"{candidate_rel}: samplesheet matched but parsed to zero "
                "rows; falling back to multiqc general stats for sample names")
            sheet = None
    if not sheet:
        if stats:
            # No validated samplesheet in this run (nf-core/rnaseq 3.22
            # writes none into the results directory -- see
            # _SAMPLESHEET_GLOB above), or the one that matched parsed to
            # zero rows (see above). Fall back to the general-stats sample
            # rows themselves: MultiQC also emits a per-read row for each
            # mate (`<sample>_1`, `<sample>_2`), so a row is excluded only
            # when it is confidently classified as one of those per-read
            # rows -- not merely because its populated columns are
            # exclusively ones MultiQC fills for per-read rows, which a real
            # sample whose alignment step failed also has (see
            # _classify_general_stats_row for how the two are told apart, and
            # why a same-named row existing is not by itself the test -- that
            # would also wrongly drop a real replicate sample legitimately
            # named e.g. "A_1" alongside "A"). Anything left ambiguous is
            # kept as a sample and warned about rather than silently dropped.
            if stats_source:
                sources["samples"] = stats_source
            warnings.append(
                "no validated samplesheet; sample names came from multiqc "
                "general stats, so fastq-based D.SEQ UID resolution is "
                "unavailable for them")
            sample_names = []
            for name in sorted(stats):
                classification = _classify_general_stats_row(name, stats[name], stats)
                if classification == "per_read":
                    continue
                if classification == "ambiguous":
                    warnings.append(
                        f"{name}: only fastqc/cutadapt columns are populated and "
                        "no sibling per-sample row was found under its stripped "
                        "_1/_2 base name; kept as a sample rather than risk "
                        "silently dropping a real sample whose alignment failed "
                        "(see _classify_general_stats_row)")
                sample_names.append(name)
            rows = [{"sample": name, "fastq_1": "", "fastq_2": "", "strandedness": ""}
                    for name in sample_names]
        else:
            warnings.append("no validated samplesheet; samples cannot be resolved")

    from NessieAI.ns.reingest import uid_resolve

    # Which sample types this run's own pipeline may point back to as a
    # parent -- see maps.PipelineMap.accepts_parent_types. `out.pipeline.name`
    # is only just now known (parsed from software_versions, above; it is ""
    # when that file never arrived), so this is the first point in the
    # harvest where the map CAN be resolved -- not at this function's own
    # call site (the caller stages files and invokes harvest_local before
    # any pipeline identity is known at all). An unrecognised or not-yet-
    # committed pipeline name (UnknownPipelineMap, or "" itself) falls back
    # to the same ("D.SEQ",) scope this lookup always used, rather than
    # raising out of a read-only harvest step.
    try:
        pipeline_map = maps.load(out.pipeline.name) if out.pipeline.name else None
    except maps.UnknownPipelineMap:
        pipeline_map = None
    accepts_parent_types = (
        tuple(pipeline_map.accepts_parent_types) if pipeline_map is not None
        else ("D.SEQ",))

    # `uid_resolve.resolve` keeps its original one-argument
    # Callable[[str], list[str]] contract (see its own module docstring) --
    # this closure is what "builds" the callable it actually receives,
    # binding in `accepts_parent_types` so `uid_resolve` never has to know
    # about maps or types scoping at all.
    base_lookup = lookup_by_fastq or (lambda path, types: [])

    def _scoped_lookup(path: str) -> list[str]:
        return base_lookup(path, accepts_parent_types)

    # `multirun_resolved_rows` is filled in as a side effect: {sample:
    # resolved_row_count}, the number of a multi-run sample's OWN
    # contributing rows that resolved to a UID, counted before
    # `uid_resolve`'s own first-occurrence de-duplication into `parents`
    # collapses same-UID rows together. The partial-resolution warning below
    # needs that un-collapsed count, not len(parents) -- see
    # uid_resolve._resolve_multirun_parents.
    multirun_resolved_rows: dict[str, int] = {}
    resolved = uid_resolve.resolve(
        rows, resolved_run_dir, _scoped_lookup,
        multirun_resolved_rows=multirun_resolved_rows)
    by_sample = {name: (uid, how, parents) for name, uid, how, parents in resolved}
    # A multi-run sample has several samplesheet ROWS sharing one name, but
    # must become exactly ONE SampleRecord -- one A.ALN child, not several
    # identical ones -- so rows are walked in order and every name after its
    # first occurrence is skipped. This is a no-op for every non-multi-run
    # sample, which by construction (uid_resolve's own Counter check) never
    # has a second row to skip.
    seen_samples: set[str] = set()
    row_counts = Counter(str(row.get("sample") or "") for row in rows)

    for row in rows:
        name = str(row.get("sample") or "")
        if name in seen_samples:
            continue
        seen_samples.add(name)
        uid, how, parents = by_sample.get(
            name, (None, manifest.RESOLUTION_UNRESOLVED, ()))
        if how == manifest.RESOLUTION_MULTIRUN:
            # Compare RESOLVED ROWS against the raw row count, not
            # de-duplicated parents against it: two rows resolving to the
            # SAME D.SEQ (one record whose File_PrimaryData lists both
            # lanes, say) collapses len(parents) below row_counts[name] even
            # though every row resolved -- that is a complete Parent list,
            # not a partial one. multirun_resolved_rows carries the
            # un-collapsed count; len(parents) is only a fallback for a name
            # that somehow never made it into that dict.
            resolved_rows = multirun_resolved_rows.get(name, len(parents))
            if 0 < resolved_rows < row_counts[name]:
                # Some, but not all, of this sample's contributing rows
                # resolved to a D.SEQ -- a real, honest partial parent list
                # (see uid_resolve._resolve_multirun_parents), not a bug.
                # Surfaced here, not silently: this is the layer that knows
                # how many rows SHOULD have contributed and so can tell
                # "partial" apart from "none of them resolved", which
                # uid_resolve cannot on its own.
                warnings.append(
                    f"{name}: multi-run sample resolved {resolved_rows} of "
                    f"{row_counts[name]} contributing D.SEQ parents; Parent "
                    "will be a partial list")
        out.samples.append(manifest.SampleRecord(
            nfcore_sample=name,
            fastq_1=str(row.get("fastq_1") or ""),
            fastq_2=str(row.get("fastq_2") or "") or None,
            d_seq_uid=uid,
            d_seq_uid_multirun=list(parents),
            uid_resolution=how,
            strandedness_declared=str(row.get("strandedness") or "") or None,
            metrics=stats.get(name, {}),
            derived=_derived_for(base, name, read),
        ))

    # The inventory is gathered elsewhere (remotely, against the real run
    # directory -- see this function's docstring) and handed in whole; it is
    # never globbed or read here. Each entry becomes one `outputs` record,
    # attributed to a sample where derivable; `named_outputs` is then
    # resolved from that same list.
    sample_names = [s.nfcore_sample for s in out.samples]
    for entry in inventory or []:
        rel = str((entry or {}).get("path") or "")
        if not rel:
            continue
        out.outputs.append(manifest.OutputRecord(
            path=rel, bytes=int((entry or {}).get("bytes") or 0),
            sample=_sample_for_output_path(rel, sample_names)))
    out.named_outputs = _resolve_named_outputs(out.outputs)

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


def _samplesheet_header(text: str) -> list[str]:
    """The raw first row of a CSV -- field names, unparsed as data. Used to
    check for a `sample` column before trusting a `*.csv` match is the real
    samplesheet, without fully parsing it through `parsers.parse_samplesheet`
    (that happens once the candidate is accepted)."""
    return next(csv.reader(io.StringIO(text)), [])


def _find_samplesheet(base: Path, read) -> tuple[str, str] | None:
    """Pick the samplesheet among every `_SAMPLESHEET_GLOB` ("*.csv") match.

    `--outdir .` makes the results root the run dir itself, so an unrelated
    CSV the fetchngs pre-stage writes there (`ids.csv`) can sort ahead of the
    real `samplesheet.csv` in a plain alphabetical `first()`. Two guards fix
    that, applied together rather than either alone:

    1. Name preference: an exact `samplesheet.csv` first, then any
       `samplesheet*.csv`, and only then whatever else this glob matched.
    2. Header validation: a candidate whose first row has no `sample` column
       is rejected outright -- `ids.csv` (empty, or one bare SRA accession per
       line with no header at all) never has one, so even a pipeline variant
       that names its real samplesheet something else entirely does not get
       silently matched against a file that cannot be the samplesheet.

    Every rejected candidate is still read through `read()`, so it counts
    against the same byte/file caps as everything else this harvester reads.
    """
    def _priority(name: str) -> int:
        if name == "samplesheet.csv":
            return 0
        if fnmatch.fnmatch(name, "samplesheet*.csv"):
            return 1
        return 2

    candidates = sorted(base.glob(_SAMPLESHEET_GLOB),
                         key=lambda p: (_priority(p.name), str(p)))
    for path in candidates:
        rel = str(path.relative_to(base))
        text = read(rel)
        if text is None:
            continue
        if "sample" not in _samplesheet_header(text):
            continue
        return rel, text
    return None


def _pipeline_name(params: dict) -> str:
    return str(params.get("pipeline") or "")


def _sample_for_output_path(rel_path: str, sample_names: list[str]) -> str | None:
    """Best-effort per-sample attribution for one inventory entry, "where
    derivable" (per the design) -- never invented. A per-sample output names
    its sample in the basename (e.g. "CONTROL_REP1.markdup.sorted.bam"); a
    per-run output (e.g. a merged matrix) names no single sample at all, and
    correctly gets None here rather than a guess.

    Matching is on the basename only, and requires the sample name to be
    either the whole stem or followed by a "." or "_" separator -- a bare
    substring match would let a sample named "A" claim an output that
    actually belongs to sample "AB" ("AB.markdup.sorted.bam" contains "A" as
    a substring, but does not start with "A." or "A_"). When more than one
    known sample name matches this way (a name that is itself a prefix of
    another, e.g. "A" and "A_1"), the LONGEST match wins, so "A_1.bam" is
    attributed to "A_1", never mistakenly to "A".
    """
    name = Path(rel_path).name
    best: str | None = None
    for sample in sample_names:
        if not sample:
            continue
        if name == sample or name.startswith(sample + ".") or name.startswith(sample + "_"):
            if best is None or len(sample) > len(best):
                best = sample
    return best


# Well-known SINGLE files the next reingest plan's map references by key --
# "$outputs.<key>" resolves against RunManifest.named_outputs (a dict), never
# against RunManifest.outputs (a list) -- see manifest.py's docstring on both
# fields and docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md.
# Each matcher runs against the LOWERCASED basename only, over every
# inventory entry sorted by path for a deterministic pick among ties (e.g.
# two aligners' MultiQC reports both matching multiqc_report.html).
_NAMED_OUTPUT_MATCHERS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("multiqc_report_html", lambda name: name == "multiqc_report.html"),
    ("kraken2_report", lambda name: name.endswith(".kraken2.report.txt")),
    ("deseq2_dds_rdata", lambda name: name.endswith(".dds.rdata") or name.endswith(".dds.rds")),
)


def _resolve_named_outputs(outputs: list[manifest.OutputRecord]) -> dict[str, str]:
    named: dict[str, str] = {}
    for key, matches in _NAMED_OUTPUT_MATCHERS:
        for record in sorted(outputs, key=lambda o: o.path):
            if matches(Path(record.path).name.lower()):
                named[key] = record.path
                break
    return named


def _classify_general_stats_row(name: str, columns: dict, stats: dict) -> str:
    """Classify one multiqc_general_stats.txt row as "sample", "per_read", or
    "ambiguous", for the samplesheet-less fallback that must decide which
    rows are real biological samples.

    Any row with at least one column outside _PER_READ_ROW_PREFIXES is
    unambiguously "sample": those columns (star, salmon, samtools_*,
    qualimap_*, ...) are per-sample and MultiQC never fills them in for a
    per-read row.

    A row where EVERY populated column is fastqc/cutadapt-prefixed is NOT
    safely "per_read" on the column signature alone. Column signature alone
    cannot tell apart two very different rows:
      (a) MultiQC's own per-read split of a real sample -- `<sample>_1` and
          `<sample>_2` -- which sits ALONGSIDE that sample's own per-sample
          row, since fastqc/cutadapt run per-mate regardless of what happens
          downstream.
      (b) a real biological sample whose alignment step FAILED: star,
          salmon, samtools_* and qualimap_* never ran, so nothing but
          fastqc/cutadapt ever gets written for it -- the exact same column
          signature as (a), with nothing to tell them apart by columns.
    The two are told apart structurally instead: (a) is only a genuine
    MultiQC artifact when the row's name ends "_1" or "_2" AND the stripped
    base name is ALSO a row in this same table (that base row is the real
    per-sample data the mate rows split off from -- fastqc/cutadapt would
    not otherwise produce a lone "_1"/"_2" row with no sibling). Naming
    alone is still not enough either way: a samplesheet can legitimately
    name a real replicate "A_1" alongside a sample "A", which is why the
    base-row check only fires once the column signature already narrowed
    things to "nothing but fastqc/cutadapt ran" -- "A_1" with a populated
    star- column is caught by the first check above and never reaches here.
    Anything that reaches this point without a confirmed sibling -- a
    plainly-named failed sample, or one with a "_1"/"_2" suffix but no base
    row -- is "ambiguous": kept as a sample rather than risk silently
    dropping a real one, per Important 1 of the 2026-09-16 review.
    Confirmed against the real CONTROL_REP1_1/_2 and TREATED_REP1_1/_2 rows
    (case (a)) in the fixture's multiqc_general_stats.txt.
    """
    if not columns:
        return "sample"
    if any(not column.startswith(_PER_READ_ROW_PREFIXES) for column in columns):
        return "sample"
    if name.endswith(("_1", "_2")):
        base = name[:-2]
        if base and base != name and base in stats:
            return "per_read"
    return "ambiguous"


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
