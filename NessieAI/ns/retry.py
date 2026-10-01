"""The evaluator's retry engine: normalize a stored run, and run a retry.

``classify_path`` maps an assistant mode and its debug dict to the normalized
``(execution_mode, path_mode, path_subtype)`` routing tuple.
``normalize_from_task`` and ``normalize_from_bundle`` turn a ``QueryTask``, or a
result bundle stored in a session, into an ``EvaluatorRetryContextResponse``,
with the observables ``_build_retry_signals`` extracts. ``_task_has_bundle`` and
``_find_bundle`` are the lookups of the run-list and retry endpoints, and
``run_retry`` is the retry endpoint's pipeline body: it runs the chat_nextseek
orchestrator (plan or standard mode) on the retry's daemon thread.

Moved verbatim from ``nextseek_api/services/evaluator.py`` (Phase B of the
NessieAI consolidation), whose ``EvaluatorViewSet`` imports them back.
``run_retry`` was the ``_run_pipeline`` closure inside ``retry_execute``; it
takes what the closure read as keyword arguments, and its body is the closure
body, only dedented. The ViewSet keeps every HTTP and host seam and hands them
in: source and credential resolution, the new ``QueryTask`` row,
``make_db_event_callback``, the ``DictSessionAdapter`` and the thread start.

The orchestrator is imported only inside ``_get_orchestrator``, so importing
this module does not import ``chat_nextseek``; the evaluator tests stub
``load_prompt`` before ``chat_nextseek.agents`` is first imported, so keep it
lazy, and do not import ``NessieAI.ns.turn`` here (it imports the orchestrator
at module scope). ``chat_nextseek.evaluator.normalization`` holds dict-based
functions of the same names; they are a separate implementation, not a copy of
these.

Nothing here imports ``nextseek_api.services``. The host edges are
``nextseek_api.assistant.models_evaluator`` (the pydantic response models) and
``nextseek_api.assistant.models_db`` (``QueryTask``, to find the task that
produced a bundle), both allowed for this module by
``NessieAI/tests/api/test_nessie_boundaries.py``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from django.conf import settings

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.models_evaluator import (
    EvaluatorLookup,
    EvaluatorRawPayloads,
    EvaluatorRetryContext,
    EvaluatorRetryContextResponse,
    EvaluatorRetrySignals,
    EvaluatorRouting,
    EvaluatorRunMeta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known assistant modes
# ---------------------------------------------------------------------------
_KNOWN_MODES = frozenset({
    "new_search",
    "refine_last_search",
    "graph_query",
    "reporter",
    "system_question",
    "ask_about_last_results",
    "plan",
})


# ---------------------------------------------------------------------------
# Helper: classify_path
# ---------------------------------------------------------------------------

def classify_path(
    mode: Optional[str],
    debug: Optional[Dict[str, Any]],
) -> Tuple[str, str, Optional[str]]:
    """Classify an assistant mode + debug dict into a normalized routing tuple.

    Returns:
        (execution_mode, path_mode, path_subtype)
    """
    debug = debug or {}

    if not mode or mode not in _KNOWN_MODES:
        return ("standard", "unsupported", None)

    # Plan mode has its own execution_mode
    if mode == "plan":
        return ("plan", "plan", None)

    # Reporter: check for subtype via debug.reporter_plan.reporter_mode
    # Production bundles use "parser_plan" instead of "debug.reporter_plan"
    path_subtype: Optional[str] = None
    if mode == "reporter":
        reporter_plan = debug.get("reporter_plan") or debug.get("parser_plan") or {}
        reporter_mode = reporter_plan.get("reporter_mode")
        if reporter_mode:
            path_subtype = f"reporter.{reporter_mode}"

    return ("standard", mode, path_subtype)


# ---------------------------------------------------------------------------
# Helper: _build_retry_signals
# ---------------------------------------------------------------------------

def _build_retry_signals(
    task_status: Optional[str],
    result: Optional[Dict[str, Any]],
    bundle: Optional[Dict[str, Any]],
    session: Optional[ChatSession],
) -> EvaluatorRetrySignals:
    """Extract normalized observables from run state for the evaluator client."""
    mode = bundle.get("mode") if bundle else None

    # Task-level
    query_error_present = bool(result.get("error")) if result else False
    raw_error = result.get("error") if result else None
    raw_error_excerpt = str(raw_error)[:200] if raw_error else None

    # Defaults
    api_ok = None
    api_status_code = None
    rows_returned = None
    graph_ok = None
    has_artifacts = False
    plan_steps_total = None
    plan_steps_executed = None
    plan_steps_ok = None
    plan_steps_failed = None
    plan_stop_reason = None

    if bundle:
        if mode in ("new_search", "refine_last_search"):
            api_full = bundle.get("api_result_full") or {}
            api_slim = bundle.get("api_result_slim") or {}
            api_ok = api_full.get("ok")
            api_status_code = api_full.get("status_code")
            full_data = api_full.get("data") or {}
            slim_data = api_slim.get("data") or {}
            rows_returned = (
                full_data.get("total")
                or slim_data.get("total")
                or full_data.get("count")
            )

        elif mode == "graph_query":
            graph_result = bundle.get("graph_result") or {}
            graph_ok = graph_result.get("ok")
            rows_returned = graph_result.get("count")

        elif mode == "reporter":
            reporter_result = bundle.get("reporter_result") or {}
            api_ok = reporter_result.get("ok")
            rows_returned = reporter_result.get("rows_returned")

        elif mode == "plan":
            step_results = bundle.get("step_results") or {}
            planned_steps = (bundle.get("plan") or {}).get("steps") or []
            plan_steps_total = len(planned_steps)
            plan_steps_executed = len(step_results)
            plan_steps_ok = sum(1 for s in step_results.values() if s.get("ok"))
            plan_steps_failed = sum(
                1 for s in step_results.values() if not s.get("ok")
            )
            if plan_steps_executed < plan_steps_total:
                sorted_ids = sorted(step_results.keys())
                last_id = sorted_ids[-1] if sorted_ids else None
                if last_id and not step_results[last_id].get("ok"):
                    plan_stop_reason = step_results[last_id].get(
                        "error", "step_failed"
                    )
                else:
                    plan_stop_reason = "incomplete"

        has_artifacts = bool(
            bundle.get("report_saved_files") or bundle.get("files")
        )

    has_prior_context = (
        len(session.results_history) > 1
        if session and session.results_history
        else False
    )

    return EvaluatorRetrySignals(
        assistant_status=task_status,
        query_error_present=query_error_present,
        raw_error_excerpt=raw_error_excerpt,
        bundle_present=bundle is not None,
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
        has_prior_context=has_prior_context,
    )


# ---------------------------------------------------------------------------
# Helper: _task_has_bundle
# ---------------------------------------------------------------------------

def _task_has_bundle(task: QueryTask) -> bool:
    """Return True if the task's result contains a non-None bundle_id."""
    if task.result is None:
        return False
    return task.result.get("bundle_id") is not None


# ---------------------------------------------------------------------------
# Helper: normalize_from_task
# ---------------------------------------------------------------------------

def normalize_from_task(task: QueryTask) -> EvaluatorRetryContextResponse:
    """Normalize a QueryTask into the unified evaluator response.

    Expects ``task.session`` to be loaded (e.g. via ``select_related``).
    """
    session = task.session
    result = task.result or {}
    bundle_id = result.get("bundle_id")
    reply = result.get("reply")

    # Try to find the bundle in session history
    bundle: Optional[Dict[str, Any]] = None
    if bundle_id is not None:
        history = session.results_history or []
        bundle = next((b for b in history if b.get("id") == bundle_id), None)

    # Classify path from bundle mode/debug (or unsupported if no bundle)
    # Production bundles use "parser_plan" instead of "debug"
    if bundle is not None:
        mode = bundle.get("mode")
        debug = bundle.get("debug") or bundle.get("parser_plan") or {}
    else:
        mode = None
        debug = {}

    execution_mode, path_mode, path_subtype = classify_path(mode, debug)

    # Retryable: completed + recognized path_mode
    retryable = (
        task.status == "completed"
        and path_mode != "unsupported"
    )

    retry_signals = _build_retry_signals(task.status, result, bundle, session)

    return EvaluatorRetryContextResponse(
        lookup=EvaluatorLookup(
            task_id=task.task_id,
            session_id=session.session_id,
            bundle_id=bundle_id,
            source="task",
        ),
        run=EvaluatorRunMeta(
            status=task.status,
            query=task.query,
            reply=reply,
            created_at=task.created_at,
            user_id=task.user_id,
        ),
        routing=EvaluatorRouting(
            execution_mode=execution_mode,
            path_mode=path_mode,
            path_subtype=path_subtype,
        ),
        retry_context=EvaluatorRetryContext(
            retryable=retryable,
            retry_signals=retry_signals,
            assistant_context=None,
        ),
        raw=EvaluatorRawPayloads(
            task_progress=task.progress if task.progress else None,
            task_result=task.result,
            bundle=bundle,
            last_debug=session.last_debug or None,
        ),
    )


# ---------------------------------------------------------------------------
# Helper: normalize_from_bundle
# ---------------------------------------------------------------------------

def normalize_from_bundle(
    session: ChatSession,
    bundle: Dict[str, Any],
) -> EvaluatorRetryContextResponse:
    """Normalize a historical bundle into the unified evaluator response.

    Cross-references the QueryTask that produced this bundle to recover
    the LLM reply, which is stored in task.result, not in the bundle.
    """
    mode = bundle.get("mode")
    # Production bundles may use "parser_plan" instead of "debug"
    debug = bundle.get("debug") or bundle.get("parser_plan") or {}
    # Production bundles use "user_query" instead of "query"
    query = bundle.get("query") or bundle.get("user_query")
    bundle_id = bundle.get("id")

    # Cross-reference: find the QueryTask that produced this bundle
    # to recover the LLM reply (stored in task.result, not in bundle)
    reply = bundle.get("reply")  # try bundle first
    linked_task = None
    task_result = None
    task_progress = None
    if bundle_id is not None and not reply:
        linked_task = (
            QueryTask.objects
            .filter(session=session, result__bundle_id=bundle_id)
            .order_by("-created_at")
            .first()
        )
        if linked_task and linked_task.result:
            reply = linked_task.result.get("reply")
            task_result = linked_task.result
            task_progress = linked_task.progress if linked_task.progress else None

    execution_mode, path_mode, path_subtype = classify_path(mode, debug)

    # Bundle-based: always "completed" status, retryable if recognized path
    retryable = path_mode != "unsupported"

    retry_signals = _build_retry_signals(
        "completed",
        linked_task.result if linked_task else None,
        bundle,
        session,
    )

    return EvaluatorRetryContextResponse(
        lookup=EvaluatorLookup(
            task_id=linked_task.task_id if linked_task else None,
            session_id=session.session_id,
            bundle_id=bundle_id,
            source="bundle",
        ),
        run=EvaluatorRunMeta(
            status="completed",
            query=query,
            reply=reply,
            created_at=linked_task.created_at if linked_task else session.created_at,
            user_id=session.user_id,
        ),
        routing=EvaluatorRouting(
            execution_mode=execution_mode,
            path_mode=path_mode,
            path_subtype=path_subtype,
        ),
        retry_context=EvaluatorRetryContext(
            retryable=retryable,
            retry_signals=retry_signals,
            assistant_context=None,
        ),
        raw=EvaluatorRawPayloads(
            task_progress=task_progress,
            task_result=task_result,
            bundle=bundle,
            last_debug=session.last_debug or None,
        ),
    )


# ---------------------------------------------------------------------------
# Helper: _get_orchestrator (lazy import)
# ---------------------------------------------------------------------------

def _get_orchestrator():
    """Lazy-import orchestrator functions to avoid import-time side effects."""
    from chat_nextseek.orchestrator import run_query, run_query_plan
    return run_query, run_query_plan


# ---------------------------------------------------------------------------
# Helper: _find_bundle
# ---------------------------------------------------------------------------

def _find_bundle(session: ChatSession, bundle_id: int) -> Optional[Dict[str, Any]]:
    """Find a bundle by id in the session's results_history."""
    history = session.results_history or []
    return next((b for b in history if b.get("id") == bundle_id), None)


# ---------------------------------------------------------------------------
# run_retry: the retry endpoint's pipeline body
# ---------------------------------------------------------------------------

def run_retry(*, adapter, req, send_event, api_user, api_pass, session_id_str, graph_scope=None) -> None:
    """Pipeline body of the evaluator ``retry`` endpoint, run on its daemon thread.

    Runs the orchestrator for ``req.mode`` (``plan``, else standard) with the
    caller's credentials, turns an unhandled error into a ``query_error``
    event, and always saves the session through ``adapter``. Progress reaches
    the client only through ``send_event``. ``graph_scope`` is the caller's
    project scope as plain data, resolved by the ViewSet; ``None`` leaves the
    keyword out, and the singleton config carries no scope, so every graph
    query refuses.
    """
    try:
        run_query, run_query_plan = _get_orchestrator()
        creds = {"api_user": api_user, "api_pass": api_pass}
        scope_kw = {} if graph_scope is None else {"graph_scope": graph_scope}
        if req.mode == "plan":
            run_query_plan(
                adapter,
                settings.NEXTSEEK_CHAT_CONFIG,
                req.query,
                send_event,
                credentials=creds,
                **scope_kw,
            )
        else:
            run_query(
                adapter,
                settings.NEXTSEEK_CHAT_CONFIG,
                req.query,
                send_event,
                credentials=creds,
                **scope_kw,
            )
    except Exception:
        logger.exception(
            "Unhandled pipeline error (evaluator retry)"
        )
        send_event(
            "query_error",
            {
                "error": "Internal pipeline error",
                "agent": "unknown",
                "session_id": session_id_str,
            },
        )
    finally:
        adapter.save()
