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


def parse_params(text: str) -> dict:
    """pipeline_info/params*.json -> every resolved param, verbatim."""
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


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
                flat[str(tool)] = str(version)
        else:
            flat[str(key)] = str(value)
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
        key = str(key)
        if isinstance(value, dict):
            by_process[key] = {str(tool): str(version) for tool, version in value.items()}
        else:
            by_process[key] = {key: str(value)}
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


def parse_execution_trace(text: str) -> dict:
    """execution_trace.txt -> {processes, failed, non_terminal}.

    `non_terminal` is what distinguishes a finished run from one still going:
    a process in SUBMITTED or RUNNING means the run has not completed.
    """
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    statuses = [(r.get("status") or "").strip().upper() for r in rows]
    return {
        "processes": len(statuses),
        "failed": sum(1 for s in statuses if s in _FAILED),
        "non_terminal": sum(1 for s in statuses if s and s not in _TERMINAL),
    }
