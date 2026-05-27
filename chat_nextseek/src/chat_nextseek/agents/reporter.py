from __future__ import annotations

import json
import re
import calendar
from datetime import datetime, timedelta
from typing import Any

from ..config import ChatConfig
from ..helpers import (
    normalize_report_type,
)
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    ParserPlan,
    ReporterPlan,
    ReportWriterOutput,
    ReportWriterPlan,
)


def reporter_agent(config: ChatConfig, user_query: str, parser_plan: ParserPlan | dict | None = None) -> ReporterPlan:
    """
    Map a user query to reporting inputs for run_project_sample_report.
    Detects relative date hints, folds in parser context, and fills missing mode/type defaults.
    Returns a structured ReporterPlan that gracefully handles parse failures.
    """
    now = datetime.now()
    parser_context = parser_plan.model_dump() if isinstance(parser_plan, ParserPlan) else parser_plan or {}
    suggested_report_mode = parser_context.get("report_mode") if isinstance(parser_context, dict) else None
    suggested_report_type = parser_context.get("report_type") if isinstance(parser_context, dict) else None
    valid_reporter_modes = {"summary", "summary_sql", "report_generation"}

    def _normalize_reporter_mode(value: str | None) -> str | None:
        if isinstance(value, str) and value in valid_reporter_modes:
            return value
        return None

    suggested_report_mode = _normalize_reporter_mode(suggested_report_mode)
    suggested_report_type = normalize_report_type(suggested_report_type)

    def _month_bounds(dt: datetime) -> tuple[str, str]:
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        start = dt.replace(day=1).date().isoformat()
        end = dt.replace(day=last_day).date().isoformat()
        return start, end

    def _previous_month_bounds(dt: datetime) -> tuple[str, str]:
        first_of_month = dt.replace(day=1)
        end_prev = first_of_month - timedelta(days=1)
        start_prev, end_prev_month = _month_bounds(end_prev)
        return start_prev, end_prev_month

    def _week_bounds(dt: datetime) -> tuple[str, str]:
        # ISO weeks start on Monday
        start = (dt - timedelta(days=dt.weekday())).date()
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    def _detect_relative_date_hint(text: str, anchor: datetime) -> dict:
        lowered = text.lower()
        hint = {"day_range": None, "month_range": None, "years": [], "reason": ""}

        if "yesterday" in lowered:
            target = (anchor - timedelta(days=1)).date().isoformat()
            hint.update({"day_range": [target, target], "reason": "yesterday relative to today"})
            return hint

        if "today" in lowered or "right now" in lowered:
            target = anchor.date().isoformat()
            hint.update({"day_range": [target, target], "reason": "today's date"})
            return hint

        match_last_ndays = re.search(r"last\s+(\d+)\s+days", lowered) or re.search(
            r"past\s+(\d+)\s+days", lowered
        )
        if match_last_ndays:
            days = int(match_last_ndays.group(1))
            start = (anchor - timedelta(days=days - 1)).date().isoformat()
            end = anchor.date().isoformat()
            hint.update({"day_range": [start, end], "reason": f"last {days} days"})
            return hint

        if "last week" in lowered or "previous week" in lowered:
            start_current, _ = _week_bounds(anchor)
            start_prev = datetime.fromisoformat(start_current) - timedelta(days=7)
            start, end = _week_bounds(start_prev)
            hint.update({"day_range": [start, end], "reason": "previous calendar week"})
            return hint

        if "this week" in lowered:
            start, end = _week_bounds(anchor)
            hint.update({"day_range": [start, end], "reason": "current calendar week"})
            return hint

        if "last month" in lowered or "previous month" in lowered:
            start, end = _previous_month_bounds(anchor)
            hint.update({"day_range": [start, end], "reason": "previous calendar month"})
            return hint

        if "this month" in lowered or "current month" in lowered:
            start, end = _month_bounds(anchor)
            hint.update({"day_range": [start, end], "reason": "current calendar month"})
            return hint

        if "last year" in lowered or "previous year" in lowered:
            hint.update({"years": [anchor.year - 1], "reason": "previous calendar year"})
            return hint

        if "this year" in lowered or "current year" in lowered:
            hint.update({"years": [anchor.year], "reason": "current calendar year"})
            return hint

        return hint

    relative_hint = _detect_relative_date_hint(user_query, now)
    date_context = {
        "current_datetime": now.isoformat(),
        "today": now.date().isoformat(),
        "yesterday": (now - timedelta(days=1)).date().isoformat(),
        "this_month_range": _month_bounds(now),
        "last_month_range": _previous_month_bounds(now),
        "this_week_range": _week_bounds(now),
    }

    messages = [
        {"role": "system", "content": config.REPORTER_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Parser context:\n"
                f"{json.dumps(parser_context, indent=2)}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Current date/time context for resolving relative phrases:\n"
                f"{json.dumps(date_context, indent=2)}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Auto-detected relative date hint from the user query (use only if helpful):\n"
                f"{json.dumps(relative_hint, indent=2)}"
            ),
        },
        {"role": "user", "content": user_query},
    ]

    reporter_client, reporter_model, reporter_budget = config.get_agent_model("reporter")
    try:
        plan_model = call_llm_structured(
            config=config,
            prompt=user_query,
            model=ReporterPlan,
            system=config.REPORTER_SYSTEM_PROMPT,
            messages=messages,
            model_name=reporter_model,
            temperature=0,
            response_format={"type": "json_object"},
            thinking_budget=reporter_budget,
            log_label="reporter",
            log_payload_extra={"user_query": user_query},
            usage_label="REPORTER",
            client=reporter_client,
        )
    except Exception as e:
        print("[DEBUG][REPORTER] Exception or parse error:", repr(e))
        fallback_uids: list[str] = []
        if isinstance(parser_context, dict):
            filters = parser_context.get("filters") or {}
            if isinstance(filters, dict):
                parser_uids = filters.get("uids") or []
                if isinstance(parser_uids, list):
                    fallback_uids = [uid for uid in parser_uids if isinstance(uid, str) and uid.strip()]

        plan_model = ReporterPlan(
            reporter_mode=suggested_report_mode,
            report_type=suggested_report_type,
            uids=fallback_uids,
            notes="Reporter could not produce a structured plan.",
        )

    # If the LLM did not set date filters but we detected a clear relative range, fill it in.
    updates: dict = {}
    if relative_hint.get("day_range") and not plan_model.day_range:
        updates["day_range"] = relative_hint["day_range"]
        updates.setdefault("month_range", None)
        updates.setdefault("years", [])
    elif relative_hint.get("month_range") and not plan_model.month_range and not plan_model.day_range:
        updates["month_range"] = relative_hint["month_range"]
        updates.setdefault("years", [])
    elif relative_hint.get("years") and relative_hint["years"] and not plan_model.years:
        updates["years"] = relative_hint["years"]

    if updates:
        note_prefix = plan_model.notes + " | " if plan_model.notes else ""
        updates["notes"] = note_prefix + (relative_hint.get("reason") or "Applied relative date hint")
        plan_model = plan_model.model_copy(update=updates)

    normalized_reporter_mode = _normalize_reporter_mode(getattr(plan_model, "reporter_mode", None))
    if normalized_reporter_mode != getattr(plan_model, "reporter_mode", None):
        plan_model = plan_model.model_copy(update={"reporter_mode": normalized_reporter_mode})

    # Apply parser hints for report mode/type if missing
    if not getattr(plan_model, "reporter_mode", None):
        plan_model = plan_model.model_copy(update={"reporter_mode": suggested_report_mode or "summary"})
    normalized_report_type = normalize_report_type(getattr(plan_model, "report_type", None))
    if normalized_report_type != getattr(plan_model, "report_type", None):
        plan_model = plan_model.model_copy(update={"report_type": normalized_report_type})
    if not getattr(plan_model, "report_type", None) and suggested_report_type:
        plan_model = plan_model.model_copy(update={"report_type": suggested_report_type})
    if not plan_model.uids and isinstance(parser_context, dict):
        filters = parser_context.get("filters") or {}
        if isinstance(filters, dict):
            parser_uids = filters.get("uids") or []
            if parser_uids:
                plan_model = plan_model.model_copy(update={"uids": parser_uids})

    print("[DEBUG][REPORTER] Parsed reporter plan:", json.dumps(plan_model.model_dump(), indent=2))
    return plan_model

def report_writer_agent(
    config: ChatConfig,
    user_query: str,
    plan: ReportWriterPlan,
    template: dict | None = None,
) -> ReportWriterOutput:
    """
    Generate a repository-style report JSON using reporter_context and a type-specific template.
    Selects a specialized output model (e.g., GEO) when needed and logs the structured response.
    Falls back to a minimal output with notes if structured parsing fails.
    """
    template = template or {}
    canonical_report_type = normalize_report_type(plan.report_type)
    model_cls = ReportWriterOutput
    if canonical_report_type == "GEO":
        from ..schemas import ReportWriterOutputGEO
        model_cls = ReportWriterOutputGEO
    messages = [
        {"role": "system", "content": config.REPORT_WRITER_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                f"Report type: {canonical_report_type or 'unknown'}\n"
                "Report template JSON (may be empty if unavailable):\n"
                f"{json.dumps(template, indent=2)}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Reporter context JSON (metadata to use; do NOT fetch anything new):\n"
                f"{json.dumps(plan.reporter_context or {}, indent=2)}"
            ),
        },
    ]
    if canonical_report_type and canonical_report_type.startswith("NFCORE"):
        ctx = plan.reporter_context or {}
        passthrough = template.get("pipeline_optional_columns_passthrough") or []
        cohorts_ctx = ctx.get("nfcore_cohorts") or []
        chosen_fields = sorted({
            f for c in cohorts_ctx for f in (c.get("enrichment_metadata_fields") or [])
        })
        cohort_summary_text = "; ".join(
            f"{c.get('label')}={c.get('pipeline')} (criterion={c.get('cohort_criterion') or {}})"
            for c in cohorts_ctx
        ) or "(single default cohort)"
        nfcore_note = (
            "NFCORE SAMPLESHEET RULES:\n"
            "\n"
            "DIVISION OF LABOR — READ FIRST:\n"
            "- You produce `report.samplesheet`: a list of row dicts (ONE FLAT LIST covering ALL samples).\n"
            "- The DOWNSTREAM EMITTER rewrites `fastq_1` / `fastq_2` to ENA HTTPS URLs "
            "for every row that has an `accession` key, stamps enrichment columns from the "
            "actual metadata, and partitions rows into per-cohort samplesheets based on "
            "the cohort criterion. So:\n"
            "  - You MUST set `accession` to the most specific run accession (SRR/ERR/DRR) "
            "for each sample, taken from the sample's metadata (fields like SRA_accession, "
            "Run_accession, sra_run, or any 'SRR…' string in the metadata).\n"
            "  - Leave `fastq_1` and `fastq_2` empty — they WILL be overwritten. Do NOT use "
            "`File_PrimaryData`, `File_SecondaryData`, or any local filename for fastq "
            "columns — those are not the actual FASTQs and will be discarded.\n"
            "  - If a sample has no run accession in its metadata, OMIT the row entirely.\n"
            "\n"
            f"COHORT PARTITIONING (downstream): {cohort_summary_text}\n"
            "- Do NOT filter rows yourself — emit ONE row per biological sample with a real "
            "accession, regardless of which cohort it belongs to. The emitter applies each "
            "cohort's criterion to partition rows into separate samplesheets.\n"
            "\n"
            "ROW SELECTION:\n"
            "- One row per biological sample that has a real run accession.\n"
            "- Same `sample` value across rows tells nf-core to concatenate them.\n"
            "- The emitter automatically expands accessions that resolve to multiple runs.\n"
            "\n"
            "REQUIRED COLUMNS (must appear on every row, even when blank): see "
            "template.pipeline.required_columns.\n"
            "\n"
            f"ENRICHMENT COLUMNS (across all cohorts): {chosen_fields or '(none — emit only required columns + accession)'}.\n"
            "- These were chosen by the user during the nf-core wizard. The emitter stamps "
            "values authoritatively from the metadata, so do not feel obligated to populate "
            "them per row — but DO include them in your row keys so the column appears.\n"
            "- Use EXACT field names from the list.\n"
            "\n"
            f"PIPELINE OPTIONAL COLUMNS (include only when clearly supported by metadata): {passthrough or '(none)'}.\n"
        )
        messages.append({"role": "system", "content": nfcore_note})
    messages.append({"role": "user", "content": user_query})

    writer_client, writer_model, writer_budget = config.get_agent_model("report_writer")
    try:
        result = call_llm_structured(
            config=config,
            prompt="Write the structured report JSON using the template and reporter_context.",
            model=model_cls,
            system=config.REPORT_WRITER_SYSTEM_PROMPT,
            messages=messages,
            model_name=writer_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label="report_writer",
            log_payload_extra={"user_query": user_query, "report_type": canonical_report_type},
            usage_label="REPORT_WRITER",
            timeout_seconds=600,
            thinking_budget=writer_budget,
            client=writer_client,
        )
    except Exception as e:
        print("[DEBUG][REPORT_WRITER] Exception or parse error:", repr(e))
        result = model_cls(
            report_type=canonical_report_type,
            report=getattr(model_cls, "report", {}) or {},
            narrative=None,
            notes="Report writer could not produce structured output.",
        )

    print("[DEBUG][REPORT_WRITER] Parsed report writer output:", json.dumps(result.model_dump(), indent=2))
    return result
