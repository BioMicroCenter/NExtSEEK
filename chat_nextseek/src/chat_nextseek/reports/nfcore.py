"""Glue for emitting nf-core samplesheet artifacts from reporter output.

Cohort filtering, samplesheet row extraction, the per-cohort launch plan
fan-out, and combined launch.yml writer live here.

Moved out of ``helpers.py`` during Phase 2 of the src/ restructure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ChatConfig
from .metadata import build_accession_metadata_lookup


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
    from ..agents import seqera_agent  # local import to avoid cycle
    from ..seqera import emit_nfcore_artifacts, submit_launch, write_combined_launch_yml

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
