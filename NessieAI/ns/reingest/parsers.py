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
    """software_versions.yml -> {tool: version}.

    nf-core nests one level (process -> {tool: version}); the Workflow block
    is shaped the same way (nf-core/rnaseq and Nextflow nested under
    "Workflow" in the 3.22 fixture, not flat at the top level). Both flatten
    through the same branch, later keys winning, which is harmless because a
    tool reports one version per run.
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
