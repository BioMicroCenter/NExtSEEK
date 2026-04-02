"""
DRF ViewSet for the NExtSEEK Evaluator endpoints.

Provides two read-only normalization endpoints:
  GET /evaluator/tasks/{task_id}/retry-context/
  GET /evaluator/sessions/{session_id}/bundles/{bundle_id}/retry-context/

Plus helper functions:
  classify_path()         — mode + debug → (execution_mode, path_mode, path_subtype)
  normalize_from_task()   — QueryTask → EvaluatorRetryContextResponse
  normalize_from_bundle() — session + bundle dict → EvaluatorRetryContextResponse
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, TokenAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from nextseek_api.assistant.descriptions_evaluator import (
    EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC,
    EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC,
)
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.models_evaluator import (
    EvaluatorLookup,
    EvaluatorRawPayloads,
    EvaluatorRetryContext,
    EvaluatorRetryContextResponse,
    EvaluatorRouting,
    EvaluatorRunMeta,
)
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

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
    path_subtype: Optional[str] = None
    if mode == "reporter":
        reporter_plan = debug.get("reporter_plan") or {}
        reporter_mode = reporter_plan.get("reporter_mode")
        if reporter_mode:
            path_subtype = f"reporter.{reporter_mode}"

    return ("standard", mode, path_subtype)


# ---------------------------------------------------------------------------
# Helper: _error_response
# ---------------------------------------------------------------------------

def _error_response(title: str, detail: str, http_status: int) -> Response:
    """Return a NExtSEEK-convention error response."""
    return Response(
        {"errors": [{"title": title, "detail": detail}]},
        status=http_status,
    )


# ---------------------------------------------------------------------------
# Helper: _build_retry_signals
# ---------------------------------------------------------------------------

def _build_retry_signals(
    task_status: str,
    reply: Optional[str],
) -> list[str]:
    """Build the retry_signals list based on task/bundle state."""
    signals: list[str] = []
    if task_status == "error":
        signals.append("error_status")
    if not reply:
        signals.append("empty_reply")
    return signals


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
    if bundle is not None:
        mode = bundle.get("mode")
        debug = bundle.get("debug") or {}
    else:
        mode = None
        debug = {}

    execution_mode, path_mode, path_subtype = classify_path(mode, debug)

    # Retryable: completed + recognized path_mode
    retryable = (
        task.status == "completed"
        and path_mode != "unsupported"
    )

    retry_signals = _build_retry_signals(task.status, reply)

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

    No task info is available (task_progress=None, task_result=None).
    """
    mode = bundle.get("mode")
    debug = bundle.get("debug") or {}
    reply = bundle.get("reply")
    query = bundle.get("query")
    bundle_id = bundle.get("id")

    execution_mode, path_mode, path_subtype = classify_path(mode, debug)

    # Bundle-based: always "completed" status, retryable if recognized path
    retryable = path_mode != "unsupported"

    retry_signals = _build_retry_signals("completed", reply)

    return EvaluatorRetryContextResponse(
        lookup=EvaluatorLookup(
            task_id=None,
            session_id=session.session_id,
            bundle_id=bundle_id,
            source="bundle",
        ),
        run=EvaluatorRunMeta(
            status="completed",
            query=query,
            reply=reply,
            created_at=session.created_at,
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
            task_progress=None,
            task_result=None,
            bundle=bundle,
            last_debug=session.last_debug or None,
        ),
    )


# ---------------------------------------------------------------------------
# EvaluatorViewSet
# ---------------------------------------------------------------------------

class EvaluatorViewSet(viewsets.ViewSet):
    """ViewSet for evaluator normalization and retry endpoints (admin-only)."""

    authentication_classes = [
        TokenAuthentication,
        CsrfExemptSessionAuthentication,
        BasicAuthentication,
    ]
    permission_classes = [IsAuthenticated, IsAdminUser]

    # ------------------------------------------------------------------
    # GET /evaluator/tasks/{task_id}/retry-context/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Evaluator: Retry Context by Task",
        description=EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC,
        tags=["evaluator"],
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"tasks/(?P<task_id>[0-9a-f-]+)/retry-context",
    )
    def retry_context_by_task(self, request, task_id=None):
        """Return normalized retry context for a specific async query task."""
        try:
            task = QueryTask.objects.select_related("session").get(
                task_id=task_id,
            )
        except QueryTask.DoesNotExist:
            return _error_response(
                "Not found",
                f"Task {task_id} not found.",
                status.HTTP_404_NOT_FOUND,
            )

        resp = normalize_from_task(task)
        return Response(
            resp.model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # GET /evaluator/sessions/{session_id}/bundles/{bundle_id}/retry-context/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Evaluator: Retry Context by Bundle",
        description=EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC,
        tags=["evaluator"],
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"sessions/(?P<session_id>[0-9a-f-]+)/bundles/(?P<bundle_id>\d+)/retry-context",
    )
    def retry_context_by_bundle(self, request, session_id=None, bundle_id=None):
        """Return normalized retry context for a historical bundle."""
        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response(
                "Not found",
                f"Session {session_id} not found.",
                status.HTTP_404_NOT_FOUND,
            )

        history = session.results_history or []
        bundle_id_int = int(bundle_id)
        bundle = next((b for b in history if b.get("id") == bundle_id_int), None)
        if bundle is None:
            return _error_response(
                "Not found",
                f"Bundle {bundle_id} not found in session.",
                status.HTTP_404_NOT_FOUND,
            )

        resp = normalize_from_bundle(session, bundle)
        return Response(
            resp.model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )
