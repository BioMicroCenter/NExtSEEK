"""Normalize orchestrator outputs for evaluator consumption.

Transforms bundles or persisted run records into ``EvaluatorRetryContextResponse``
with stable routing and retry-signal fields.
Use this module before evaluation whenever the source is raw session state.
Invariant: unknown or missing parser modes become ``path_mode == "unsupported"``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .models import (
    EvaluatorLookup,
    EvaluatorRawPayloads,
    EvaluatorRetryContext,
    EvaluatorRetryContextResponse,
    EvaluatorRetrySignals,
    EvaluatorRouting,
    EvaluatorRunMeta,
    EvaluatorRunRecord,
)


_KNOWN_MODES = {
    "new_search",
    "refine_last_search",
    "graph_query",
    "reporter",
    "system_question",
    "ask_about_last_results",
    "plan",
}


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _extract_rows_returned(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, Mapping):
        return None
    for key in ("total", "count", "rows_returned", "num_rows"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
    for key in ("results", "items", "data", "reports"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        return _extract_rows_returned(metadata)
    return None


def _extract_reply(bundle: Mapping[str, Any], result: Mapping[str, Any] | None = None) -> str | None:
    for key in ("reply", "provisional_reply", "answer", "narrative"):
        value = bundle.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(result, Mapping):
        for key in ("reply", "answer", "summary"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def classify_path(mode: str | None, debug: dict[str, Any] | None) -> tuple[str, str, str | None]:
    debug = debug or {}
    if not mode or mode not in _KNOWN_MODES:
        return ("standard", "unsupported", None)
    if mode == "plan":
        return ("plan", "plan", None)
    if mode == "reporter":
        reporter_plan = _as_dict(debug.get("reporter_plan")) or _as_dict(debug.get("parser_plan"))
        reporter_mode = reporter_plan.get("reporter_mode") or reporter_plan.get("summary_mode")
        subtype = f"reporter.{reporter_mode}" if reporter_mode else None
        return ("standard", "reporter", subtype)
    return ("standard", mode, None)


def _build_retry_signals(
    task_status: str | None,
    result: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    session: dict[str, Any] | None,
) -> EvaluatorRetrySignals:
    result = result or {}
    bundle = bundle or {}
    session = session or {}
    mode = bundle.get("mode")
    api_ok = None
    api_status_code = None
    rows_returned = None
    graph_ok = None
    plan_steps_total = None
    plan_steps_executed = None
    plan_steps_ok = None
    plan_steps_failed = None
    plan_stop_reason = None
    raw_error = result.get("error")

    if mode in {"new_search", "refine_last_search"}:
        api_full = _as_dict(bundle.get("api_result_full"))
        api_ok = api_full.get("ok")
        api_status_code = api_full.get("status_code")
        rows_returned = _extract_rows_returned(api_full.get("data"))
        raw_error = raw_error or api_full.get("error")
    elif mode == "graph_query":
        graph_result = _as_dict(bundle.get("graph_result"))
        graph_ok = graph_result.get("ok")
        rows_returned = _extract_rows_returned(graph_result)
        raw_error = raw_error or graph_result.get("error")
    elif mode == "reporter":
        reporter_result = _as_dict(bundle.get("reporter_result"))
        api_ok = reporter_result.get("ok")
        rows_returned = _extract_rows_returned(reporter_result)
        metadata = _as_dict(reporter_result.get("metadata"))
        if rows_returned is None and metadata:
            rows_returned = _extract_rows_returned(metadata)
        raw_error = raw_error or reporter_result.get("error")
    elif mode == "plan":
        step_results = _as_dict(bundle.get("step_results"))
        plan_steps_total = len((_as_dict(bundle.get("plan")).get("steps") or []))
        plan_steps_executed = len(step_results)
        plan_steps_ok = sum(1 for value in step_results.values() if _as_dict(value).get("ok") is True)
        plan_steps_failed = sum(1 for value in step_results.values() if _as_dict(value).get("ok") is False)
        plan_stop_reason = bundle.get("stop_reason")
        if not plan_stop_reason:
            for step in step_results.values():
                step_dict = _as_dict(step)
                if step_dict.get("ok") is False:
                    plan_stop_reason = step_dict.get("error") or step_dict.get("stop_reason") or "step_failed"
        raw_error = raw_error or plan_stop_reason

    has_artifacts = bool(
        bundle.get("report_saved_files")
        or bundle.get("files")
        or bundle.get("raw_result_path")
        or bundle.get("raw_result_paths")
        or bundle.get("graph_debug_path")
        or bundle.get("graph_debug_paths")
        or bundle.get("plan_debug_path")
    )

    return EvaluatorRetrySignals(
        assistant_status=task_status,
        query_error_present=bool(raw_error),
        raw_error_excerpt=str(raw_error)[:200] if raw_error else None,
        bundle_present=bool(bundle),
        path_mode=mode,
        api_ok=api_ok,
        api_status_code=api_status_code,
        rows_returned=rows_returned,
        graph_ok=graph_ok,
        has_artifacts=has_artifacts,
        plan_steps_total=plan_steps_total,
        plan_steps_executed=plan_steps_executed,
        plan_steps_ok=plan_steps_ok,
        plan_steps_failed=plan_steps_failed,
        plan_stop_reason=plan_stop_reason,
        has_prior_context=len(session.get("results_history") or []) > 1,
    )


def build_retry_context(
    *,
    task_status: str | None,
    result: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    session: dict[str, Any] | None,
    retryable: bool,
    assistant_context: str | None = None,
) -> EvaluatorRetryContext:
    return EvaluatorRetryContext(
        retryable=retryable,
        retry_signals=_build_retry_signals(task_status, result, bundle, session),
        assistant_context=assistant_context,
    )


def normalize_from_bundle(session: dict[str, Any], bundle: dict[str, Any]) -> EvaluatorRetryContextResponse:
    session = session or {}
    bundle = bundle or {}
    mode = bundle.get("mode")
    debug = _as_dict(session.get("last_debug"))
    if mode == "reporter":
        debug = {
            **debug,
            "reporter_plan": bundle.get("reporter_plan") or debug.get("reporter_plan"),
        }
    execution_mode, path_mode, path_subtype = classify_path(mode, debug)
    retryable = path_mode not in {"unsupported", "system_question", "ask_about_last_results"}
    return EvaluatorRetryContextResponse(
        lookup=EvaluatorLookup(
            session_id=_coerce_uuid(session.get("session_id")),
            bundle_id=bundle.get("id"),
            source="bundle",
        ),
        run=EvaluatorRunMeta(
            status="completed",
            query=bundle.get("query") or bundle.get("user_query"),
            reply=_extract_reply(bundle),
            user_id=session.get("user_id"),
        ),
        routing=EvaluatorRouting(
            execution_mode=execution_mode,
            path_mode=path_mode,
            path_subtype=path_subtype,
        ),
        retry_context=build_retry_context(
            task_status="completed",
            result=None,
            bundle=bundle,
            session=session,
            retryable=retryable,
        ),
        raw=EvaluatorRawPayloads(
            bundle=bundle,
            last_debug=_as_dict(session.get("last_debug")) or None,
        ),
    )


def normalize_from_task(
    task: EvaluatorRunRecord | dict[str, Any],
    *,
    session: dict[str, Any] | None = None,
    bundle: dict[str, Any] | None = None,
) -> EvaluatorRetryContextResponse:
    record = task if isinstance(task, EvaluatorRunRecord) else EvaluatorRunRecord.model_validate(task)
    if record.normalized_payload:
        restored = EvaluatorRetryContextResponse.model_validate(record.normalized_payload)
        return restored.model_copy(
            update={
                "lookup": restored.lookup.model_copy(
                    update={
                        "task_id": record.task_id,
                        "session_id": record.source_session_id or restored.lookup.session_id,
                        "bundle_id": record.source_bundle_id if record.source_bundle_id is not None else restored.lookup.bundle_id,
                        "source": "task",
                    }
                ),
                "run": restored.run.model_copy(
                    update={
                        "status": record.status,
                        "query": record.query,
                        "reply": record.reply or restored.run.reply,
                        "user_id": record.user_id,
                    }
                ),
            }
        )

    session = session or {}
    bundle = bundle or {}
    if bundle:
        normalized = normalize_from_bundle(session, bundle)
        return normalized.model_copy(
            update={
                "lookup": normalized.lookup.model_copy(
                    update={
                        "task_id": record.task_id,
                        "source": "task",
                    }
                ),
                "run": normalized.run.model_copy(
                    update={
                        "status": record.status,
                        "query": record.query,
                        "reply": record.reply or normalized.run.reply,
                        "user_id": record.user_id,
                    }
                ),
            }
        )

    execution_mode, path_mode, path_subtype = classify_path(
        record.mode if record.mode == "plan" else None,
        None,
    )
    if record.mode == "standard":
        execution_mode = "standard"
        path_mode = "unsupported"
        path_subtype = None
    retryable = path_mode not in {"unsupported", "system_question", "ask_about_last_results"}
    return EvaluatorRetryContextResponse(
        lookup=EvaluatorLookup(
            task_id=record.task_id,
            session_id=record.source_session_id or record.session_id,
            bundle_id=record.source_bundle_id,
            source="task",
        ),
        run=EvaluatorRunMeta(
            status=record.status,
            query=record.query,
            reply=record.reply,
            user_id=record.user_id,
        ),
        routing=EvaluatorRouting(
            execution_mode=execution_mode,
            path_mode=path_mode,
            path_subtype=path_subtype,
        ),
        retry_context=build_retry_context(
            task_status=record.status,
            result={"error": record.error} if record.error else None,
            bundle=bundle,
            session=session,
            retryable=retryable,
        ),
        raw=EvaluatorRawPayloads(
            task_result=record.model_dump(mode="json"),
            bundle=bundle or None,
            last_debug=_as_dict(session.get("last_debug")) or None,
        ),
    )
