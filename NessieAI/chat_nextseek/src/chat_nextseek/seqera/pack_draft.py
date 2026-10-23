"""Draft a pipeline's Nessie files from nf-core's own pinned schemas and nf-test snapshots.

A drafting aid, never a publisher: every function returns plain dicts for a person to edit
into seqera/catalog.py, reports/templates/nfcore/<key>.json and
NessieAI/ns/reingest_maps/<key>.outputs.json. Network access goes only through the injected
`fetch`, so tests run on committed fixtures.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from chat_nextseek.seqera.nfcore_schema import RAW_BASE, get_schema
from chat_nextseek.seqera.schema_check import check_params, schema_properties

Fetch = Callable[[str], bytes]

TREE_URL = "https://api.github.com/repos/nf-core/{pipeline}/git/trees/{revision}?recursive=1"
_SNAPSHOT = re.compile(r"^tests/[^/]+\.nf\.test\.snap$")
_REFERENCE_FLAGS = ("genome", "fasta", "gtf")
_SKIP_SECTIONS = {"input_output_options", "institutional_config_options", "generic_options"}
_MENU_TYPE = {"boolean": "bool", "string": "string", "integer": "int", "number": "number"}
# A literal file extension in a pattern (\.gtf, \.(bed|...)) with no gz alternative.
_EXTENSION = re.compile(r"\\\.\(?[a-z]")
_REMOTE = ("s3://", "gs://", "http://", "https://")


def _sections(nextflow_schema: dict) -> dict:
    return nextflow_schema.get("$defs") or nextflow_schema.get("definitions") or {}


def _input_kind(columns: list[str]) -> str:
    for column, kind in (("fastq_1", "fastq"), ("bam", "bam"), ("cram", "cram")):
        if column in columns:
            return kind
    return "other"


def draft_catalog_row(pipeline: str, revision: str, nextflow_schema: dict,
                      input_schema: dict) -> dict[str, Any]:
    """The machine-readable half of a catalog entry. common_assays, accepted_assay_patterns
    and pipeline_kind_description are a person's to write."""
    props = schema_properties(nextflow_schema)
    items = (input_schema or {}).get("items") or {}
    columns = list(items.get("properties") or {})
    return {
        "repo": f"https://github.com/nf-core/{pipeline}",
        "default_revision": revision,
        "description": nextflow_schema.get("description", ""),
        "reference_cli_flags": [f for f in _REFERENCE_FLAGS if f in props],
        "required_columns": list(items.get("required") or []),
        "samplesheet_input_kind": _input_kind(columns),
        "accepted_leaf_sample_types": ["D.SEQ"],
    }


def draft_params_menu(nextflow_schema: dict) -> dict[str, dict]:
    """Every visible param outside the I/O and institutional sections, in the curated
    menu's shape. Reference params are left out: the launcher sets them."""
    menu: dict[str, dict] = {}
    for section, body in _sections(nextflow_schema).items():
        if section in _SKIP_SECTIONS:
            continue
        for name, spec in (body.get("properties") or {}).items():
            if spec.get("hidden") or name in _REFERENCE_FLAGS or name.startswith("igenomes_"):
                continue
            entry = {"type": "enum" if spec.get("enum") else _MENU_TYPE.get(spec.get("type"), "string"),
                     "default": spec.get("default"), "steerable": True,
                     "help": spec.get("description", "")}
            if spec.get("enum"):
                entry["allowed"] = list(spec["enum"])
            menu[name] = entry
    return menu


def draft_samplesheet_columns(input_schema: dict) -> dict[str, Any]:
    items = (input_schema or {}).get("items") or {}
    required = list(items.get("required") or [])
    return {"required": required,
            "optional": [c for c in (items.get("properties") or {}) if c not in required],
            "conditional": {k: items[k] for k in ("dependentRequired", "anyOf", "oneOf") if k in items}}


def trap_report(nextflow_schema: dict, input_schema: dict) -> list[str]:
    """The pinned-schema traps the RNA work hit late, as one line each."""
    traps: list[str] = []
    for name, spec in sorted(schema_properties(nextflow_schema).items()):
        pattern = spec.get("pattern") or ""
        if pattern and _EXTENSION.search(pattern) and "gz" not in pattern:
            traps.append(f"--{name}: pattern {pattern} rejects a gzipped file; stage the plain one")
        if name.startswith("skip_") and spec.get("type") == "boolean":
            traps.append(f"--{name}: read what it gates in the pipeline's .nf source before relying on it")
        default = spec.get("default")
        if isinstance(default, str) and default.startswith(_REMOTE):
            traps.append(f"--{name}: defaults to {default}; a run may download it")
    columns = (((input_schema or {}).get("items") or {}).get("properties") or {})
    for column, spec in sorted(columns.items()):
        pattern = spec.get("pattern") or ""
        if pattern and _EXTENSION.search(pattern) and "gz" not in pattern:
            traps.append(f"samplesheet {column}: pattern {pattern} rejects a gzipped file")
    return traps


def _is_file_path(entry: str) -> bool:
    """A snapshot entry that names a file a run writes: no whitespace (log lines), no ':'
    (md5 pairs, 'WARN: ...'), not absolute, and either nested or carrying an extension."""
    if not entry or any(c.isspace() for c in entry) or ":" in entry or entry.startswith("/"):
        return False
    return "/" in entry or "." in entry


def snapshot_paths(snapshot: dict, test_name: str | None = None) -> list[str]:
    """Output files a test run writes, read from an nf-test .snap: the content lists whose
    entries are relative paths (not 'name:md5,...'), without the folder entries.

    Two rules keep the list to what a real run writes. An entry counts only if it looks like
    a file path (see _is_file_path), so log lines and bare folders such as 'reference' drop
    out. And with no `test_name`, every test whose name contains '-stub' is skipped, because
    a stub run writes placeholder names no real run does; an explicit `test_name` selects
    exactly that test, stub or not."""
    paths: set[str] = set()
    for name, test in snapshot.items():
        if test_name is None and "-stub" in name:
            continue
        if test_name is not None and name != test_name:
            continue
        for part in test.get("content") or []:
            if (isinstance(part, list) and part and all(isinstance(p, str) for p in part)
                    and not any(":md5," in p for p in part) and any("/" in p for p in part)):
                paths.update(p for p in part if _is_file_path(p))
    return sorted(p for p in paths if not any(q.startswith(p + "/") for q in paths))


def draft_map_skeleton(pipeline: str, revision: str, paths: list[str]) -> dict[str, Any]:
    """A v2 map with no rules yet. `_files_by_folder` is reading matter for whoever writes
    the rules; it is not a PipelineMap field, so delete it before committing a map."""
    parts = revision.split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError(f"cannot pin a minor release from revision {revision!r}: "
                         "its first two dot parts must be numeric (e.g. 3.10.0)")
    major, minor = parts[0], parts[1]
    by_folder: dict[str, list[str]] = {}
    for path in paths:
        by_folder.setdefault(path.split("/", 1)[0], []).append(path)
    return {"map_version": 2, "pipeline": f"nf-core/{pipeline}",
            "applies_to_versions": f">={major}.{minor},<{major}.{int(minor) + 1}",
            "accepts_parent_types": ["D.SEQ"], "outputs": [], "deliberately_unmapped": [],
            "_files_by_folder": by_folder}


def list_snapshots(pipeline: str, revision: str, fetch: Fetch) -> list[str]:
    tree = json.loads(fetch(TREE_URL.format(pipeline=pipeline, revision=revision)))
    return sorted(e["path"] for e in tree.get("tree") or [] if _SNAPSHOT.match(e.get("path", "")))


def draft(pipeline: str, revision: str, fetch: Fetch) -> dict[str, Any]:
    """Everything above for one pinned release, plus the schema check of its own defaults."""
    schema = get_schema(pipeline, revision, fetcher=fetch)
    nextflow_schema = schema["nextflow_schema"]
    input_schema = schema.get("input_schema") or {}
    snapshots = {path: json.loads(fetch(f"{RAW_BASE}/{pipeline}/{revision}/{path}"))
                 for path in list_snapshots(pipeline, revision, fetch)}
    paths = sorted({p for snap in snapshots.values() for p in snapshot_paths(snap)})
    menu = draft_params_menu(nextflow_schema)
    defaults = {k: v["default"] for k, v in menu.items() if v.get("default") is not None}
    errors, skipped = check_params(pipeline, revision, defaults,
                                   schema_getter=lambda _p, _r: schema)
    return {"catalog_row": draft_catalog_row(pipeline, revision, nextflow_schema, input_schema),
            "params_menu": menu,
            "samplesheet_columns": draft_samplesheet_columns(input_schema),
            "map_skeleton": draft_map_skeleton(pipeline, revision, paths),
            "snapshots": sorted(snapshots),
            "traps": trap_report(nextflow_schema, input_schema),
            "default_check_errors": errors,
            "default_check_skipped": skipped}
