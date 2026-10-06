"""Format-level parsers for the files in an nf-core run's pipeline_info/.

Each function takes text and returns plain Python. None of them touch the
filesystem or the network, so every one is directly unit-testable.
"""
from __future__ import annotations

import csv
import io
import json

import yaml

# execution_trace.txt statuses that mean the process is finished, either way.
_TERMINAL = {"COMPLETED", "FAILED", "ABORTED", "CACHED"}
_FAILED = {"FAILED", "ABORTED"}
_SUCCEEDED = {"COMPLETED", "CACHED"}


def parse_params(text: str) -> dict:
    """pipeline_info/params*.json -> every resolved param, verbatim."""
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _version_field(value) -> str:
    """A process, tool or version from software_versions.yml, as a bare string.

    Pipelines pad these inconsistently: nf-core/scrnaseq 4.1.0 writes
    CELLRANGER_COUNT's cellranger version as '9.0.1 ' and every other
    CELLRANGER_* process's as 9.0.1. Padding is never part of a version, so
    every field is stripped once, here, before any view compares or keys on it.
    """
    return str(value).strip()


def parse_software_versions(text: str) -> dict[str, str]:
    """software_versions.yml -> {tool: version}, a lossy convenience view.

    nf-core nests one level (process -> {tool: version}); the Workflow block
    is shaped the same way (nf-core/rnaseq and Nextflow nested under
    "Workflow" in the 3.22 fixture, not flat at the top level). Both flatten
    through the same branch.

    The SAME tool name can appear under several processes at DIFFERENT
    versions: in the 3.22 fixture, MAKE_TRANSCRIPTS_FASTA reports
    star: 2.7.10a while STAR_ALIGN reports star: 2.7.11b. That is a real,
    legitimate difference (a pipeline can invoke different builds of a tool
    at different steps), not noise. This flat map keeps only the LAST value
    seen in file (dict-iteration) order and silently drops the rest -- it is
    a convenience for map files that key on `$software_versions.<tool>`, not
    a source of truth. Full fidelity lives in
    `parse_software_versions_by_process`, which keeps every process's view
    intact; use `software_version_conflicts` to detect when this flattening
    has discarded a genuine disagreement.
    """
    doc = yaml.safe_load(text) or {}
    flat: dict[str, str] = {}
    for key, value in doc.items():
        if isinstance(value, dict):
            for tool, version in value.items():
                flat[_version_field(tool)] = _version_field(version)
        else:
            flat[_version_field(key)] = _version_field(value)
    return flat


def parse_software_versions_by_process(text: str) -> dict[str, dict[str, str]]:
    """software_versions.yml -> {process: {tool: version}}, full fidelity.

    Unlike `parse_software_versions`, nothing is collapsed: every process
    (including the "Workflow" block) keeps its own {tool: version} map, so a
    tool reported at two different versions under two different processes
    (e.g. "star" under both MAKE_TRANSCRIPTS_FASTA and STAR_ALIGN in the 3.22
    fixture) is preserved rather than one silently overwriting the other.
    A top-level scalar (not a nested mapping) has no process name to key by,
    so it is kept under its own key as a single-entry {key: {key: value}}.
    """
    doc = yaml.safe_load(text) or {}
    by_process: dict[str, dict[str, str]] = {}
    for key, value in doc.items():
        key = _version_field(key)
        if isinstance(value, dict):
            by_process[key] = {_version_field(tool): _version_field(version)
                               for tool, version in value.items()}
        else:
            by_process[key] = {key: _version_field(value)}
    return by_process


def software_version_conflicts(text: str) -> dict[str, list[str]]:
    """{tool: [distinct versions]} for every tool reported at more than one
    distinct version across processes -- i.e. exactly what
    `parse_software_versions` silently collapses to its last-seen value.

    Versions are listed in first-seen (file) order. A tool reported
    consistently everywhere it appears is absent from the result.
    """
    by_process = parse_software_versions_by_process(text)
    seen: dict[str, list[str]] = {}
    for tool_versions in by_process.values():
        for tool, version in tool_versions.items():
            versions = seen.setdefault(tool, [])
            if version not in versions:
                versions.append(version)
    return {tool: versions for tool, versions in seen.items() if len(versions) > 1}


def parse_samplesheet(text: str) -> list[dict[str, str]]:
    """samplesheet.valid.csv -> one dict per row, keys as spelled in the header."""
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


_CONTRAST_FIELDS = ("variable", "reference", "target", "blocking")


def parse_contrasts(text: str, filename: str) -> dict[str, dict[str, str]]:
    """An nf-core/differentialabundance contrasts file -> {contrast id: direction}.

    Each value is {variable, reference, target, blocking}: the samplesheet
    column compared, the baseline group, the group compared against it (a
    positive log2 fold change means higher in the target), and the extra
    variables modelled alongside, ";"-joined. The format follows the file's
    extension, as the pipeline's own ``--contrasts`` does:

    - ``.csv`` / ``.tsv`` (1.x and 2.0.0): columns ``id, variable, reference,
      target`` and an optional ``blocking``, already ";"-delimited.
    - ``.yml`` / ``.yaml`` (2.0.0): a ``contrasts`` list whose entries give
      ``comparison: [variable, reference, target]`` and ``blocking_factors``. A
      formula contrast (``formula`` + ``make_contrasts_str``) states no single
      reference or target, so its fields stay "" rather than being guessed.

    A file without those columns (or that list) is not a contrasts file: {}.
    """
    blank = dict.fromkeys(_CONTRAST_FIELDS, "")
    name = filename.lower()
    if name.endswith((".yml", ".yaml")):
        doc = yaml.safe_load(text) or {}
        entries = doc.get("contrasts") if isinstance(doc, dict) else None
        out: dict[str, dict[str, str]] = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            fields = dict(blank)
            comparison = entry.get("comparison")
            if isinstance(comparison, list) and len(comparison) == 3:
                fields.update(zip(("variable", "reference", "target"),
                                  (str(v).strip() for v in comparison)))
                blocking = entry.get("blocking_factors") or []
                if isinstance(blocking, list):
                    fields["blocking"] = ";".join(str(b).strip() for b in blocking)
            out[str(entry["id"]).strip()] = fields
        return out
    delimiter = "\t" if name.endswith(".tsv") else ","
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    if not rows or not {"id", "variable", "reference", "target"} <= set(rows[0]):
        return {}
    return {
        (row.get("id") or "").strip(): {
            field: (row.get(field) or "").strip() for field in _CONTRAST_FIELDS}
        for row in rows if (row.get("id") or "").strip()
    }


def parse_general_stats(text: str) -> dict[str, dict[str, float | str]]:
    """multiqc_general_stats.txt -> {sample: {column: value}}.

    Column names are kept EXACTLY as MultiQC spelled them. They vary by which
    modules ran and by MultiQC version, and normalising them here would be
    meaning-assignment -- the mapper's job, not this one's. A blank cell is
    omitted rather than stored as an empty string, so "the tool did not run"
    and "the tool reported nothing" stay distinguishable downstream.

    Rows are keyed by whatever MultiQC put in the Sample column, including
    the per-read FastQC rows (`<sample>_1`, `<sample>_2`) that sit alongside
    a biological sample's own row and populate only the fastqc columns --
    those are real rows and are returned as-is; deciding what to do with them
    is a later stage's job.
    """
    rows = csv.DictReader(io.StringIO(text), delimiter="\t")
    out: dict[str, dict[str, float | str]] = {}
    for row in rows:
        sample = (row.get("Sample") or "").strip()
        if not sample:
            continue
        values: dict[str, float | str] = {}
        for column, cell in row.items():
            if column == "Sample" or cell is None:
                continue
            cell = cell.strip()
            if not cell:
                continue
            try:
                values[column] = float(cell)
            except ValueError:
                values[column] = cell
        out[sample] = values
    return out


def parse_execution_trace(text: str) -> dict:
    """execution_trace.txt -> {processes, failed, non_terminal, recovered,
    recovered_tasks}.

    `non_terminal` is what distinguishes a finished run from one still going:
    a process in SUBMITTED or RUNNING means the run has not completed.

    A retried task leaves one row per try under the same `name`, written as
    each try finishes, so a failure followed by a later success of that name
    was retried until it passed. `failed` counts only the failed rows of a
    name whose LAST row failed; the rest are `recovered`, and their names are
    `recovered_tasks`. Two different inputs can share a name tag, so this is
    a best guess: Nextflow's own report verdict, where there is one, outranks
    it (see harvest._apply_run_verdict). A trace with no `name` column
    counts every failed row.
    """
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    statuses = [(r.get("status") or "").strip().upper() for r in rows]
    names = [(r.get("name") or "").strip() for r in rows]
    last_status: dict[str, str] = {}
    for name, status in zip(names, statuses):
        if name:
            last_status[name] = status
    failed = recovered = 0
    recovered_tasks: list[str] = []
    for name, status in zip(names, statuses):
        if status not in _FAILED:
            continue
        if name and last_status[name] in _SUCCEEDED:
            recovered += 1
            if name not in recovered_tasks:
                recovered_tasks.append(name)
        else:
            failed += 1
    return {
        "processes": len(statuses),
        "failed": failed,
        "non_terminal": sum(1 for s in statuses if s and s not in _TERMINAL),
        "recovered": recovered,
        "recovered_tasks": recovered_tasks,
    }


# Nextflow's three completion sentences, from its ReportTemplate.html. The
# middle one is `workflow.success && workflow.stats.ignoredCount`: a task
# failed and the pipeline carried on without it (errorStrategy 'ignore').
# Retries never produce it; a run whose failures were all retried until they
# passed reads "completed successfully!".
RUN_SUCCEEDED = "succeeded"
RUN_SUCCEEDED_IGNORING_ERRORS = "succeeded_ignoring_errors"
RUN_FAILED = "failed"
_VERDICTS = (
    ("Workflow execution completed successfully. One or more tasks reported an error condition.",
     RUN_SUCCEEDED_IGNORING_ERRORS),
    ("Workflow execution completed successfully!", RUN_SUCCEEDED),
    ("Workflow execution completed unsuccessfully!", RUN_FAILED),
)


def classify_run_verdict(sentence: str) -> str | None:
    """A verdict line from Nextflow's execution report -> one of RUN_SUCCEEDED,
    RUN_SUCCEEDED_IGNORING_ERRORS, RUN_FAILED, or None if it is none of them."""
    text = " ".join(sentence.split())
    for prefix, kind in _VERDICTS:
        if text.startswith(prefix):
            return kind
    return None
