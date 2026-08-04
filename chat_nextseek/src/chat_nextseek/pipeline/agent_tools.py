"""Tools + dispatch for the full-agentic nf-core pipeline agent.

Anthropic-style tools driven by BedrockClient.chat_with_tools (submit tools exposed per config):
  - resolve_samples:   UIDs/last-search -> compact leaf table (+ caches refs)
  - write_samplesheet: agent-built cohorts -> validated samplesheet CSV (CSV only)
  - configure_run:     curated params + species references -> params.yml + launch.yml
  - submit_to_luria:   submit the built run to MIT's Luria SLURM cluster
  - submit_to_luria:   submit the built launch artifacts to the Luria SLURM cluster
  - conclude:          terminate the conversation (control tool, intercepted by the loop)
"""
from __future__ import annotations

import json
import re
from collections import Counter
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

from ..schemas import SeqeraLaunchPlan
from ..seqera.catalog import NFCORE_PIPELINE_CATALOG
from ..seqera.emitter import emit_launch_artifacts, emit_luria_launch_artifacts, emit_nfcore_artifacts
from ..seqera.ena import extract_accessions_from_metadata, resolve_accessions
from ..seqera.pipeline_params import (
    build_run_params,
    gencode_for_genome_key,
    load_pipeline_context,
    load_reference_bundles,
    process_args_for,
    resolve_bundle_for_species,
)
from ..seqera.submitter import submit_launch
from ..seqera.user_params import (
    missing_user_params,
    render_elicitation,
    validate_user_params,
)
from ..luria.submitter import submit_luria
from ..luria.run_script import local_luria_ref_files

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
            "to fix. Leave fastq_1/fastq_2 empty for SRR samples; local /net/bmc-* paths are "
            "filled from metadata and SRR accessions are fetched on-cluster (nf-core/fetchngs)."
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
        "name": "configure_run",
        "description": (
            "Build the Luria run config (params.yml + launch.yml) for the run. Call AFTER "
            "write_samplesheet. Set pipeline params from the param_menu returned by resolve_samples; "
            "genome/reference defaults come from the samples' detected species. Returns the resolved "
            "params + reference_status so you can show the user and let them steer; re-call to change "
            "params. Does NOT submit. If a param is rejected, fix it and call again."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_key": {"type": "string", "description": "Catalog key, e.g. 'rnaseq', 'scrnaseq'."},
                "params": {"type": "object",
                           "description": "param -> value (subset of the curated menu). May include 'genome' "
                                          "or explicit reference paths to steer references."},
                "revision": {"type": "string", "description": "pipeline revision override (-> launch.yml)."},
                "profile": {"type": "string", "description": "docker|singularity|conda (-> launch.yml)."},
            },
            "required": ["pipeline_key"],
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
    {
        "name": "handoff",
        "description": (
            "Give the turn back to the main NExtSEEK assistant because the user's message "
            "is NOT about building, configuring or launching a pipeline. Call this for "
            "anything else they ask while a pipeline build happens to be open — a sample "
            "search, a lineage or study question, a report, an unrelated question. Do not "
            "answer it yourself and do not tell them you cannot search; just hand off and "
            "the right agent will take it. Your build state is discarded, so only hand off "
            "when they have genuinely moved on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "one short phrase: what the user actually asked for.",
                },
            },
            "required": ["reason"],
        },
    },
]

SUBMIT_TO_LURIA_SCHEMA: dict[str, Any] = {
    "name": "submit_to_luria",
    "description": (
        "Submit the most recently built launch artifacts to MIT's Luria SLURM "
        "cluster (ssh + sbatch a generated run.sh wrapping `nextflow run`). Only "
        "call this AFTER the user has confirmed they want to submit. You may set "
        "SLURM resources when the user asks for them; otherwise defaults are used. "
        "If Luria is not configured this returns the samplesheet path instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "job_name": {"type": "string", "description": "optional SLURM job name."},
            "resources": {
                "type": "object",
                "description": "optional SLURM overrides; invalid values fall back to defaults.",
                "properties": {
                    "partition": {"type": "string"},
                    "time": {"type": "string", "description": "HH:MM:SS"},
                    "cpus": {"type": "integer"},
                    "mem": {"type": "string", "description": "e.g. 8G"},
                },
            },
        },
        "required": [],
    },
}

_SCHEMA_BY_NAME = {t["name"]: t for t in PIPELINE_TOOL_SCHEMAS}


def build_pipeline_tool_schemas(config) -> list[dict[str, Any]]:
    """Expose only the submit tools whose backend env is complete (core + conclude always)."""
    tools = [
        _SCHEMA_BY_NAME["resolve_samples"],
        _SCHEMA_BY_NAME["write_samplesheet"],
        _SCHEMA_BY_NAME["configure_run"],
    ]
    # Tower/Seqera retired: Luria is the only exposed launch target. tool_submit_to_tower
    # and its schema stay in place (dormant) for a future re-enable.
    if getattr(config, "LURIA_ENV_COMPLETE", False):
        tools.append(SUBMIT_TO_LURIA_SCHEMA)
    tools.append(_SCHEMA_BY_NAME["conclude"])
    # Always available: an open build must never be able to trap the conversation.
    tools.append(_SCHEMA_BY_NAME["handoff"])
    return tools


def _accepted_types_for(pipeline_key: str) -> list[str]:
    return list(NFCORE_PIPELINE_CATALOG.get(pipeline_key, {}).get("accepted_leaf_sample_types") or [])


_ARCHIVE_ACCESSION_RE = re.compile(
    r"^(?:SRR|SRX|SRP|SRS|ERR|ERX|ERP|ERS|DRR|DRX|DRP|DRS|GSE|GSM|PRJ[A-Z]+)\d+$", re.I)

# Cap on how many sequencing leaves an interactive build will ingest. Beyond this,
# the per-leaf metadata table would blow the model's context window, so we ask the
# user to narrow the set instead (matches the "be specific / start from D.SEQ" flow).
MAX_RESOLVE_LEAVES = 75


def _flatten_lineage(uid: str, uid_index: dict) -> dict:
    """Merge metadata from root down to the leaf (leaf wins) via the parent chain."""
    chain, seen, cur = [], set(), uid
    while cur and cur in uid_index and cur not in seen:
        seen.add(cur)
        chain.append(uid_index[cur].get("metadata") or {})
        cur = uid_index[cur].get("parent_uid")
    merged: dict = {}
    for md in reversed(chain):  # root first, leaf last so the leaf overrides ancestors
        for k, v in md.items():
            if isinstance(v, (str, int, float, bool)) and v not in (None, ""):
                merged[k] = v
    return merged


def tool_resolve_samples(config: "ChatConfig", session, state: dict, tool_input: dict, pipeline_key: str) -> str:
    """Resolve a sample ref into a compact leaf table; cache ground-truth refs in state['resolved']."""
    kind = tool_input.get("kind")

    if kind == "accessions":
        accs = [a.strip() for a in (tool_input.get("accessions") or []) if a and a.strip()]
        if not accs:
            return json.dumps({"ok": False, "error": "kind='accessions' requires a non-empty accessions list."})
        if not any(_ARCHIVE_ACCESSION_RE.match(a) for a in accs):
            return json.dumps({"ok": False, "error": (
                "Those look like NExtSEEK sample UIDs, not raw archive accessions. "
                "Call resolve_samples again with kind='explicit_uids' and put them in 'uids'. "
                "(kind='accessions' is only for raw SRA/ENA/GEO IDs like SRR.../ERR.../GSE... used with fetchngs.)")})
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

    if len(leaves) > MAX_RESOLVE_LEAVES:
        # Building the per-leaf table for this many leaves would overflow the model's
        # context. Stop here and ask the user to narrow rather than crash mid-build.
        return json.dumps({
            "ok": False,
            "leaf_count": len(leaves),
            "error": (
                f"Resolved {len(leaves)} sequencing samples — more than the "
                f"{MAX_RESOLVE_LEAVES}-sample limit for an interactive build. Ask the user to "
                "narrow the set: specific D.SEQ UIDs, a tighter search, or a filter (e.g. one "
                "study, lab, or treatment). Large cohorts can't be assembled in a single pass yet."
            ),
        })

    # Grouping-candidate fields + per-leaf lineage-flattened values, so the agent can both
    # pick a grouping field AND assign each leaf to a cohort by that field's value.
    uid_index: dict = {}
    grouping_fields: dict = {}
    try:
        summary = filter_summary_to_sequencing_lineage(build_metadata_summary({"__sample__": annotated}))
        uid_index = summary.get("_uid_index") or {}
        grouping_fields = {
            st: {f: fd.get("examples", []) for f, fd in (data.get("fields") or {}).items()}
            for st, data in (summary.get("by_sample_type") or {}).items()
        }
    except Exception as exc:  # advisory; never block resolution
        print(f"[DEBUG][PIPELINE_AGENT] summary build failed: {exc!r}")

    candidate_fields = {f for fields in grouping_fields.values() for f in fields}

    table: list[dict] = []
    all_uids: set[str] = set()
    all_accs: set[str] = set()
    species_votes: Counter = Counter()
    file_paths_by_acc: dict[str, dict] = {}
    for leaf in leaves:
        accs = extract_accessions_from_metadata(leaf.get("metadata") or {})
        all_uids.add(leaf["uid"])
        all_accs.update(accs)
        flat = _flatten_lineage(leaf["uid"], uid_index) if uid_index else (leaf.get("metadata") or {})
        # Stash the leaf's FULL metadata per accession (the emitter's lookup key) so the emitter
        # can find the fastq paths in whatever fields hold them (Link_PrimaryData / File_* / etc.)
        # by value, not a hardcoded field name. ENA URL stays the fallback when none is found.
        _meta = {**(flat if isinstance(flat, dict) else {}), **(leaf.get("metadata") or {})}
        if _meta:
            # Key by leaf UID so a sample with a local /net/bmc-* path but NO accession
            # still reaches the emitter (the samplesheet 'sample' column is the leaf UID),
            # and also by accession for the (dormant) ENA path.
            file_paths_by_acc[str(leaf["uid"])] = dict(_meta)
            for _a in accs:
                file_paths_by_acc[str(_a).strip()] = dict(_meta)
        # Generically detect species: any flattened value that maps to a reference
        # bundle is a species vote (no hardcoded field name).
        for val in flat.values():
            if isinstance(val, str) and resolve_bundle_for_species(val):
                species_votes[val.strip()] += 1
        leaf_fields = {f: flat[f] for f in candidate_fields if f in flat}
        table.append({
            "uid": leaf["uid"],
            "sample_type": leaf.get("sample_type", ""),
            "assay": leaf.get("assay", ""),
            "source_uid": leaf.get("source_uid", ""),
            "accessions": accs,
            "fields": leaf_fields,
        })

    seen_sources = {leaf.get("source_uid") for leaf in leaves}
    orphans = [u for u in source_uids if u not in seen_sources]

    prev = state.get("resolved") or {"uids": [], "accessions": []}
    state["resolved"] = {
        "uids": sorted(set(prev.get("uids") or []) | all_uids),
        "accessions": sorted(set(prev.get("accessions") or []) | all_accs),
    }
    # Merge curated fastq paths across resolve_samples calls; the emitter reads this in write_samplesheet.
    state["accession_file_paths"] = {**(state.get("accession_file_paths") or {}), **file_paths_by_acc}
    detected_species = species_votes.most_common(1)[0][0] if species_votes else None
    bundle_key = resolve_bundle_for_species(detected_species)
    # Write unconditionally so this resolution's detection (incl. None) replaces any
    # stale value cached by an earlier resolve_samples call in the same session —
    # configure_run reads state["bundle_key"] and must see the latest, not a leftover.
    state["detected_species"] = detected_species
    state["bundle_key"] = bundle_key
    ctx = load_pipeline_context(pipeline_key)
    return json.dumps({
        "ok": True,
        "kind": kind,
        "leaf_count": len(table),
        "leaves": table,
        "grouping_fields": grouping_fields,
        "source_uids_with_no_leaves": orphans,
        "detected_species": detected_species,
        "bundle_key": bundle_key,
        "param_menu": ctx.get("params", {}),
        "reference_resources": ctx.get("reference_resources", []),
    })


_REF_KEYS = ("sample", "Sample")
_ACC_KEYS = ("accession", "Accession", "ena_accession")


def _slugify_label(label: str, fallback: str) -> str:
    """Make an agent-supplied cohort label safe to use as a path component."""
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    return slug or fallback


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
            if not sample and not acc:
                errors.append(f"cohort {label!r} row {i}: row has no sample or accession.")
                continue
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
    grouped = len(cohorts) > 1

    # ONE samplesheet for the whole build. When the agent split the samples into
    # >1 cohort, the cohort label rides along as a 'cohort' metadata COLUMN rather
    # than as separate per-cohort files/runs. nf-core ignores the extra column;
    # downstream differential/contrast steps use it to define the groups.
    merged_rows: list[dict] = []
    cohort_summaries: list[dict] = []
    for idx, cohort in enumerate(cohorts):
        label = cohort.get("label") or f"{pipeline_key}-{idx}"
        rows = cohort.get("rows") or []
        for row in rows:
            r = dict(row)
            if grouped:
                r["cohort"] = label
            merged_rows.append(r)
        cohort_summaries.append({"label": label, "row_count": len(rows)})

    # ENA route retired: Luria resolves fastqs from a local /net/bmc-* path (filled here)
    # or fetches SRR accessions on-cluster (run.sh fetchngs pre-stage). No ENA URL synthesis.
    # resolve_accessions is left imported but unused for a future ENA re-enable.
    resolutions: list = []

    slug = _slugify_label(cohorts[0].get("label", "") if not grouped else pipeline_key, pipeline_key)
    base = Path(log_dir or getattr(config, "LOG_DIR", ".")) / f"nfcore_{slug}"
    base.mkdir(parents=True, exist_ok=True)

    result = emit_nfcore_artifacts(
        base,
        pipeline=pipeline_key,
        samplesheet_rows=merged_rows,
        resolutions=resolutions,
        accession_metadata=state.get("accession_file_paths") or {},
        launch_plan=None,  # configure_run now owns params.yml + launch.yml
        tower_env=tower_env,
        selector_rationale="full-agentic pipeline_agent build",
        samplesheet_relative_dir=".",
        write_launch_yml=True,
    )

    state.setdefault("artifacts", {})
    state["artifacts"]["cohorts"] = [result.saved_files]
    state["artifacts"]["samplesheet"] = result.saved_files.get("samplesheet")
    state["artifacts"]["base_dir"] = str(base)
    state["artifacts"]["excluded_accessions"] = list(getattr(result, "excluded_accessions", []) or [])
    # A (re)built samplesheet invalidates any prior configure_run output, so the agent
    # must call configure_run again before submit — never submit a stale params/launch.
    state["artifacts"].pop("params", None)
    state["artifacts"].pop("launch", None)
    state.pop("launch_plan", None)

    return json.dumps({
        "ok": True,
        "pipeline_key": pipeline_key,
        "samplesheet": result.saved_files.get("samplesheet"),
        "total_rows": result.samplesheet_row_count,
        "grouped_by_cohort": grouped,
        "cohorts": cohort_summaries,
        "excluded_accessions": list(getattr(result, "excluded_accessions", []) or []),
    })


def tool_configure_run(config: "ChatConfig", state: dict, tool_input: dict, log_dir: str) -> str:
    """Assemble params.yml + launch.yml from curated params + species references + user steering."""
    pipeline_key = tool_input.get("pipeline_key") or state.get("pipeline_key") or ""
    if pipeline_key not in NFCORE_PIPELINE_CATALOG:
        return json.dumps({"ok": False, "error": f"Unknown pipeline {pipeline_key!r}."})
    artifacts = state.get("artifacts") or {}
    samplesheet = artifacts.get("samplesheet")
    if not samplesheet:
        return json.dumps({"ok": False, "error": "Build the samplesheet first with write_samplesheet."})

    # A genome override in params can re-select the bundle; else use the detected-species bundle.
    agent_params = dict(tool_input.get("params") or {})

    # Params only the user can supply (a CRISPR guide, a Hi-C digestion protocol, a
    # miRTrace species). Enforced here rather than in the prompt: an instruction can
    # be forgotten mid-conversation, this cannot. Fail-closed because a wrong value
    # of this kind does not error — it silently produces a wrong result.
    bad = validate_user_params(pipeline_key, agent_params)
    if bad:
        return json.dumps({"ok": False, "invalid_user_params": bad,
                           "message": "Ask the user to correct these; do not guess."})
    missing = missing_user_params(pipeline_key, agent_params)
    if missing:
        return json.dumps({"ok": False,
                           "needs_user_input": [s["name"] for s in missing],
                           "ask_the_user": render_elicitation(missing),
                           "message": ("Relay `ask_the_user` to the user in plain text and STOP. "
                                       "Do not call conclude, and do not invent values.")})
    override = agent_params.get("genome")
    bundle_key = state.get("bundle_key")
    if override:
        known_bundles = load_reference_bundles().get("bundles") or {}
        if override in known_bundles:
            bundle_key = override
            agent_params.pop("genome", None)
        elif resolve_bundle_for_species(override):
            bundle_key = resolve_bundle_for_species(override)
            agent_params.pop("genome", None)
        # else: a raw iGenomes key (e.g. "GRCh38") the agent wants verbatim -> leave in agent_params
    # Persist the (possibly steered) bundle so a follow-up configure_run that doesn't
    # re-supply genome keeps the user's chosen reference instead of reverting to the
    # auto-detected one.
    state["bundle_key"] = bundle_key

    merged, errors, reference_status = build_run_params(pipeline_key, agent_params, bundle_key)
    if errors:
        return json.dumps({"ok": False, "errors": errors})

    base = artifacts.get("base_dir") or str(Path(log_dir or getattr(config, "LOG_DIR", ".")))
    plan = SeqeraLaunchPlan(
        run_name=(Path(base).name or pipeline_key),
        params=merged,
        pipeline_revision=tool_input.get("revision"),
        profile=tool_input.get("profile"),
    )
    result = emit_luria_launch_artifacts(
        base, pipeline=pipeline_key, samplesheet_path=samplesheet,
        launch_plan=plan.model_dump())

    state.setdefault("artifacts", {})
    state["artifacts"]["params"] = result.saved_files.get("params")
    state["artifacts"]["launch"] = result.saved_files.get("launch")
    state["launch_plan"] = plan.model_dump()
    state["pipeline_key"] = pipeline_key

    ref_files = (local_luria_ref_files(merged.get("genome"))
                 if reference_status == "local_luria" else None)
    return json.dumps({
        "ok": True,
        "pipeline_key": pipeline_key,
        "resolved_params": merged,
        "reference_status": reference_status,
        "reference_files": ref_files,
        "bundle_key": bundle_key,
        "params_yml": result.saved_files.get("params"),
        "launch_yml": result.saved_files.get("launch"),
    })


def tool_submit_to_tower(config: "ChatConfig", state: dict) -> str:
    artifacts = state.get("artifacts") or {}
    launch = artifacts.get("launch")
    if not launch:
        return json.dumps({"ok": False, "message": "No launch artifact to submit — build a samplesheet first."})
    tower_env = dict(getattr(config, "TOWER_ENV", {}) or {})
    if not (tower_env.get("access_token") and tower_env.get("workspace")):
        return json.dumps({"ok": False, "message": f"Tower not configured. Samplesheet/launch is at {launch}. "
                                                   "Configure TOWER_* env vars or run seqerakit manually."})
    try:
        run_urls = submit_launch(launch, tower_env=tower_env)
    except Exception as exc:
        return json.dumps({"ok": False, "message": f"Submit failed: {exc!r}"})
    if not run_urls:
        return json.dumps({"ok": False, "message": "Tower returned no run URLs — check seqera logs."})
    return json.dumps({"ok": True, "run_urls": run_urls})



def tool_submit_to_luria(config: "ChatConfig", state: dict, tool_input: dict | None = None) -> str:
    artifacts = state.get("artifacts") or {}
    launch = artifacts.get("launch")
    if not launch:
        return json.dumps({"ok": False, "message": "No launch artifact to submit — build a samplesheet first."})
    if not getattr(config, "LURIA_ENV_COMPLETE", False):
        return json.dumps({"ok": False, "message": f"Luria not configured. Samplesheet/launch is at {launch}. "
                                                   "Set LURIA_USER / LURIAKEY / LURIA_WORKING_PATH."})
    luria_env = dict(getattr(config, "LURIA_ENV", {}) or {})
    samplesheet = artifacts.get("samplesheet")
    # Thread the species-resolved iGenomes key (mouse->GRCm39, human->GRCh38) that configure_run
    # computed, so run.sh aligns to the right genome instead of a hardcoded GRCh38. The full
    # merged param set feeds params.yml (per-pipeline curated params); the submitter strips the
    # CLI-owned keys (input/outdir/genome) from it.
    launch_params = dict((state.get("launch_plan") or {}).get("params") or {})
    genome = launch_params.get("genome")
    # Luria runs on local luria.config refs, which are GENCODE for human/mouse (Ensembl for the
    # macaques). Set --gencode from the genome when the pipeline curates a gencode param — the
    # shared build_run_params leaves it at the curated default because that value is Tower-correct
    # (iGenomes, non-GENCODE) but wrong for our local GENCODE refs.
    if "gencode" in launch_params and gencode_for_genome_key(genome):
        launch_params["gencode"] = True
    # Per-protocol process ext.args the curated pipeline JSON declares (e.g. scrnaseq dropseq ->
    # SIMPLEAF_QUANT --knee); the submitter renders these into the run's -c config.
    process_args = process_args_for(state.get("pipeline_key") or "", launch_params.get("protocol"))
    tool_input = tool_input or {}
    try:
        runs = submit_luria(launch, luria_env=luria_env,
                            resources=tool_input.get("resources"),
                            job_name=tool_input.get("job_name"),
                            samplesheet_local=samplesheet,
                            genome=genome,
                            launch_params=launch_params,
                            process_args=process_args)
    except Exception as exc:
        return json.dumps({"ok": False, "message": f"Luria submit failed: {exc!r}"})
    if not runs:
        return json.dumps({"ok": False, "message": "No runs submitted — check Luria logs."})
    state.setdefault("artifacts", {})["luria_runs"] = runs
    return json.dumps({"ok": True, "luria_runs": runs})


def dispatch_pipeline_tool_call(*, config, session, state: dict, name: str, tool_input: dict, log_dir: str) -> str:
    """Route a non-control tool to its implementation. 'conclude' is intercepted by the loop."""
    if name == "resolve_samples":
        pipeline_key = state.get("pipeline_key") or tool_input.get("pipeline_key") or ""
        return tool_resolve_samples(config, session, state, tool_input, pipeline_key)
    if name == "write_samplesheet":
        state["pipeline_key"] = tool_input.get("pipeline_key") or state.get("pipeline_key")
        return tool_write_samplesheet(config, state, tool_input, log_dir)
    if name == "configure_run":
        state["pipeline_key"] = tool_input.get("pipeline_key") or state.get("pipeline_key")
        return tool_configure_run(config, state, tool_input, log_dir)
    if name == "submit_to_tower":
        return tool_submit_to_tower(config, state)
    if name == "submit_to_luria":
        return tool_submit_to_luria(config, state, tool_input)
    if name in ("conclude", "handoff"):
        raise ValueError(f"dispatch_pipeline_tool_call must not be called for {name!r}; the loop intercepts it.")
    raise ValueError(f"Unknown pipeline tool: {name!r}")
