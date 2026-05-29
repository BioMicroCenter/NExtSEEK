"""Tools + dispatch for the full-agentic nf-core pipeline agent.

Four anthropic-style tools driven by BedrockClient.chat_with_tools:
  - resolve_samples:   UIDs/last-search -> compact leaf table (+ caches refs)
  - write_samplesheet: agent-built cohorts -> validated samplesheet + launch.yml
  - submit_to_tower:   submit the built launch artifacts
  - conclude:          terminate the conversation (control tool, intercepted by the loop)
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ChatConfig

from ..helpers import (
    annotate_metadata_with_sampletypes,
    build_metadata_summary,
    enumerate_lineage_leaves,
    fetch_reporter_metadata,
    filter_summary_to_sequencing_lineage,
    uids_from_last_search,
)
from pathlib import Path

from ..seqera.catalog import NFCORE_PIPELINE_CATALOG
from ..seqera.emitter import emit_nfcore_artifacts, write_combined_launch_yml
from ..seqera.ena import extract_accessions_from_metadata, resolve_accessions

PIPELINE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "resolve_samples",
        "description": (
            "Resolve a sample reference into a per-leaf metadata table. Call this FIRST. "
            "ref.kind is 'last_search' (the user's most recent search results), "
            "'explicit_uids' (uids you were given), or 'accessions' (raw GEO/ENA accessions "
            "for fetchngs). Returns each sequencing leaf with its uid, sample_type, assay, "
            "any accessions, and the grouping-candidate fields with their distinct values. "
            "Use the returned fields+values to map a group-by phrase to a real field. "
            "Pass the pipeline_key you intend to run so the right leaf sample types are eligible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["last_search", "explicit_uids", "accessions"]},
                "uids": {"type": "array", "items": {"type": "string"},
                         "description": "Required when kind='explicit_uids'."},
                "accessions": {"type": "array", "items": {"type": "string"},
                               "description": "Required when kind='accessions'."},
                "pipeline_key": {"type": "string",
                                 "description": "the pipeline you intend to run; determines which leaf sample types are eligible."},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "write_samplesheet",
        "description": (
            "Build the nf-core samplesheet(s) and launch artifacts from cohorts YOU assemble. "
            "Each cohort is one pipeline run. Put each sample in exactly one cohort. For a "
            "group-by, make one cohort per distinct field value; for a filter, make one cohort "
            "of the matching samples. Every row's 'sample' and (if present) 'accession' MUST "
            "come from a resolve_samples result — invented refs are rejected and returned to you "
            "to fix. Leave fastq_1/fastq_2 empty; the ENA layer fills them from accessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_key": {"type": "string",
                                 "description": "Catalog key, e.g. 'rnaseq', 'scrnaseq', 'fetchngs'."},
                "cohorts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "kebab-case cohort label, unique."},
                            "rows": {
                                "type": "array",
                                "items": {"type": "object", "description": "samplesheet row; keys are column names."},
                            },
                        },
                        "required": ["label", "rows"],
                    },
                },
            },
            "required": ["pipeline_key", "cohorts"],
        },
    },
    {
        "name": "submit_to_tower",
        "description": (
            "Submit the most recently built launch artifacts to Seqera Tower. Only call this "
            "AFTER the user has confirmed they want to submit. If Tower is not configured this "
            "returns the samplesheet path instead of submitting."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "conclude",
        "description": (
            "End the conversation. Call this when the task is fully done: after a successful "
            "submit (outcome='submitted'), after answering a standalone question (outcome='answered'), "
            "when the request can't be done (outcome='rejected'), or on user cancel (outcome='cancelled'). "
            "Do NOT call conclude when you are pausing to ask the user something — just write your "
            "question as plain text and stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["submitted", "rejected", "cancelled", "answered"]},
                "message": {"type": "string", "description": "final user-facing message."},
            },
            "required": ["outcome", "message"],
        },
    },
]


def _accepted_types_for(pipeline_key: str) -> list[str]:
    return list(NFCORE_PIPELINE_CATALOG.get(pipeline_key, {}).get("accepted_leaf_sample_types") or [])


def tool_resolve_samples(config: "ChatConfig", session, state: dict, tool_input: dict, pipeline_key: str) -> str:
    """Resolve a sample ref into a compact leaf table; cache ground-truth refs in state['resolved']."""
    kind = tool_input.get("kind")

    if kind == "accessions":
        accs = [a.strip() for a in (tool_input.get("accessions") or []) if a and a.strip()]
        if not accs:
            return json.dumps({"ok": False, "error": "kind='accessions' requires a non-empty accessions list."})
        state.setdefault("resolved", {"uids": [], "accessions": []})
        state["resolved"]["accessions"] = sorted(set(state["resolved"].get("accessions", [])) | set(accs))
        return json.dumps({"ok": True, "kind": "accessions", "accessions": accs, "leaf_count": 0, "leaves": []})

    if kind == "last_search":
        source_uids = uids_from_last_search(session)
        if not source_uids:
            return json.dumps({"ok": False, "error": "No pinned search to use. Run a search first or pass explicit UIDs."})
    elif kind == "explicit_uids":
        source_uids = [u for u in (tool_input.get("uids") or []) if u]
        if not source_uids:
            return json.dumps({"ok": False, "error": "kind='explicit_uids' requires a non-empty uids list."})
    else:
        return json.dumps({"ok": False, "error": f"Unknown ref kind {kind!r}."})

    raw = fetch_reporter_metadata(config, source_uids)
    if not raw.get("ok"):
        return json.dumps({"ok": False, "error": f"Metadata fetch failed: {raw.get('error') or 'unknown error'}"})

    annotated = annotate_metadata_with_sampletypes(config, raw)
    leaves = enumerate_lineage_leaves(annotated, accepted_types=_accepted_types_for(pipeline_key))

    table: list[dict] = []
    all_uids: set[str] = set()
    all_accs: set[str] = set()
    for leaf in leaves:
        accs = extract_accessions_from_metadata(leaf.get("metadata") or {})
        all_uids.add(leaf["uid"])
        all_accs.update(accs)
        table.append({
            "uid": leaf["uid"],
            "sample_type": leaf.get("sample_type", ""),
            "assay": leaf.get("assay", ""),
            "source_uid": leaf.get("source_uid", ""),
            "accessions": accs,
        })

    try:
        summary = filter_summary_to_sequencing_lineage(build_metadata_summary({"__sample__": annotated}))
        grouping_fields = {
            st: {f: fd.get("examples", []) for f, fd in (data.get("fields") or {}).items()}
            for st, data in (summary.get("by_sample_type") or {}).items()
        }
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_AGENT] summary build failed: {exc!r}")
        grouping_fields = {}

    seen_sources = {leaf.get("source_uid") for leaf in leaves}
    orphans = [u for u in source_uids if u not in seen_sources]

    prev = state.get("resolved") or {"uids": [], "accessions": []}
    state["resolved"] = {
        "uids": sorted(set(prev.get("uids") or []) | all_uids),
        "accessions": sorted(set(prev.get("accessions") or []) | all_accs),
    }
    return json.dumps({
        "ok": True,
        "kind": kind,
        "leaf_count": len(table),
        "leaves": table,
        "grouping_fields": grouping_fields,
        "source_uids_with_no_leaves": orphans,
    })


_REF_KEYS = ("sample", "Sample")
_ACC_KEYS = ("accession", "Accession", "ena_accession")


def _validate_rows_against_resolved(cohorts: list, resolved: dict) -> list[str]:
    """Reject any row whose sample/accession the agent did not get from resolve_samples.

    A row is acceptable if its sample id is a resolved uid OR it carries a resolved
    accession. Separately, any accession present must itself be resolved. When the
    resolved set for a dimension is empty (e.g. a pure-accession fetchngs flow with no
    uids), that dimension is not enforced.
    """
    ok_uids = set(resolved.get("uids") or [])
    ok_accs = set(resolved.get("accessions") or [])
    errors: list[str] = []
    for cohort in cohorts:
        label = cohort.get("label", "?")
        for i, row in enumerate(cohort.get("rows") or []):
            sample = next((row[k] for k in _REF_KEYS if row.get(k)), None)
            acc = next((row[k] for k in _ACC_KEYS if row.get(k)), None)
            sample_ok = (not sample) or (not ok_uids) or (sample in ok_uids) or (acc in ok_accs)
            acc_ok = (not acc) or (not ok_accs) or (acc in ok_accs)
            if not sample_ok:
                errors.append(f"cohort {label!r} row {i}: sample {sample!r} not in resolved samples.")
            if not acc_ok:
                errors.append(f"cohort {label!r} row {i}: accession {acc!r} not in resolved metadata.")
    return errors


def tool_write_samplesheet(config: "ChatConfig", state: dict, tool_input: dict, log_dir: str) -> str:
    """Validate agent-built cohorts against resolved refs, then emit samplesheet(s) + launch.yml."""
    pipeline_key = tool_input.get("pipeline_key") or ""
    cohorts = tool_input.get("cohorts") or []
    if pipeline_key not in NFCORE_PIPELINE_CATALOG:
        return json.dumps({"ok": False, "errors": [f"Unknown pipeline {pipeline_key!r}."]})
    if not cohorts:
        return json.dumps({"ok": False, "errors": ["No cohorts provided."]})

    resolved = state.get("resolved") or {"uids": [], "accessions": []}
    errors = _validate_rows_against_resolved(cohorts, resolved)
    if errors:
        return json.dumps({"ok": False, "errors": errors})

    tower_env = dict(getattr(config, "TOWER_ENV", {}) or {})
    base = Path(log_dir or getattr(config, "LOG_DIR", ".")) / (
        "nfcore_multi" if len(cohorts) > 1 else f"nfcore_{cohorts[0].get('label', pipeline_key)}")
    base.mkdir(parents=True, exist_ok=True)
    multi = len(cohorts) > 1

    cohort_summaries: list[dict] = []
    launch_entries: list[dict] = []
    state.setdefault("artifacts", {})
    state["artifacts"]["cohorts"] = []

    for cohort in cohorts:
        label = cohort.get("label") or pipeline_key
        rows = cohort.get("rows") or []
        accs = [r[k] for r in rows for k in _ACC_KEYS if r.get(k)]
        resolutions = resolve_accessions(accs) if accs else []
        cohort_dir = (base / label) if multi else base
        cohort_dir.mkdir(parents=True, exist_ok=True)
        result = emit_nfcore_artifacts(
            cohort_dir,
            pipeline=pipeline_key,
            samplesheet_rows=rows,
            resolutions=resolutions,
            launch_plan={"run_name": label} if tower_env.get("access_token") else None,
            tower_env=tower_env,
            selector_rationale="full-agentic pipeline_agent build",
            samplesheet_relative_dir=(label if multi else "."),
            write_launch_yml=not multi,
        )
        cohort_summaries.append({"label": label, "row_count": result.samplesheet_row_count})
        state["artifacts"]["cohorts"].append(result.saved_files)
        if result.launch_entry:
            launch_entries.append(result.launch_entry)
        if result.fetchngs_launch_entry:
            launch_entries.append(result.fetchngs_launch_entry)

    launch_path = None
    if multi and launch_entries:
        launch_path = write_combined_launch_yml(base, launch_entries)
    elif not multi:
        launch_path = (state["artifacts"]["cohorts"][0] or {}).get("launch")
    state["artifacts"]["launch"] = launch_path
    state["artifacts"]["base_dir"] = str(base)

    return json.dumps({
        "ok": True,
        "pipeline_key": pipeline_key,
        "cohort_count": len(cohorts),
        "cohorts": cohort_summaries,
        "launch_yml": launch_path,
    })
