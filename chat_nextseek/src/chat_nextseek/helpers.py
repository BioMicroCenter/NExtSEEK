from __future__ import annotations

import ast
import calendar
import csv
import json
import os
import re
import signal
import html
from copy import copy
from io import BytesIO
from urllib.parse import quote, urlparse
from zipfile import ZipFile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Any, Sequence, Callable

import requests

from .artifacts import (
    ArtifactStore,
    build_saved_report_file_manifest,
)
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .config import ChatConfig
from .schemas import ReportWriterPlan
from .session import SessionState



# TODO (later): replace this mapping with a database-backed context/config table.

# ======================================================
# Reporter / Report Writer helpers
# ======================================================


def persist_report_file(
    label: str,
    payload: dict | list | str | None,
    base_dir: str | Path,
    subdir: str | None = None,
    *,
    kind: str = "report",
    filename: str | None = None,
    mime: str | None = None,
) -> str | None:
    """
    Persist a payload under base_dir (optional subdir) with a .json extension.
    Returns the written path or None on failure.
    """
    if payload is None:
        return None
    try:
        store = ArtifactStore(base_dir)
        out_name = filename or f"{label}.json"
        if isinstance(payload, (dict, list)):
            entry = store.write_json(
                key=label,
                label=label,
                filename=out_name,
                payload=payload,
                kind=kind,
                subdir=subdir,
            )
        else:
            entry = store.write_text(
                key=label,
                label=label,
                filename=out_name,
                payload=str(payload),
                kind=kind,
                subdir=subdir,
                mime=mime,
            )
        return entry["path"] if entry else None
    except Exception as e:
        print(f"[DEBUG][REPORTER] Failed to persist {label}:", repr(e))
        return None


def top_items(d: dict, n: int = 5) -> list[dict[str, Any]]:
    """
    Return the top n key/count pairs from a dict sorted by count descending.
    Provides a compact structure for summaries without mutating the input mapping.
    """
    if not isinstance(d, dict):
        return []
    return [
        {"key": k, "count": v}
        for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]
    ]


def _extract_nfcore_samplesheet_rows(merged_report: dict) -> list[dict[str, Any]]:
    """Pull samplesheet rows out of report_writer outputs (per-UID merged dict).

    Each UID maps to a ReportWriterOutput dump whose `report` body has either a
    `samplesheet` / `samplesheet_rows` / `samplesheet_template` / `rows` /
    `samples` list. We accept the first that's present.
    """
    candidates = (
        "samplesheet", "samplesheet_rows", "samplesheet_template", "rows", "samples",
    )
    rows: list[dict[str, Any]] = []
    if not isinstance(merged_report, dict):
        return rows
    for _, payload in merged_report.items():
        if not isinstance(payload, dict):
            continue
        body = payload.get("report") if isinstance(payload.get("report"), dict) else payload
        if not isinstance(body, dict):
            continue
        for key in candidates:
            section = body.get(key)
            if isinstance(section, list) and section:
                rows.extend(r for r in section if isinstance(r, dict))
                break
    return rows


def _accession_matches_criterion(
    accession: str,
    criterion: dict[str, str] | None,
    accession_metadata: dict[str, dict[str, Any]],
) -> bool:
    """True if the sample metadata for this accession satisfies every key=value
    pair in `criterion` (case-insensitive value compare). Empty criterion = always True."""
    if not criterion:
        return True
    meta = accession_metadata.get(accession) or {}
    for key, expected in criterion.items():
        if not isinstance(key, str):
            return False
        actual = meta.get(key)
        if actual is None:
            return False
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False
    return True


def _handle_nfcore_artifacts(
    *,
    config: ChatConfig,
    user_query: str,
    merged_path: str,
    merged_report: dict,
    nfcore_state: dict[str, Any],
    metadata_map: dict | None = None,
) -> dict[str, Any]:
    """Emit per-cohort artifacts under a parent dir, plus a combined launch.yml.
    Optionally submits via seqerakit. Returns aggregated dict.
    """
    from .agents import seqera_agent  # local import to avoid cycle
    from .seqera import emit_nfcore_artifacts, submit_launch, write_combined_launch_yml

    cohorts: list[dict[str, Any]] = nfcore_state.get("cohorts") or []
    if not cohorts:
        cohorts = [{"label": "rnaseq", "pipeline": "rnaseq", "rationale": "fallback",
                    "enrichment_metadata_fields": [], "cohort_criterion": {}}]

    # Top-level dir name reflects multi-cohort vs single-cohort
    if len(cohorts) == 1:
        parent_dir_name = f"nfcore_{cohorts[0]['label']}"
    else:
        parent_dir_name = "nfcore_multi"
    parent_out_dir = Path(merged_path).parent / parent_dir_name
    parent_out_dir.mkdir(parents=True, exist_ok=True)

    rows = _extract_nfcore_samplesheet_rows(merged_report)
    accession_metadata = build_accession_metadata_lookup(metadata_map or {})
    tower_env = config.TOWER_ENV if config.TOWER_ENV_COMPLETE else {}

    aggregated_saved: dict[str, Any] = {}
    combined_launch: list[dict[str, Any]] = []
    cohort_summaries: list[dict[str, Any]] = []
    skipped_cohorts: list[dict[str, Any]] = []
    multi = len(cohorts) > 1

    for cohort in cohorts:
        label = cohort["label"]
        pipeline = cohort["pipeline"]
        criterion = cohort.get("cohort_criterion") or {}
        enrichment = cohort.get("enrichment_metadata_fields") or []

        # Filter rows whose accession metadata matches this cohort's criterion.
        cohort_rows: list[dict[str, Any]] = []
        for row in rows:
            acc = row.get("accession") or row.get("Accession") or row.get("ena_accession")
            if not acc:
                if not criterion:
                    cohort_rows.append(row)
                continue
            if _accession_matches_criterion(str(acc).strip(), criterion, accession_metadata):
                cohort_rows.append(row)

        # If the LLM under-emitted, fall back to synthesizing rows for every
        # accession that matches the criterion.
        if not cohort_rows:
            for acc in accession_metadata.keys():
                if _accession_matches_criterion(acc, criterion, accession_metadata):
                    meta = accession_metadata[acc]
                    sample_id = (
                        meta.get("Library_ID")
                        or meta.get("Title")
                        or meta.get("UID")
                        or acc
                    )
                    strandedness = (meta.get("Strandedness") or "auto").strip().lower() or "auto"
                    cohort_rows.append({
                        "sample": sample_id,
                        "fastq_1": "",
                        "fastq_2": "",
                        "strandedness": strandedness,
                        "accession": acc,
                    })

        # Skip empty cohorts (no rows after filtering AND no synthesizable rows).
        # This protects against the case where a user-pinned pipeline doesn't
        # match any actual data (e.g., user typed `sarek` but data is RNA-seq).
        if not cohort_rows:
            skipped_cohorts.append({
                "label": label,
                "pipeline": pipeline,
                "criterion": criterion,
                "rationale": cohort.get("rationale") or "",
                "user_pinned": bool(cohort.get("_user_pinned")),
                "reason": "0 rows matched after filtering",
            })
            print(
                f"[DEBUG][REPORTER_NFCORE][{label}] empty cohort — skipping. "
                f"criterion={criterion}, user_pinned={cohort.get('_user_pinned', False)}"
            )
            continue

        # Per-cohort launch plan (params + run name) via the seqera agent.
        launch_plan_dump: dict[str, Any] = {}
        if config.TOWER_ENV_COMPLETE:
            try:
                preview = {
                    "columns": sorted({k for r in cohort_rows[:10] for k in r.keys()}),
                    "first_row": cohort_rows[0] if cohort_rows else {},
                    "row_count": len(cohort_rows),
                    "cohort_label": label,
                    "cohort_criterion": criterion,
                }
                plan_obj = seqera_agent(
                    config=config,
                    user_query=user_query,
                    pipeline=pipeline,
                    samplesheet_preview=preview,
                    reporter_context_summary=nfcore_state.get("reporter_summary") or {},
                )
                launch_plan_dump = plan_obj.model_dump() if hasattr(plan_obj, "model_dump") else dict(plan_obj)
            except Exception as e:
                print(f"[DEBUG][REPORTER_NFCORE][{label}] seqera_agent failed:", repr(e))

        cohort_dir = parent_out_dir / label if multi else parent_out_dir
        rel_dir = label if multi else "."
        cohort_dir.mkdir(parents=True, exist_ok=True)

        emission = emit_nfcore_artifacts(
            cohort_dir,
            pipeline=pipeline,
            samplesheet_rows=cohort_rows,
            resolutions=nfcore_state.get("resolutions") or [],
            launch_plan=launch_plan_dump,
            tower_env=tower_env,
            selector_rationale=cohort.get("rationale") or "",
            enrichment_fields=enrichment,
            accession_metadata=accession_metadata,
            samplesheet_relative_dir=rel_dir,
            write_launch_yml=not multi,  # for multi-cohort we write a combined launch.yml at parent level
        )

        prefix = f"nfcore_{label}_" if multi else "nfcore_"
        for k, v in (emission.saved_files or {}).items():
            aggregated_saved[f"{prefix}{k}"] = v

        if emission.launch_entry:
            combined_launch.append(emission.launch_entry)
        if emission.fetchngs_launch_entry:
            combined_launch.append(emission.fetchngs_launch_entry)

        cohort_summaries.append({
            "label": label,
            "pipeline": pipeline,
            "rationale": cohort.get("rationale") or "",
            "criterion": criterion,
            "enrichment_fields": enrichment,
            "row_count": emission.samplesheet_row_count,
            "excluded_accessions": emission.excluded_accessions,
        })

    # Recompute multi-cohort status after skips
    effective_multi = len(cohort_summaries) > 1

    # Combined launch.yml at parent level when multi-cohort
    combined_launch_path: str | None = None
    if effective_multi and combined_launch:
        combined_launch_path = write_combined_launch_yml(parent_out_dir, combined_launch)
        if combined_launch_path:
            aggregated_saved["nfcore_launch_combined"] = combined_launch_path

    # Top-level cohort summary notes.md
    summary_notes_path = parent_out_dir / "notes.md"
    summary_notes_path.write_text(
        _build_cohort_summary_md(
            cohort_summaries,
            nfcore_state,
            multi=effective_multi,
            skipped_cohorts=skipped_cohorts,
        ),
        encoding="utf-8",
    )
    aggregated_saved["nfcore_summary_notes"] = str(summary_notes_path)

    # Optional auto-submit
    run_urls: list[str] = []
    if config.SEQERA_AUTO_LAUNCH and config.TOWER_ENV_COMPLETE:
        # Prefer combined launch when multi; else the single cohort's launch.yml
        target = combined_launch_path or aggregated_saved.get("nfcore_launch")
        if target:
            try:
                run_urls = submit_launch(target, tower_env=config.TOWER_ENV)
            except Exception as e:
                print("[DEBUG][REPORTER_NFCORE] seqerakit submit failed:", repr(e))

    return {
        "out_dir": str(parent_out_dir),
        "saved_files": aggregated_saved,
        "cohort_summaries": cohort_summaries,
        "run_urls": run_urls,
    }


def _build_cohort_summary_md(
    cohort_summaries: list[dict[str, Any]],
    nfcore_state: dict[str, Any],
    *,
    multi: bool,
    skipped_cohorts: list[dict[str, Any]] | None = None,
) -> str:
    lines = ["# nf-core run summary", ""]

    # Conflict banner: prominently surfaced at the top
    if nfcore_state.get("conflict_detected"):
        pipeline_key = nfcore_state.get("pipeline_key") or "unknown"
        recommended = sorted({
            c.get("pipeline") for c in (nfcore_state.get("cohorts") or [])
            if c.get("pipeline") != pipeline_key and not c.get("_user_pinned")
        })
        lines.append("> ⚠️ **PIPELINE DISAGREEMENT**")
        lines.append(">")
        lines.append(
            f"> You explicitly requested **nf-core/{pipeline_key}**, but the metadata "
            f"indicates **{', '.join(f'nf-core/{p}' for p in recommended) or 'a different pipeline'}** "
            "is more appropriate for this data."
        )
        lines.append(">")
        lines.append(
            "> Both samplesheets are emitted below. Pick the cohort you actually intend "
            "to run, or run both as separate Tower workflows from the top-level `launch.yml`."
        )
        lines.append("")

    if nfcore_state.get("selector_rationale"):
        lines.append(f"**Selector rationale:** {nfcore_state['selector_rationale']}")
        lines.append("")
    lines.append(f"**Cohorts emitted:** {len(cohort_summaries)}")
    if skipped_cohorts:
        lines.append(f"**Cohorts skipped (empty):** {len(skipped_cohorts)}")
    lines.append("")

    for c in cohort_summaries:
        lines.append(f"## {c['label']} → nf-core/{c['pipeline']}")
        if c.get("rationale"):
            lines.append(f"- Rationale: {c['rationale']}")
        if c.get("criterion"):
            lines.append(f"- Cohort criterion: {c['criterion']}")
        else:
            lines.append("- Cohort criterion: (all samples)")
        if c.get("enrichment_fields"):
            lines.append(f"- Enrichment columns: {c['enrichment_fields']}")
        lines.append(f"- Samplesheet rows: {c['row_count']}")
        if c.get("excluded_accessions"):
            lines.append(f"- Excluded (ENA-missing): {c['excluded_accessions']}")
        lines.append("")

    if skipped_cohorts:
        lines.append("## Skipped cohorts")
        for sc in skipped_cohorts:
            tag = " (user-pinned)" if sc.get("user_pinned") else ""
            lines.append(
                f"- `{sc['label']}` → nf-core/{sc['pipeline']}{tag}: "
                f"{sc.get('reason') or 'no rows matched'}. Criterion: {sc.get('criterion') or {}}"
            )
        lines.append("")

    if multi:
        lines.append("Run all cohorts: `seqerakit launch.yml` (top-level launch.yml).")
    else:
        lines.append("Run: `seqerakit launch.yml` (inside the cohort folder).")
    return "\n".join(lines) + "\n"


def generate_report_outputs(
    *,
    config: ChatConfig,
    user_query: str,
    parser_plan,
    reporter_plan,
    uids: list[str],
    log_dir: str | Path,
    report_writer_fn: Callable[[ChatConfig, str, ReportWriterPlan, dict | None], Any],
    per_sample_reports: bool = True,
    pre_supplied_cohorts: list[dict] | None = None,
) -> tuple[dict, dict | Any, dict[str, str], str]:
    """
    Full report-generation flow (metadata fetch, protocol fetch, report writer call, persistence).
    Returns (reporter_result, report_writer_output, saved_files, reply_text).
    """
    plan_dump = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else parser_plan or {}
    parser_report_type = getattr(parser_plan, "report_type", None) or (plan_dump.get("report_type") if isinstance(plan_dump, dict) else None)
    report_type_value = normalize_report_type(getattr(reporter_plan, "report_type", None) or parser_report_type)
    per_uid_reports: list[dict] = []
    saved_files: dict[str, Any] = {}

    # NFCORE-specific scratch state populated below if the flow is NFCORE_*
    nfcore_state: dict[str, Any] = {
        "active": False,
        "cohorts": [],            # list[dict]: {label, pipeline, rationale, enrichment_metadata_fields, cohort_criterion}
        "selector_rationale": "",
        "resolutions": [],
        "accession_rows": [],
        "reporter_summary": {},
        "metadata_summary": {},
    }

    # Fetch metadata (combined vs per-sample)
    metadata_map: dict[str | None, dict] = {}
    if per_sample_reports:
        for uid in uids or [None]:
            current_uids = [uid] if uid else []
            metadata = (
                fetch_reporter_metadata(config, current_uids)
                if current_uids
                else {"ok": False, "error": "No UID provided"}
            )
            print("[DEBUG][REPORTER] Metadata fetch result ok:", metadata.get("ok"), "uid:", uid)
            metadata = annotate_metadata_with_sampletypes(config, metadata) if metadata else metadata
            metadata_map[uid] = metadata
    else:
        metadata = fetch_reporter_metadata(config, uids) if uids else {"ok": False, "error": "No UID provided"}
        print("[DEBUG][REPORTER] Combined metadata fetch ok:", metadata.get("ok"), "uids:", uids)
        metadata = annotate_metadata_with_sampletypes(config, metadata) if metadata else metadata
        metadata_map["__all__"] = metadata

    # Collect and fetch protocols
    all_protocol_refs: dict[tuple[str, str], dict[str, str]] = {}
    for md in metadata_map.values():
        refs = extract_protocol_refs_from_metadata(md) if md else []
        if refs:
            print("[DEBUG][REPORTER_PROTOCOL] Found protocol refs:", refs)
        for ref in refs:
            key = (ref.get("source", ""), ref.get("value", ""))
            if key[1]:
                all_protocol_refs[key] = ref
    protocol_payloads = fetch_protocols(config, list(all_protocol_refs.values())) if all_protocol_refs else {}
    if protocol_payloads:
        ok_ids = [pid for pid, resp in protocol_payloads.items() if isinstance(resp, dict) and resp.get("ok")]
        print("[DEBUG][REPORTER_PROTOCOL] Protocol fetch complete. ok:", ok_ids, "total:", len(protocol_payloads))
    else:
        print("[DEBUG][REPORTER_PROTOCOL] No protocols discovered.")
    protocol_files = download_and_extract_protocol_blobs(protocol_payloads, log_dir, config=config) if protocol_payloads else {}
    if protocol_files:
        print("[DEBUG][REPORTER_PROTOCOL] Downloaded/extracted protocol files for IDs:", list(protocol_files.keys()))

    protocols_for_llm = sanitize_protocols_for_llm(protocol_payloads)

    # ── NFCORE: cohorts come from the wizard (pre_supplied_cohorts) — no LLM selector
    if report_type_value and report_type_value.startswith("NFCORE"):
        nfcore_state["active"] = True
        try:
            from .seqera import (
                extract_accessions_from_metadata,
                resolve_accessions,
            )
        except Exception as e:  # pragma: no cover
            print("[DEBUG][REPORTER_NFCORE] Failed to import nfcore deps:", repr(e))
            extract_accessions_from_metadata = None  # type: ignore
            resolve_accessions = None  # type: ignore

        # 1) Build the metadata summary once — used by the report writer + emitter.
        try:
            full_summary = build_metadata_summary(metadata_map)
            deg_summary = filter_summary_for_deg(full_summary)
            nfcore_state["metadata_summary"] = full_summary
            nfcore_state["deg_summary"] = deg_summary
            print(
                "[DEBUG][REPORTER_NFCORE] metadata_summary sample types:",
                list((full_summary.get("by_sample_type") or {}).keys()),
            )
        except Exception as e:
            print("[DEBUG][REPORTER_NFCORE] build_metadata_summary failed:", repr(e))
            full_summary = {}
            deg_summary = {}

        # 2) Cohorts: must come from the wizard. If nothing was supplied, fall back
        # to a single-cohort run keyed off the report_type's pipeline suffix.
        pipeline_key = nfcore_pipeline_from_report_type(report_type_value)
        reporter_summary = {
            "uids": uids,
            "sample_types": sorted(list((full_summary.get("by_sample_type") or {}).keys())),
        }
        nfcore_state["reporter_summary"] = reporter_summary

        cohorts: list[dict[str, Any]] = []
        rationale = ""
        if pre_supplied_cohorts:
            cohorts = [dict(c) for c in pre_supplied_cohorts]
            rationale = (
                getattr(reporter_plan, "notes", "")
                or "Cohorts collected interactively via the nf-core wizard."
            )
            print(
                f"[DEBUG][REPORTER_NFCORE] Using pre-supplied wizard cohorts ({len(cohorts)}): "
                + ", ".join(
                    f"{c.get('label')}(pipeline={c.get('pipeline')}, criterion={c.get('cohort_criterion') or {}})"
                    for c in cohorts
                )
            )

        if not cohorts:
            fallback = pipeline_key or "rnaseq"
            cohorts = [{
                "label": fallback,
                "pipeline": fallback,
                "rationale": "Fallback single cohort (no wizard cohorts supplied).",
                "enrichment_metadata_fields": [],
                "cohort_criterion": {},
                "expected_sample_count": 0,
            }]
            rationale = rationale or cohorts[0]["rationale"]
            print(f"[DEBUG][REPORTER_NFCORE] No pre-supplied cohorts — falling back to single cohort '{fallback}'.")

        nfcore_state["cohorts"] = cohorts
        nfcore_state["selector_rationale"] = rationale
        nfcore_state["conflict_detected"] = False
        nfcore_state["pipeline_key"] = pipeline_key

        # 3) For schema/template loading, pick the first cohort's pipeline as the
        # canonical report_type_value (so report_writer loads SOME nf-core
        # template). Cohort-specific filtering happens in _handle_nfcore_artifacts.
        primary_pipeline = cohorts[0]["pipeline"]
        report_type_value = f"NFCORE_{primary_pipeline.upper()}"

        # 3) ENA accession resolution
        try:
            accessions: list[str] = []
            for md in metadata_map.values():
                accessions.extend(extract_accessions_from_metadata(md) if extract_accessions_from_metadata else [])
            seen: set[str] = set()
            ordered: list[str] = []
            for acc in accessions:
                if acc and acc not in seen:
                    seen.add(acc)
                    ordered.append(acc)
            resolutions = resolve_accessions(ordered) if (resolve_accessions and ordered) else []
            nfcore_state["resolutions"] = resolutions
            rows: list[dict[str, Any]] = []
            for r in resolutions:
                if r.missing:
                    continue
                for run in r.runs:
                    rows.append({
                        "accession": r.accession,
                        "run_accession": run.run_accession,
                        "fastq_1": run.fastq_1,
                        "fastq_2": run.fastq_2,
                        "library_layout": run.layout,
                    })
            nfcore_state["accession_rows"] = rows
            print(
                f"[DEBUG][REPORTER_NFCORE] Resolved {len(rows)} runs from "
                f"{sum(1 for r in resolutions if not r.missing)}/{len(resolutions)} accessions"
            )
        except Exception as e:
            print("[DEBUG][REPORTER_NFCORE] ENA resolver failed:", repr(e))

    # ── NFCORE bypass: skip the report_writer LLM entirely ─────────────────
    # Rationale: the seqera emitter (_handle_nfcore_artifacts) already synthesizes
    # samplesheet rows directly from accession_metadata via its fallback path. The
    # report_writer was producing a JSON shape that the emitter then re-parsed and
    # validated against templates — pure indirection that cost us a 5.1M-token
    # prompt on a 195-UID NDMA-mice flow. The wizard's explicit cohort_criteria +
    # enrichment_fields give us everything we need without an LLM call here.
    if nfcore_state["active"]:
        print("[DEBUG][REPORTER_NFCORE] Bypassing report_writer; emitter will synthesize rows from accession_metadata.")
        per_uid_reports = [{
            "uid": None,
            "metadata": metadata_map.get("__all__") if not per_sample_reports else None,
            "protocols": protocol_payloads,
            "report_writer_output": {
                "report_type": report_type_value,
                "report": {"samplesheet": []},
                "narrative": "Samplesheet synthesized from accession metadata (no LLM call).",
                "notes": "NFCORE bypass — emitter handles row construction.",
            },
        }]
        merged_report = {"all_samples": per_uid_reports[0]["report_writer_output"]}
    elif per_sample_reports:
        loop_uids = uids or [None]
        for idx, uid in enumerate(loop_uids):
            meta = metadata_map.get(uid)
            reporter_context = getattr(reporter_plan, "reporter_context", {}) or {}
            reporter_context = {
                **(reporter_context or {}),
                "uids": [uid] if uid else [],
                "metadata": meta,
                "protocols": protocols_for_llm,
                "protocol_files": protocol_files,
                "parser_plan": plan_dump,
                "reporter_plan": reporter_plan.model_dump() if hasattr(reporter_plan, "model_dump") else {},
            }
            if nfcore_state["active"]:
                reporter_context["nfcore_cohorts"] = nfcore_state["cohorts"]
                reporter_context["accession_rows"] = nfcore_state["accession_rows"]
                reporter_context["nfcore_selector_rationale"] = nfcore_state["selector_rationale"]
                reporter_context["nfcore_metadata_summary"] = nfcore_state.get("deg_summary") or nfcore_state.get("metadata_summary") or {}
            report_writer_plan = ReportWriterPlan(
                report_type=report_type_value,
                reporter_context=reporter_context,
                notes=getattr(reporter_plan, "notes", ""),
            )
            template = load_report_template(config, report_writer_plan.report_type)
            template_for_llm = {k: v for k, v in (template or {}).items() if k != "schema"}
            print("[DEBUG][REPORT_WRITER] Using template keys (schema stripped):", list(template_for_llm.keys()), "uid:", uid)
            report_writer_output = report_writer_fn(config, user_query, report_writer_plan, template_for_llm)
            per_uid_reports.append(
                {
                    "uid": uid,
                    "metadata": meta,
                    "protocols": protocol_payloads,
                    "report_writer_output": report_writer_output.model_dump()
                    if hasattr(report_writer_output, "model_dump")
                    else report_writer_output,
                }
            )
        merged_report = {
            entry.get("uid") or f"item_{i}": entry.get("report_writer_output", {}) for i, entry in enumerate(per_uid_reports)
        }
    else:
        meta = metadata_map.get("__all__")
        reporter_context = getattr(reporter_plan, "reporter_context", {}) or {}
        reporter_context = {
            **(reporter_context or {}),
            "uids": uids,
            "metadata": meta,
            "protocols": protocols_for_llm,
            "protocol_files": protocol_files,
            "parser_plan": plan_dump,
            "reporter_plan": reporter_plan.model_dump() if hasattr(reporter_plan, "model_dump") else {},
        }
        report_writer_plan = ReportWriterPlan(
            report_type=report_type_value,
            reporter_context=reporter_context,
            notes=getattr(reporter_plan, "notes", ""),
        )
        template = load_report_template(config, report_writer_plan.report_type)
        template_for_llm = {k: v for k, v in (template or {}).items() if k != "schema"}
        print("[DEBUG][REPORT_WRITER] Using template keys (schema stripped):", list(template_for_llm.keys()), "uid: ALL")
        combined_output = report_writer_fn(config, user_query, report_writer_plan, template_for_llm)
        per_uid_reports.append(
            {
                "uid": None,
                "metadata": meta,
                "protocols": protocol_payloads,
                "report_writer_output": combined_output.model_dump()
                if hasattr(combined_output, "model_dump")
                else combined_output,
            }
        )
        merged_report = {"all_samples": combined_output.model_dump() if hasattr(combined_output, "model_dump") else combined_output}

    reporter_result = {
        "reports": per_uid_reports,
        "merged_report": merged_report,
    }
    report_writer_output = merged_report

    # Persist report payloads and metadata
    extracted_protocols = {}
    for pid, files_list in (protocol_files or {}).items():
        texts = []
        for f in files_list or []:
            if f.get("text"):
                texts.append({"filename": f.get("filename"), "text": f.get("text")})
        if texts:
            extracted_protocols[pid] = texts

    meta_map = {}
    for idx, entry in enumerate(per_uid_reports):
        uid_key = entry.get("uid") or f"item_{idx}"
        combined_meta = entry.get("metadata") or {}
        combined_meta = dict(combined_meta)
        combined_meta["protocols"] = extracted_protocols
        meta_map[uid_key] = combined_meta
        saved = persist_report_file(
            f"report_writer_output_{uid_key}",
            entry.get("report_writer_output"),
            log_dir,
            kind="report",
        )
        if saved:
            saved_files[f"report_writer_output_{uid_key}"] = saved

    report_type_label = normalize_report_type(report_type_value) or "REPORT"
    merged_filename = f"merged_report_{report_type_label}"
    merged_path = persist_report_file(merged_filename, merged_report, log_dir, kind="report")
    if merged_path:
        saved_files["merged_report"] = merged_path

    if report_type_label == "GEO" and merged_path:
        try:
            geo_workbooks = export_geo_report_to_seq_xlsx(
                merged_path,
                str(config.SEQ_TEMPLATE_PATH),
                Path(merged_path).parent,
                one_workbook_per_uid=False,
            )
            if geo_workbooks:
                saved_files["geo_seq_workbooks"] = geo_workbooks
                print("[DEBUG][REPORTER_GEO] Exported GEO submission workbooks:", geo_workbooks)
        except Exception as e:
            print("[DEBUG][REPORTER_GEO] Failed to export GEO XLSX:", repr(e))
    elif report_type_label.startswith("NFCORE") and merged_path and nfcore_state["active"]:
        try:
            nfcore_artifacts = _handle_nfcore_artifacts(
                config=config,
                user_query=user_query,
                merged_path=merged_path,
                merged_report=merged_report,
                nfcore_state=nfcore_state,
                metadata_map=metadata_map,
            )
            for k, v in (nfcore_artifacts.get("saved_files") or {}).items():
                saved_files[k if k.startswith("nfcore_") else f"nfcore_{k}"] = v
            run_urls = nfcore_artifacts.get("run_urls") or []
            if run_urls:
                saved_files["nfcore_tower_run_urls"] = run_urls
                print("[DEBUG][REPORTER_NFCORE] Tower run URLs:", run_urls)
            print(
                "[DEBUG][REPORTER_NFCORE] Emitted nf-core artifacts at",
                nfcore_artifacts.get("out_dir"),
            )
        except Exception as e:
            print("[DEBUG][REPORTER_NFCORE] Failed to emit nf-core artifacts:", repr(e))
    elif report_type_label == "SRA" and merged_path:
        try:
            sra_workbooks = export_sra_report_to_xlsx(
                merged_path,
                str(Path(config.BASE_DIR) / "reports" / "SRA_metadata.xlsx"),
                Path(merged_path).parent,
                one_workbook_per_uid=False,
            )
            if sra_workbooks:
                saved_files["sra_submission_workbooks"] = sra_workbooks
                print("[DEBUG][REPORTER_SRA] Exported SRA submission workbooks:", sra_workbooks)
        except Exception as e:
            print("[DEBUG][REPORTER_SRA] Failed to export SRA workbook:", repr(e))
        try:
            biosample_workbooks = export_sra_biosample_report_to_xlsx(
                merged_path,
                str(Path(config.BASE_DIR) / "reports" / "SRA_biosample.xlsx"),
                Path(merged_path).parent,
                one_workbook_per_uid=False,
            )
            if biosample_workbooks:
                saved_files["sra_biosample_workbooks"] = biosample_workbooks
                print("[DEBUG][REPORTER_SRA] Exported SRA BioSample workbooks:", biosample_workbooks)
        except Exception as e:
            print("[DEBUG][REPORTER_SRA] Failed to export SRA BioSample workbook:", repr(e))

    meta_path = persist_report_file("report_metadata", meta_map, log_dir, kind="report")
    if meta_path:
        saved_files["metadata"] = meta_path
    if protocol_payloads:
        proto_path = persist_report_file("protocols", protocol_payloads, log_dir, subdir="protocols", kind="protocol")
        if proto_path:
            saved_files["protocols"] = proto_path
    if protocol_files:
        proto_files_path = persist_report_file("protocol_files", protocol_files, log_dir, subdir="protocols", kind="protocol")
        if proto_files_path:
            saved_files["protocol_files"] = proto_files_path

    reply_lines = ["Generated report payload is available in the Reporter result panel."]
    for entry in per_uid_reports:
        narrative = ((entry.get("report_writer_output") or {}).get("narrative")) if entry else None
        if narrative:
            reply_lines.insert(0, narrative.strip())
            break
    reply = "\n\n".join(reply_lines)

    return reporter_result, report_writer_output, saved_files, reply



def load_report_template(config, report_type: str | None) -> dict:
    """
    Load a report template JSON from reports/ based on report_type name.
    Returns an empty dict on missing files or parse errors to let report writer proceed with defaults.
    """
    template_basename = get_report_template_basename(report_type)
    if not template_basename:
        return {}
    try:
        path = Path(config.BASE_DIR) / "reports" / f"{template_basename}.json"
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        print("[DEBUG][REPORT_WRITER] Failed to load report template:", repr(e))
    return {}


def normalize_report_type(report_type: str | None) -> str | None:
    """
    Normalize user/model-facing report type aliases into canonical internal labels.
    Returns None for empty values so callers can preserve "not specified" semantics.
    """
    if not isinstance(report_type, str):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", report_type).strip("_").upper()
    if not cleaned:
        return None

    alias_map = {
        "GEO": "GEO",
        "PRIDE": "PRIDE",
        "SRA": "SRA",
        "NFCORE": "NFCORE",
        "NF_CORE": "NFCORE",
        "NFCORE_RNASEQ": "NFCORE_RNASEQ",
        "NFCORE_RNASEQ_SAMPLESHEET": "NFCORE_RNASEQ",
        "NF_CORE_RNASEQ": "NFCORE_RNASEQ",
        "NFCORE_SCRNASEQ": "NFCORE_SCRNASEQ",
        "NFCORE_SCRNASEQ_SAMPLESHEET": "NFCORE_SCRNASEQ",
        "NF_CORE_SCRNASEQ": "NFCORE_SCRNASEQ",
        "NFCORE_ATACSEQ": "NFCORE_ATACSEQ",
        "NF_CORE_ATACSEQ": "NFCORE_ATACSEQ",
        "NFCORE_CHIPSEQ": "NFCORE_CHIPSEQ",
        "NF_CORE_CHIPSEQ": "NFCORE_CHIPSEQ",
        "NFCORE_SAREK": "NFCORE_SAREK",
        "NF_CORE_SAREK": "NFCORE_SAREK",
        "NFCORE_METHYLSEQ": "NFCORE_METHYLSEQ",
        "NF_CORE_METHYLSEQ": "NFCORE_METHYLSEQ",
        "NFCORE_AMPLISEQ": "NFCORE_AMPLISEQ",
        "NF_CORE_AMPLISEQ": "NFCORE_AMPLISEQ",
        "NFCORE_FETCHNGS": "NFCORE_FETCHNGS",
        "NF_CORE_FETCHNGS": "NFCORE_FETCHNGS",
    }
    return alias_map.get(cleaned, cleaned)


def nfcore_pipeline_from_report_type(report_type: str | None) -> str | None:
    """If report_type is NFCORE_<PIPELINE>, return the lowercase pipeline key.
    Generic NFCORE returns None (caller should run pipeline selector).
    """
    canonical = normalize_report_type(report_type)
    if not canonical or not canonical.startswith("NFCORE"):
        return None
    if canonical == "NFCORE":
        return None
    return canonical[len("NFCORE_"):].lower()


def get_report_template_basename(report_type: str | None) -> str | None:
    """
    Map canonical report types to the JSON template basename stored in reports/.
    """
    canonical = normalize_report_type(report_type)
    if not canonical:
        return None
    static = {
        "GEO": "GEO-updated",
        "PRIDE": "pride",
        "SRA": "SRA",
    }
    if canonical in static:
        return static[canonical]
    if canonical.startswith("NFCORE_"):
        return f"nfcore/{canonical[len('NFCORE_'):].lower()}"
    if canonical == "NFCORE":
        return "nfcore/rnaseq"  # generic default; pipeline selector overrides upstream
    return canonical.lower()


def extract_protocol_refs_from_metadata(metadata: dict) -> list[dict[str, str]]:
    """
    Walk metadata dict and collect supported protocol references from any key named 'Protocol'.
    Supported references:
    - fairdata-dev / fairdata URLs pointing at /sops/{id-or-name}
    - fairdomhub URLs pointing at /sops/{id-or-name}
    - direct protocol names beginning with 'P.'
    Unsupported external/vendor URLs are ignored.
    """
    refs: dict[tuple[str, str], dict[str, str]] = {}

    def _add(source: str, value: str, raw_value: str) -> None:
        clean = value.strip()
        if not clean:
            return
        refs[(source, clean)] = {"source": source, "value": clean, "raw": raw_value}

    def _classify(value: Any) -> None:
        if isinstance(value, (int, float)):
            _add("fairdata-dev", str(value), str(value))
            return
        if not isinstance(value, str):
            return

        raw = value.strip()
        if not raw:
            return

        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        sop_match = re.search(r"/sops/([^/?#]+)", path, flags=re.IGNORECASE)

        if host in {"fairdata-dev.mit.edu", "fairdata.mit.edu"} and sop_match:
            _add(host, sop_match.group(1), raw)
            return
        if host == "fairdomhub.org" and sop_match:
            _add(host, sop_match.group(1), raw)
            return
        if re.match(r"^P\.[A-Za-z0-9._-]+$", raw):
            _add("protocol_name", raw, raw)
            return

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() == "protocol":
                    _classify(v)
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(metadata)
    return [refs[key] for key in sorted(refs)]


def _request_protocol_record(config: ChatConfig, base_url: str, protocol_ref: str) -> dict:
    """
    Fetch a protocol record from a specific host using the SOP detail endpoint.
    Accepts numeric ids or protocol names.
    """
    host = (urlparse(base_url).netloc or "").lower()
    if host == "fairdomhub.org":
        url = f"{base_url.rstrip('/')}/sops/{quote(protocol_ref, safe='')}/"
    else:
        url = f"{base_url.rstrip('/')}/nextseek_api/sops/{quote(protocol_ref, safe='')}/"
    auth = None
    headers: dict[str, str] = {}
    auth_mode = "None"

    if host == "fairdomhub.org":
        fdh_api = os.getenv("FDH_API")
        if fdh_api:
            headers["Authorization"] = f"Bearer {fdh_api}"
            headers["Accept"] = "application/json"
            auth_mode = "Bearer(FDH_API)"
    elif config.API_USER and config.API_PASS:
        auth = (config.API_USER, config.API_PASS)
        auth_mode = "Basic"

    print("[DEBUG][API] Request:")
    print("  METHOD: GET")
    print(f"  URL:    {url}")
    print("  PARAMS: {'page_size': 1000}")
    print("  BODY:   {}")
    print(f"  AUTH:   {auth_mode}")
    print("  TIMEOUT:90s")

    try:
        resp = requests.get(url, auth=auth, headers=headers or None, params={"page_size": 1000}, timeout=90)
        print("[DEBUG][API] Response:")
        print(f"  STATUS: {resp.status_code}")
        preview = resp.text[:300].replace("\n", " ")
        print(f"  PREVIEW: {preview!r}")
        try:
            data = resp.json()
        except Exception:
            data = {"_raw": resp.text[:1000]}
        return {
            "ok": resp.ok,
            "url": url,
            "status_code": resp.status_code,
            "method": "GET",
            "query": {"page_size": 1000},
            "body": {},
            "data": data,
            "source_base_url": base_url.rstrip("/"),
            "protocol_ref": protocol_ref,
        }
    except Exception as e:
        print(f"[DEBUG][API] Exception: {repr(e)}")
        return {
            "ok": False,
            "error": repr(e),
            "url": url,
            "method": "GET",
            "source_base_url": base_url.rstrip("/"),
            "protocol_ref": protocol_ref,
        }


def fetch_protocols(config, protocol_refs: list[dict[str, str]]) -> dict:
    """
    Fetch protocol details for classified metadata references.
    fairdata-dev/fairdata hosts are queried directly; fairdomhub uses its own host;
    P.* names are resolved against the configured NExtSEEK API host.
    Returns a mapping keyed by the protocol reference value.
    """
    results: dict[str, dict] = {}
    host_map = {
        "fairdata-dev.mit.edu": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://nextseek-dev.mit.edu",
        "fairdata.mit.edu": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://nextseek-dev.mit.edu",
        "fairdomhub.org": "https://fairdomhub.org",
        "fairdata-dev": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://fairdata-dev.mit.edu",
        "protocol_name": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://fairdata-dev.mit.edu",
    }

    for ref in protocol_refs or []:
        source = ref.get("source", "")
        value = ref.get("value", "")
        if not value:
            continue
        base_url = host_map.get(source)
        if not base_url:
            print("[DEBUG][REPORTER_PROTOCOL] Skipping unsupported protocol reference:", ref)
            continue
        try:
            resp = _request_protocol_record(config, base_url, value)
            resp["protocol_source"] = source
            resp["protocol_raw"] = ref.get("raw")
            results[value] = resp
            print("[DEBUG][REPORTER_PROTOCOL] Fetched protocol", value, "source:", source, "ok:", resp.get("ok"))
        except Exception as e:
            results[value] = {"ok": False, "error": repr(e), "protocol_source": source, "protocol_raw": ref.get("raw")}
            print("[DEBUG][REPORTER_PROTOCOL] Failed to fetch protocol", value, "source:", source, "err:", repr(e))
    return results


def _extract_docx_text(content: bytes) -> str | None:
    """
    Extract plain text from a DOCX binary by reading word/document.xml.
    Strips tags, unescapes entities, and returns None on failure so callers can continue gracefully.
    """
    try:
        with ZipFile(BytesIO(content)) as zf:
            with zf.open("word/document.xml") as f:
                xml = f.read().decode("utf-8", errors="ignore")
        # Strip tags and unescape XML entities
        text = re.sub(r"<[^>]+>", " ", xml)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        print("[DEBUG][REPORTER_PROTOCOL] docx extract failed:", repr(e))
        return None


def _extract_pdf_text(content: bytes) -> str | None:
    """
    Extract text from a PDF binary using PyPDF2 when available.
    Returns None when the library is missing or extraction fails, logging debug hints for diagnostics.
    """
    try:
        import PyPDF2
    except Exception:
        print("[DEBUG][REPORTER_PROTOCOL] PyPDF2 not available; skipping PDF text extraction.")
        return None
    try:
        reader = PyPDF2.PdfReader(BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None
    except Exception as e:
        print("[DEBUG][REPORTER_PROTOCOL] PDF extract failed:", repr(e))
        return None


def sanitize_protocols_for_llm(protocol_payloads: dict) -> dict:
    """
    Sanitize protocol payloads for LLM consumption:
    - Replace localhost URLs with fairdata-dev.mit.edu
    - Remove internal fields not needed by the LLM
    Returns a cleaned copy of the protocol payloads.
    Keeps URLs model-safe while preserving useful content for prompt context.
    """
    if not protocol_payloads:
        return {}

    def fix_url(url: str | None) -> str | None:
        if not url:
            return url
        if "localhost" in url or "127.0.0.1" in url:
            path_match = re.search(r"https?://[^/]+(/.*)", url)
            if path_match:
                return f"https://fairdata-dev.mit.edu{path_match.group(1)}"
        return url

    def sanitize_dict(d: dict) -> dict:
        result = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = sanitize_dict(v)
            elif isinstance(v, list):
                result[k] = [sanitize_dict(i) if isinstance(i, dict) else fix_url(i) if isinstance(i, str) and ("localhost" in i or "127.0.0.1" in i) else i for i in v]
            elif isinstance(v, str) and ("localhost" in v or "127.0.0.1" in v):
                result[k] = fix_url(v)
            else:
                result[k] = v
        return result

    return {pid: sanitize_dict(resp) if isinstance(resp, dict) else resp for pid, resp in protocol_payloads.items()}


def download_and_extract_protocol_blobs(protocol_payloads: dict, base_dir: str | Path, config=None) -> dict:
    """
    For each protocol response, download attached files (content_blobs), save them under base_dir/protocols/files,
    and attempt to extract text (docx/pdf). Returns a mapping id -> list of file metadata with text.
    """
    store = ArtifactStore(base_dir)
    results: dict[str, list[dict]] = {}
    session = requests.Session()

    for pid, resp in (protocol_payloads or {}).items():
        files_out: list[dict] = []
        source_base_url = resp.get("source_base_url") if isinstance(resp, dict) else None
        source_host = (urlparse(source_base_url).netloc or "").lower() if source_base_url else ""
        if source_host == "fairdomhub.org":
            session.auth = None
            session.headers.pop("Authorization", None)
            fdh_api = os.getenv("FDH_API")
            if fdh_api:
                session.headers["Authorization"] = f"Bearer {fdh_api}"
        else:
            session.headers.pop("Authorization", None)
            session.auth = (config.API_USER, config.API_PASS) if config and config.API_USER and config.API_PASS else None
        # Response structure: {"ok": ..., "data": {"data": {"id": ..., "attributes": {"content_blobs": [...]}}}}
        attrs = resp.get("data", {}).get("data", {}).get("attributes", {}) if isinstance(resp, dict) else {}
        blobs = attrs.get("content_blobs") or []
        for idx, blob in enumerate(blobs):
            link = blob.get("link") or blob.get("url")
            # Fix localhost URLs - content blobs are served from fairdata-dev.mit.edu
            if link and ("localhost" in link or "127.0.0.1" in link):
                path_match = re.search(r"https?://[^/]+(/.*)", link)
                if path_match and source_base_url:
                    link = f"{source_base_url}{path_match.group(1)}"
            fname = blob.get("original_filename") or f"{pid}_{idx}"
            entry: dict = {"filename": fname, "content_type": blob.get("content_type"), "link": link}
            if not link:
                files_out.append({"filename": fname, "ok": False, "error": "No link in content_blob"})
                continue
            try:
                content_resp = None
                attempted = []
                for candidate in (f"{link}/download", f"{link}?download=1", link):
                    attempted.append(candidate)
                    r = session.get(candidate, timeout=30)
                    ctype_hdr = (r.headers.get("Content-Type") or "").lower()
                    looks_json = ctype_hdr.startswith("application/vnd.api+json") or r.content[:1] in (b"{", b"[")
                    is_ok = r.status_code == 200 and not looks_json
                    if is_ok:
                        content_resp = r
                        entry["status_code"] = r.status_code
                        entry["response_content_type"] = r.headers.get("Content-Type")
                        entry["download_url_used"] = candidate
                        break
                    entry["last_status"] = r.status_code
                    entry["last_response_content_type"] = r.headers.get("Content-Type")

                if content_resp is None:
                    entry.update({"ok": False, "error": f"Unable to download blob; tried {attempted}"})
                    files_out.append(entry)
                    continue

                artifact_entry = store.write_bytes(
                    key=f"protocol_blob_{pid}_{idx}",
                    label=fname,
                    filename=fname,
                    payload=content_resp.content,
                    kind="protocol",
                    subdir="files",
                    mime=blob.get("content_type"),
                )
                dest_path = artifact_entry["path"] if artifact_entry else None
                text = None
                text_error = None
                ctype = (blob.get("content_type") or "").lower()
                try:
                    if "pdf" in ctype or fname.lower().endswith(".pdf") or content_resp.content[:4] == b"%PDF":
                        text = _extract_pdf_text(content_resp.content)
                    elif "word" in ctype or fname.lower().endswith(".docx"):
                        text = _extract_docx_text(content_resp.content)
                    else:
                        # Fallback: try docx, then pdf
                        text = _extract_docx_text(content_resp.content) or _extract_pdf_text(content_resp.content)
                except Exception as e:
                    text_error = repr(e)

                # Truncate text to ~3000 tokens max
                text_truncated = False
                if text:
                    PROTOCOL_TOKEN_LIMIT = 3000
                    token_count = estimate_tokens_from_text(text)
                    if token_count > PROTOCOL_TOKEN_LIMIT:
                        # Truncate: ~4 chars per token
                        max_chars = PROTOCOL_TOKEN_LIMIT * 4
                        text = text[:max_chars] + "\n\n[... truncated, exceeded 3000 token limit ...]"
                        text_truncated = True

                entry.update(
                    {
                        "path": dest_path,
                        "md5": blob.get("md5sum"),
                        "sha1": blob.get("sha1sum"),
                        "size": blob.get("size"),
                        "ok": True,
                        "text": text,
                        "text_truncated": text_truncated,
                        "text_error": text_error,
                    }
                )
                files_out.append(entry)
            except Exception as e:
                entry.update({"ok": False, "error": repr(e)})
                files_out.append(entry)
        if files_out:
            results[str(pid)] = files_out
    return results


# ======================================================
# GEO report -> SEQ template export
# ======================================================

def _copy_row_format(ws: Worksheet, source_row: int, target_row: int) -> None:
    """
    Copy styling and height from source_row to target_row on the same sheet.
    Preserves number formats, comments, and hyperlinks so cloned rows match the template.
    Useful when expanding list sections without rebuilding formatting manually.
    """
    ws.row_dimensions[target_row].height = ws.row_dimensions[source_row].height
    for cell in ws[source_row]:
        tgt = ws.cell(row=target_row, column=cell.col_idx)
        tgt._style = copy(cell._style)
        tgt.number_format = cell.number_format
        tgt._comment = cell._comment
        tgt.hyperlink = cell.hyperlink


def _write_cell(ws: Worksheet, row: int, col: int, value: Any) -> None:
    """
    Write a value into a worksheet cell unless the value is None.
    Keeps template defaults intact when optional fields are absent.
    """
    if value is None:
        return
    ws.cell(row=row, column=col).value = value


def _write_list_down(
    ws: Worksheet,
    start_row: int,
    col: int,
    values: Sequence[Any] | None,
    *,
    max_rows: int | None = None,
) -> None:
    """
    Write a sequence of values down a column, optionally packing overflow into the final row.
    Preserves existing cell content when condensing and skips empty values so templates stay clean.
    """
    if not values:
        return
    for idx, val in enumerate(values):
        if val in (None, ""):
            continue
        row = start_row + idx
        if max_rows and idx >= max_rows:
            row = start_row + max_rows - 1
            existing = ws.cell(row=row, column=col).value or ""
            separator = "\n" if existing else ""
            ws.cell(row=row, column=col).value = f"{existing}{separator}{val}"
            continue
        ws.cell(row=row, column=col).value = val


def _build_header_map(ws: Worksheet, header_row: int) -> dict[str, list[int]]:
    """
    Build a mapping of normalized header labels to column indices for a given header row.
    Supports duplicate headers by returning lists so repeated fields can be filled in order.
    """
    mapping: dict[str, list[int]] = {}
    for cell in ws[header_row]:
        if cell.value is None:
            continue
        label = _normalize_sheet_label(cell.value)
        mapping.setdefault(label, []).append(cell.col_idx)
    return mapping


def _normalize_geo_key(value: Any) -> str:
    """
    Normalize GEO template keys so starred/unstarred variants map to the same logical field.
    Collapses whitespace and lowercases labels to tolerate minor template or model variations.
    """
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"^[*#\s]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_sheet_label(value: Any) -> str:
    """
    Normalize worksheet labels while preserving leading marker characters like '*'.
    This keeps starred and unstarred template columns distinct.
    """
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _geo_get(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    """
    Read a GEO field from a mapping using exact and normalized-key fallback.
    This lets the exporter accept both literal template keys ('*title') and logical keys ('title').
    """
    if not isinstance(mapping, Mapping):
        return None

    normalized = {_normalize_geo_key(key): value for key, value in mapping.items() if isinstance(key, str)}
    for key in keys:
        if key in mapping:
            return mapping[key]
        norm_key = _normalize_geo_key(key)
        if norm_key in normalized:
            return normalized[norm_key]
    return None


def _find_first_row_with_label(ws: Worksheet, label: str, *, col: int = 1) -> int | None:
    """
    Find the first row whose target column matches a label after GEO-key normalization.
    """
    target = _normalize_geo_key(label)
    for row_idx in range(1, ws.max_row + 1):
        if _normalize_geo_key(ws.cell(row=row_idx, column=col).value) == target:
            return row_idx
    return None


def _find_sample_header_row(ws: Worksheet) -> int:
    """
    Locate the sample header row from the required GEO sample columns.
    Falls back to the current template row if discovery fails.
    """
    required = {
        _normalize_geo_key("*library name"),
        _normalize_geo_key("*title"),
        _normalize_geo_key("*library strategy"),
        _normalize_geo_key("*organism"),
    }
    for row_idx in range(1, ws.max_row + 1):
        labels = {
            _normalize_geo_key(ws.cell(row=row_idx, column=col).value)
            for col in range(1, ws.max_column + 1)
            if ws.cell(row=row_idx, column=col).value not in (None, "")
        }
        if required.issubset(labels):
            return row_idx
    return 38


def _find_paired_end_header_row(ws: Worksheet) -> int:
    """
    Locate the paired-end table header row from the file-name columns.
    """
    required = {
        _normalize_geo_key("file name 1"),
        _normalize_geo_key("file name 2"),
        _normalize_geo_key("file name 3"),
        _normalize_geo_key("file name 4"),
    }
    for row_idx in range(1, ws.max_row + 1):
        labels = {
            _normalize_geo_key(ws.cell(row=row_idx, column=col).value)
            for col in range(1, 5)
            if ws.cell(row=row_idx, column=col).value not in (None, "")
        }
        if required.issubset(labels):
            return row_idx
    return 76


def _select_study_summary(study: Mapping[str, Any] | None) -> str | None:
    """
    Return the best available study summary, preferring 'summary (abstract)' then 'summary'.
    Keeps GEO population tolerant of missing fields while still returning None when nothing is present.
    """
    if not isinstance(study, Mapping):
        return None
    return _geo_get(study, "*summary (abstract)", "summary (abstract)", "summary") or None


def _populate_geo_seq_workbook(wb, report: Mapping[str, Any]) -> None:
    """
    Fill the GEO SEQ template workbook in-place from a single report entry.
    Writes study metadata, sample records, protocols, paired-end entries, and checksum info while preserving formats.
    Mutates the workbook directly so callers can immediately save it to disk.
    """
    meta_sheet = wb["Metadata"]
    study = report.get("study") or {}
    samples = report.get("samples") or []
    protocols = report.get("protocols") or {}
    paired_end_experiments = report.get("paired_end_experiments") or []
    checksums = (report.get("checksums") or {})

    # ---- Study section ----
    title_row = _find_first_row_with_label(meta_sheet, "*title") or 12
    summary_row = _find_first_row_with_label(meta_sheet, "*summary (abstract)") or 13
    design_row = _find_first_row_with_label(meta_sheet, "*experimental design") or 14
    contributor_row = _find_first_row_with_label(meta_sheet, "contributor") or 15
    supplementary_row = _find_first_row_with_label(meta_sheet, "supplementary file") or 22

    _write_cell(meta_sheet, title_row, 2, _geo_get(study, "*title", "title"))
    _write_cell(meta_sheet, summary_row, 2, _select_study_summary(study))
    _write_cell(meta_sheet, design_row, 2, _geo_get(study, "*experimental design", "experimental design"))

    contributors = _geo_get(study, "contributor") or []
    _write_list_down(meta_sheet, start_row=contributor_row, col=2, values=contributors, max_rows=7)

    supplementary = _geo_get(study, "supplementary file") or []
    _write_list_down(meta_sheet, start_row=supplementary_row, col=2, values=supplementary, max_rows=16)

    # ---- Samples section ----
    sample_header_row = _find_sample_header_row(meta_sheet)
    header_map = _build_header_map(meta_sheet, sample_header_row)
    sample_start_row = sample_header_row + 1
    sample_rows_available = max(0, 52 - sample_start_row + 1)

    extra_sample_rows = max(0, len(samples) - sample_rows_available)
    if extra_sample_rows:
        template_row = 52
        insert_at = template_row + 1
        for offset in range(extra_sample_rows):
            meta_sheet.insert_rows(insert_at + offset)
            _copy_row_format(meta_sheet, template_row, insert_at + offset)

    def set_sample_field(row_idx: int, header_key: str, value: Any, occurrence: int = 0) -> None:
        key = _normalize_sheet_label(header_key)
        cols = header_map.get(key)
        if not cols or occurrence >= len(cols) or value in (None, ""):
            return
        meta_sheet.cell(row=row_idx, column=cols[occurrence]).value = value

    for idx, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            continue
        row_idx = sample_start_row + idx
        set_sample_field(row_idx, "*library name", _geo_get(sample, "*library name", "library name"))
        set_sample_field(row_idx, "*title", _geo_get(sample, "*title", "title"))
        set_sample_field(row_idx, "*library strategy", _geo_get(sample, "*library strategy", "library strategy"))
        set_sample_field(row_idx, "*organism", _geo_get(sample, "*organism", "organism"))
        set_sample_field(row_idx, "**tissue", _geo_get(sample, "**tissue", "tissue"))
        set_sample_field(row_idx, "**cell line", _geo_get(sample, "**cell line", "cell line"))
        set_sample_field(row_idx, "**cell type", _geo_get(sample, "**cell type", "cell type"))
        set_sample_field(row_idx, "genotype", _geo_get(sample, "genotype"))
        set_sample_field(row_idx, "treatment", _geo_get(sample, "treatment"))
        set_sample_field(row_idx, "batch", _geo_get(sample, "batch"))
        set_sample_field(row_idx, "*molecule", _geo_get(sample, "*molecule", "molecule"))
        set_sample_field(row_idx, "*single or paired-end", _geo_get(sample, "*single or paired-end", "single or paired-end"))
        set_sample_field(row_idx, "*instrument model", _geo_get(sample, "*instrument model", "instrument model"))
        set_sample_field(row_idx, "description", _geo_get(sample, "description"))
        set_sample_field(row_idx, "processed data file", _geo_get(sample, "processed data file"), occurrence=0)
        set_sample_field(row_idx, "processed data file", _geo_get(sample, "processed data file (2)"), occurrence=1)
        set_sample_field(row_idx, "*raw file", _geo_get(sample, "*raw file", "raw file"), occurrence=0)
        set_sample_field(row_idx, "raw file", _geo_get(sample, "raw file"), occurrence=0)
        set_sample_field(row_idx, "raw file", _geo_get(sample, "raw file (2)"), occurrence=1)
        set_sample_field(row_idx, "raw file", _geo_get(sample, "raw file (3)"), occurrence=2)
        set_sample_field(row_idx, "raw file", _geo_get(sample, "raw file (4)"), occurrence=3)

    # ---- Protocols section ----
    growth_row = _find_first_row_with_label(meta_sheet, "growth protocol") or 57
    treatment_row = _find_first_row_with_label(meta_sheet, "treatment protocol") or 58
    extract_row = _find_first_row_with_label(meta_sheet, "*extract protocol") or 59
    library_row = _find_first_row_with_label(meta_sheet, "*library construction protocol") or 60
    base_data_processing_row = _find_first_row_with_label(meta_sheet, "*data processing step") or 62

    _write_cell(meta_sheet, growth_row, 2, _geo_get(protocols, "growth protocol"))
    _write_cell(meta_sheet, treatment_row, 2, _geo_get(protocols, "treatment protocol"))
    _write_cell(meta_sheet, extract_row, 2, _geo_get(protocols, "*extract protocol", "extract protocol"))
    _write_cell(meta_sheet, library_row, 2, _geo_get(protocols, "*library construction protocol", "library construction protocol"))

    data_processing_steps: list[Any] = []
    primary_data_processing = _geo_get(protocols, "*data processing step", "data processing step")
    if primary_data_processing not in (None, ""):
        if isinstance(primary_data_processing, list):
            data_processing_steps.extend(primary_data_processing)
        else:
            data_processing_steps.append(primary_data_processing)
    extra_processing = _geo_get(protocols, "data processing step")
    if isinstance(extra_processing, list):
        data_processing_steps.extend([step for step in extra_processing if step not in (None, "")])
    elif extra_processing not in (None, "") and extra_processing != primary_data_processing:
        data_processing_steps.append(extra_processing)

    data_processing_rows_available = 1
    probe_row = base_data_processing_row + 1
    while _normalize_geo_key(meta_sheet.cell(row=probe_row, column=1).value) == _normalize_geo_key("data processing step"):
        data_processing_rows_available += 1
        probe_row += 1

    extra_dp_rows = max(0, len(data_processing_steps) - data_processing_rows_available)
    for i in range(extra_dp_rows):
        insert_at = base_data_processing_row + data_processing_rows_available + i
        meta_sheet.insert_rows(insert_at)
        _copy_row_format(meta_sheet, base_data_processing_row + data_processing_rows_available - 1, insert_at)

    first_dp_label = meta_sheet.cell(row=base_data_processing_row, column=1).value or "*data processing step"
    for idx, step in enumerate(data_processing_steps):
        row_idx = base_data_processing_row + idx
        label = first_dp_label if idx == 0 else "data processing step"
        _write_cell(meta_sheet, row_idx, 1, label)
        _write_cell(meta_sheet, row_idx, 2, step)

    genome_build_row = (_find_first_row_with_label(meta_sheet, "*genome build/assembly") or 67) + extra_dp_rows
    processed_format_row = (_find_first_row_with_label(meta_sheet, "*processed data files format and content") or 68) + extra_dp_rows
    _write_cell(meta_sheet, genome_build_row, 2, _geo_get(protocols, "*genome build/assembly", "genome build/assembly"))
    processed_val = _geo_get(
        protocols,
        "*processed data files format and content",
        "processed data files format and content",
    )
    if isinstance(processed_val, list):
        processed_val = "\n".join([str(v) for v in processed_val if v not in (None, "")])
    _write_cell(meta_sheet, processed_format_row, 2, processed_val)

    # ---- Paired-end experiments ----
    paired_header_row = _find_paired_end_header_row(meta_sheet)
    paired_data_start_row = paired_header_row + 1
    paired_label_row_template = paired_data_start_row

    if len(paired_end_experiments) > 1:
        for i in range(len(paired_end_experiments) - 1):
            insert_at = paired_data_start_row + i + 1
            meta_sheet.insert_rows(insert_at)
            _copy_row_format(meta_sheet, paired_label_row_template, insert_at)

    for idx, entry in enumerate(paired_end_experiments):
        if not isinstance(entry, Mapping):
            continue
        row_idx = paired_data_start_row + idx
        meta_sheet.cell(row=row_idx, column=1).value = entry.get("file name 1")
        meta_sheet.cell(row=row_idx, column=2).value = entry.get("file name 2")
        meta_sheet.cell(row=row_idx, column=3).value = entry.get("file name 3")
        meta_sheet.cell(row=row_idx, column=4).value = entry.get("file name 4")

    # ---- Checksums sheet ----
    checksums_sheet = wb["MD5 Checksums"]
    raw_files = (checksums.get("raw_data_files") or []) if isinstance(checksums, Mapping) else []
    checksums_header_row = _find_first_row_with_label(checksums_sheet, "file name") or 8
    raw_start_row = checksums_header_row + 1
    for idx, item in enumerate(raw_files):
        if not isinstance(item, Mapping):
            continue
        row_idx = raw_start_row + idx
        _write_cell(checksums_sheet, row_idx, 1, item.get("file name"))
        _write_cell(checksums_sheet, row_idx, 2, item.get("file checksum"))

    processed_files = (checksums.get("processed_data_files") or []) if isinstance(checksums, Mapping) else []
    if processed_files:
        processed_section_row = raw_start_row + max(len(raw_files), 1) + 2
        _write_cell(checksums_sheet, processed_section_row, 1, "PROCESSED FILES")
        _write_cell(checksums_sheet, processed_section_row + 1, 1, "file name")
        _write_cell(checksums_sheet, processed_section_row + 1, 2, "file checksum")
        for idx, item in enumerate(processed_files):
            if not isinstance(item, Mapping):
                continue
            row_idx = processed_section_row + 2 + idx
            _write_cell(checksums_sheet, row_idx, 1, item.get("file name"))
            _write_cell(checksums_sheet, row_idx, 2, item.get("file checksum"))


def export_geo_report_to_seq_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str,
    *,
    one_workbook_per_uid: bool = True,
) -> list[str]:
    """
    Convert a GEO report JSON into filled GEO submission Excel workbooks.
    Returns list of output file paths.
    """
    json_path = Path(report_json_path)
    template_path = Path(template_xlsx_path)
    output_root = Path(out_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return []

    template_bytes = template_path.read_bytes()
    output_paths: list[str] = []

    reports: list[tuple[str, Mapping[str, Any]]] = []
    for uid, payload in data.items():
        if not isinstance(payload, Mapping):
            continue
        if (payload.get("report_type") or payload.get("report type") or "").upper() != "GEO":
            continue
        report = payload.get("report") or {}
        if isinstance(report, Mapping):
            reports.append((str(uid), report))

    if not reports:
        return []

    def merge_reports(entries: list[Mapping[str, Any]]) -> Mapping[str, Any]:
        merged: dict[str, Any] = {}
        studies = [r.get("study") for r in entries if isinstance(r, Mapping)]
        if studies:
            merged_study = dict(studies[0] or {})
            for study in studies[1:]:
                if not isinstance(study, Mapping):
                    continue
                for key, val in study.items():
                    if key in {"contributor", "supplementary file"} and isinstance(val, list):
                        existing = merged_study.get(key) or []
                        if not isinstance(existing, list):
                            existing = [existing]
                        merged_study[key] = list(existing) + list(val)
                    elif key not in merged_study or merged_study.get(key) in (None, ""):
                        merged_study[key] = val
            merged["study"] = merged_study

        merged_samples: list[Mapping[str, Any]] = []
        for r in entries:
            if isinstance(r, Mapping) and isinstance(r.get("samples"), list):
                merged_samples.extend(r["samples"])
        if merged_samples:
            merged["samples"] = merged_samples

        merged_protocols: dict[str, Any] = {}
        for r in entries:
            proto = r.get("protocols")
            if not isinstance(proto, Mapping):
                continue
            if not merged_protocols:
                merged_protocols = dict(proto)
                continue
            for key, val in proto.items():
                if key == "data processing step" and isinstance(val, list):
                    existing = merged_protocols.get(key) or []
                    if not isinstance(existing, list):
                        existing = [existing] if existing else []
                    merged_protocols[key] = list(existing) + list(val)
                elif merged_protocols.get(key) in (None, ""):
                    merged_protocols[key] = val
        if merged_protocols:
            merged["protocols"] = merged_protocols

        merged_pairs: list[Mapping[str, Any]] = []
        for r in entries:
            if isinstance(r, Mapping) and isinstance(r.get("paired_end_experiments"), list):
                merged_pairs.extend(r["paired_end_experiments"])
        if merged_pairs:
            merged["paired_end_experiments"] = merged_pairs

        merged_checksums: dict[str, Any] = {}
        for r in entries:
            csum = r.get("checksums")
            if not isinstance(csum, Mapping):
                continue
            if "raw_data_files" in csum and isinstance(csum["raw_data_files"], list):
                existing = merged_checksums.get("raw_data_files") or []
                if not isinstance(existing, list):
                    existing = []
                merged_checksums["raw_data_files"] = existing + list(csum["raw_data_files"])
            for key, val in csum.items():
                if key == "raw_data_files":
                    continue
                if key not in merged_checksums:
                    merged_checksums[key] = val
        if merged_checksums:
            merged["checksums"] = merged_checksums

        return merged

    if one_workbook_per_uid:
        for uid, report in reports:
            wb = load_workbook(BytesIO(template_bytes))
            _populate_geo_seq_workbook(wb, report)
            filename = f"{uid}_GEO_template_filled.xlsx"
            dest = output_root / filename
            wb.save(dest)
            output_paths.append(str(dest))
    else:
        merged_report = merge_reports([r for _, r in reports])
        wb = load_workbook(BytesIO(template_bytes))
        _populate_geo_seq_workbook(wb, merged_report)
        dest = output_root / f"{json_path.stem}_GEO_template_filled.xlsx"
        wb.save(dest)
        output_paths.append(str(dest))

    return output_paths


def _extract_sra_section_reports(
    data: Mapping[str, Any],
    *,
    section_name: str,
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    """Collect per-UID SRA report rows for a given section."""
    reports: list[tuple[str, list[Mapping[str, Any]]]] = []
    for uid, payload in data.items():
        if not isinstance(payload, Mapping):
            continue
        if (payload.get("report_type") or payload.get("report type") or "").upper() != "SRA":
            continue
        report = payload.get("report") or {}
        rows = report.get(section_name) if isinstance(report, Mapping) else None
        if isinstance(rows, list) and rows:
            reports.append((str(uid), [row for row in rows if isinstance(row, Mapping)]))
    return reports


def _worksheet_headers(ws: Worksheet, *, header_row: int) -> list[str]:
    """Read contiguous headers from a worksheet header row."""
    headers: list[str] = []
    col = 1
    while True:
        value = ws.cell(row=header_row, column=col).value
        if value in (None, ""):
            break
        headers.append(str(value))
        col += 1
    return headers


def _write_template_rows(
    wb,
    *,
    sheet_name: str,
    header_row: int,
    template_row: int,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Populate a row-based workbook template from ordered row mappings."""
    ws = wb[sheet_name]
    headers = _worksheet_headers(ws, header_row=header_row)

    if len(rows) > 1:
        for i in range(len(rows) - 1):
            insert_at = template_row + i + 1
            ws.insert_rows(insert_at)
            _copy_row_format(ws, template_row, insert_at)

    for row_idx, row in enumerate(rows, start=template_row):
        for col_idx, header in enumerate(headers, start=1):
            _write_cell(ws, row_idx, col_idx, row.get(header))


def _export_sra_section_to_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str | Path,
    *,
    section_name: str,
    sheet_name: str | None,
    header_row: int,
    template_row: int,
    filename_suffix: str,
    one_workbook_per_uid: bool,
) -> list[str]:
    """Render a row-based SRA report section into workbook copies from a template."""
    json_path = Path(report_json_path)
    template_path = Path(template_xlsx_path)
    output_root = Path(out_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return []

    template_bytes = template_path.read_bytes()
    output_paths: list[str] = []
    reports = _extract_sra_section_reports(data, section_name=section_name)

    if not reports:
        return []

    if one_workbook_per_uid:
        for uid, rows in reports:
            wb = load_workbook(BytesIO(template_bytes))
            target_sheet = sheet_name or wb.sheetnames[0]
            _write_template_rows(
                wb,
                sheet_name=target_sheet,
                header_row=header_row,
                template_row=template_row,
                rows=rows,
            )
            dest = output_root / f"{uid}_{filename_suffix}"
            wb.save(dest)
            output_paths.append(str(dest))
    else:
        merged_rows: list[Mapping[str, Any]] = []
        for _, rows in reports:
            merged_rows.extend(rows)
        if not merged_rows:
            return []
        wb = load_workbook(BytesIO(template_bytes))
        target_sheet = sheet_name or wb.sheetnames[0]
        _write_template_rows(
            wb,
            sheet_name=target_sheet,
            header_row=header_row,
            template_row=template_row,
            rows=merged_rows,
        )
        dest = output_root / f"{json_path.stem}_{filename_suffix}"
        wb.save(dest)
        output_paths.append(str(dest))

    return output_paths


def export_sra_report_to_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str | Path,
    *,
    one_workbook_per_uid: bool = True,
) -> list[str]:
    """
    Convert the SRA libraries report JSON into filled SRA submission workbooks.
    Returns list of output file paths.
    """
    return _export_sra_section_to_xlsx(
        report_json_path,
        template_xlsx_path,
        out_dir,
        section_name="libraries",
        sheet_name="SRA_data",
        header_row=1,
        template_row=2,
        filename_suffix="SRA_metadata_filled.xlsx",
        one_workbook_per_uid=one_workbook_per_uid,
    )


def export_sra_biosample_report_to_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str | Path,
    *,
    one_workbook_per_uid: bool = True,
) -> list[str]:
    """
    Convert the SRA biosamples report JSON into filled BioSample submission workbooks.
    Returns list of output file paths.
    """
    return _export_sra_section_to_xlsx(
        report_json_path,
        template_xlsx_path,
        out_dir,
        section_name="biosamples",
        sheet_name=None,
        header_row=12,
        template_row=13,
        filename_suffix="SRA_biosample_filled.xlsx",
        one_workbook_per_uid=one_workbook_per_uid,
    )


def _coerce_scalar_csv_value(value: Any) -> str:
    """Convert JSON-like values into CSV-safe scalar strings."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            return ";".join("" if item is None else str(item) for item in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _normalize_rows_for_csv(rows: Any) -> list[dict[str, Any]]:
    """Normalize a report section into a list of row dicts suitable for CSV export."""
    if rows is None:
        return []
    if isinstance(rows, Mapping):
        return [dict(rows)]
    if isinstance(rows, list):
        normalized: list[dict[str, Any]] = []
        for item in rows:
            if isinstance(item, Mapping):
                normalized.append(dict(item))
            elif item is not None:
                normalized.append({"value": item})
        return normalized
    return [{"value": rows}]


def _extract_report_section_rows(report: Mapping[str, Any], candidates: Sequence[str]) -> list[dict[str, Any]]:
    """Return the first matching report section that looks like tabular row data."""
    for key in candidates:
        value = report.get(key)
        rows = _normalize_rows_for_csv(value)
        if rows:
            return rows
    return []


def _ordered_csv_columns(rows: Sequence[Mapping[str, Any]], preferred: Sequence[str]) -> list[str]:
    """Build CSV column order with required columns first, then observed extras in row order."""
    columns: list[str] = []
    seen: set[str] = set()
    for col in preferred:
        if col not in seen:
            columns.append(col)
            seen.add(col)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                columns.append(key)
                seen.add(key)
    return columns


def _write_csv_rows(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Write ordered rows to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _coerce_scalar_csv_value(row.get(col)) for col in columns})
    return str(path)



# Moved to helpers_new in Phase 2 — re-exported for backward compat
from .helpers_new.prompts import load_prompt, log_usage, log_prompt  # noqa: E402,F401
from .helpers_new.json_io import _extract_required_paths, estimate_tokens_from_text, safe_parse_json  # noqa: E402,F401
from .helpers_new.text import strip_html, strip_html_recursive, load_file_for_memory, load_json_for_memory  # noqa: E402,F401
from .helpers_new.tools.memory_code import (  # noqa: E402,F401
    _json_type_name,
    _compact_json_value,
    _merge_skeletons,
    _build_skeleton_node,
    _find_record_arrays,
    build_memory_data_profile,
    _build_field_index,
    MemoryCodeSafetyError,
    MemoryCodeTimeoutError,
    _validate_memory_code,
    execute_memory_code,
)
from .helpers_new.results import slim_api_result_for_llm, collect_bundle_files, normalize_api_result_for_memory  # noqa: E402,F401
from .helpers_new.tools.neo4j import tool_neo4j_query  # noqa: E402,F401
from .helpers_new.tools.nextseek_api import (  # noqa: E402,F401
    tool_nextseek_api_request,
    _sanitize_api_row_strings,
    log_api_call,
    fix_sample_endpoint,
    build_recent_results_summary,
    _extract_total_and_rows,
    _should_retry_advanced_search,
    _split_retry_keyword,
    _has_expandable_keyword,
    _advanced_search_retry_attempts,
    _retry_advanced_search_if_empty,
)
from .helpers_new.tools.catalog_match import (  # noqa: E402,F401
    _norm_text,
    _tokenize,
    _doc_from_sampletype,
    _doc_from_assay,
    _score_pair,
    shortlist_catalog,
)
from .helpers_new.dates import (  # noqa: E402,F401
    _normalize_project_id,
    _normalize_years,
    _parse_month,
    _month_range_to_yymmdd_bounds,
    _parse_day,
    _day_range_to_yymmdd_bounds,
)
from .helpers_new.lineage import enumerate_lineage_leaves  # noqa: E402,F401
from .reports_pkg.runners import run_project_sample_report, run_project_protocols_report, run_project_published_report, run_reporter_summary  # noqa: E402,F401
from .reports_pkg.metadata import (  # noqa: E402,F401
    annotate_metadata_with_sampletypes,
    fetch_reporter_metadata,
    _scalar_for_summary,
    _entries_from_metadata,
    build_metadata_summary,
    _is_sequencing_type,
    filter_summary_to_sequencing_lineage,
    build_accession_metadata_lookup,
    filter_summary_for_deg,
)
