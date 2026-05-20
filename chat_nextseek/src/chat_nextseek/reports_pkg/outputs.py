"""Report output writers: ``persist_report_file`` and the
``generate_report_outputs`` orchestrator that wires together metadata,
protocol, samplesheet, and exporter steps.

Moved out of ``helpers.py`` during Phase 2 of the src/ restructure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..artifacts import ArtifactStore
from ..config import ChatConfig
from ..schemas import ReportWriterPlan
from .metadata import (
    annotate_metadata_with_sampletypes,
    build_metadata_summary,
    fetch_reporter_metadata,
    filter_summary_for_deg,
)
from .nfcore import _handle_nfcore_artifacts
from .protocols import (
    download_and_extract_protocol_blobs,
    extract_protocol_refs_from_metadata,
    fetch_protocols,
    sanitize_protocols_for_llm,
)

# Symbols that are still in helpers.py at the moment of this move (Tasks 2.18-2.20
# relocate them). Imported via the helpers forwarder so the forward references
# resolve at call time once those moves land.
from ..helpers import (
    export_geo_report_to_seq_xlsx,
    export_sra_biosample_report_to_xlsx,
    export_sra_report_to_xlsx,
    load_report_template,
    nfcore_pipeline_from_report_type,
    normalize_report_type,
)


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
            from ..seqera import (
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
