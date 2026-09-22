"""
DRF ViewSet for the NExtSEEK Evaluator endpoints.

Provides two read-only normalization endpoints:
  GET /evaluator/tasks/{task_id}/retry-context/
  GET /evaluator/sessions/{session_id}/bundles/{bundle_id}/retry-context/

The helper functions (classify_path, normalize_from_task, normalize_from_bundle)
and the retry endpoint's pipeline body (run_retry) live in NessieAI/ns/retry.py
(NessieAI Phase B). This module keeps the HTTP surface and hands the host seams
in.
"""

from __future__ import annotations

import logging
import threading

from django.db.models import Q
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, TokenAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from nextseek_api.assistant.descriptions_evaluator import (
    EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC,
    EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC,
    EVALUATOR_RETRY_EXECUTE_DESC,
    EVALUATOR_RUNS_LIST_DESC,
)
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.models_evaluator import (
    EvaluatorRunSummary,
    EvaluatorRunsListResponse,
    RetryRequest,
    RetryResponse,
)
from nextseek_api.helpers import resolve_seek_auth, StandardResultsSetPagination
from nextseek_api.graph_search.scope import plain_scope
from nextseek_api.permissions import IsSuperUser
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
# Moved to NessieAI/ns/retry.py (NessieAI Phase B): the normalizers the two
# retry-context actions call, the run-list and retry lookups, and the retry
# pipeline body that retry_execute runs on its thread. classify_path is not
# called here; it is re-exported because it was a public name of this module.
from NessieAI.ns.retry import (  # noqa: F401
    _find_bundle,
    _task_has_bundle,
    classify_path,
    normalize_from_bundle,
    normalize_from_task,
    run_retry,
)

logger = logging.getLogger(__name__)


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
# EvaluatorViewSet
# ---------------------------------------------------------------------------

class EvaluatorViewSet(viewsets.ViewSet):
    """ViewSet for evaluator normalization and retry endpoints (admin-only)."""

    authentication_classes = [
        TokenAuthentication,
        CsrfExemptSessionAuthentication,
        BasicAuthentication,
    ]
    # IsSuperUser, not DRF's IsAdminUser (#75). IsAdminUser checks is_staff, and
    # dmac/views.py:80,97 set is_staff = 1 on every SEEK user at login — so it was
    # equivalent to IsAuthenticated here, and these reads return OTHER users'
    # assistant prompts and result bundles. See nextseek_api/permissions.py.
    permission_classes = [IsAuthenticated, IsSuperUser]

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

    # ------------------------------------------------------------------
    # GET /evaluator/runs/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Evaluator: List Runs",
        description=EVALUATOR_RUNS_LIST_DESC,
        tags=["evaluator"],
        parameters=[
            OpenApiParameter("session_id", OpenApiTypes.UUID, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("task_id", OpenApiTypes.UUID, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("has_bundle", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("created_after", OpenApiTypes.DATETIME, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("created_before", OpenApiTypes.DATETIME, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("user_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("page_size", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: EvaluatorRunsListResponse},
    )
    @action(detail=False, methods=["get"], url_path="runs")
    def runs_list(self, request):
        """List and filter historical query task runs for evaluator analysis."""
        qs = QueryTask.objects.select_related("session").order_by("-created_at")

        # Apply filters
        session_id = request.query_params.get("session_id")
        if session_id:
            qs = qs.filter(session__session_id=session_id)

        task_id = request.query_params.get("task_id")
        if task_id:
            qs = qs.filter(task_id=task_id)

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        user_id = request.query_params.get("user_id")
        if user_id:
            qs = qs.filter(user_id=user_id)

        created_after = request.query_params.get("created_after")
        if created_after:
            qs = qs.filter(created_at__gte=created_after)

        created_before = request.query_params.get("created_before")
        if created_before:
            qs = qs.filter(created_at__lte=created_before)

        # has_bundle filter: Python-level filtering since JSON field lookups
        # for nested keys are not reliably supported across all DB backends
        has_bundle_param = request.query_params.get("has_bundle")

        paginator = StandardResultsSetPagination()

        if has_bundle_param is not None:
            want_bundle = has_bundle_param.lower() in ("true", "1")
            # Materialise to list and filter in Python for SQLite compat
            all_tasks = list(qs)
            filtered = [t for t in all_tasks if _task_has_bundle(t) == want_bundle]
            # Paginate the filtered list
            page = paginator.paginate_queryset(filtered, request)
        else:
            page = paginator.paginate_queryset(qs, request)

        results = [
            EvaluatorRunSummary(
                task_id=t.task_id,
                session_id=t.session.session_id,
                status=t.status,
                query=t.query,
                has_bundle=_task_has_bundle(t),
                user_id=t.user_id,
                created_at=t.created_at,
            ).model_dump(mode="json")
            for t in page
        ]

        return paginator.get_paginated_response(results)

    # ------------------------------------------------------------------
    # POST /evaluator/retry/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Evaluator: Execute Retry",
        description=EVALUATOR_RETRY_EXECUTE_DESC,
        tags=["evaluator"],
        request=RetryRequest,
        responses={202: RetryResponse},
    )
    @action(detail=False, methods=["post"], url_path="retry")
    def retry_execute(self, request):
        """Submit a retry query through the assistant pipeline."""
        try:
            req = RetryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response(
                "Validation error", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # --- Source identifier validation ---
        has_task = req.task_id is not None
        has_bundle = req.session_id is not None and req.bundle_id is not None
        has_session_only = req.session_id is not None and req.bundle_id is None

        if has_session_only:
            return _error_response(
                "Invalid source",
                "session_id requires bundle_id.",
                status.HTTP_400_BAD_REQUEST,
            )
        if not has_task and not has_bundle:
            return _error_response(
                "Invalid source",
                "Provide either task_id OR session_id + bundle_id.",
                status.HTTP_400_BAD_REQUEST,
            )
        if has_task and has_bundle:
            return _error_response(
                "Invalid source",
                "Provide either task_id OR session_id + bundle_id, not both.",
                status.HTTP_400_BAD_REQUEST,
            )

        # --- Resolve source ---
        source_task_id = None
        source_bundle_id = None

        if has_task:
            try:
                source_task = QueryTask.objects.select_related("session").get(
                    task_id=req.task_id,
                )
            except QueryTask.DoesNotExist:
                return _error_response(
                    "Not found",
                    "Source task not found.",
                    status.HTTP_404_NOT_FOUND,
                )
            chat_session = source_task.session
            source_task_id = source_task.task_id
            if source_task.result:
                source_bundle_id = source_task.result.get("bundle_id")
        else:
            try:
                chat_session = ChatSession.objects.get(session_id=req.session_id)
            except ChatSession.DoesNotExist:
                return _error_response(
                    "Not found",
                    "Source session not found.",
                    status.HTTP_404_NOT_FOUND,
                )
            bundle = _find_bundle(chat_session, req.bundle_id)
            if bundle is None:
                return _error_response(
                    "Not found",
                    "Source bundle not found.",
                    status.HTTP_404_NOT_FOUND,
                )
            source_bundle_id = req.bundle_id

        # --- Resolve credentials ---
        basic_tuple, extra_headers = resolve_seek_auth(
            request, ["BASIC", "SESSION"]
        )
        if basic_tuple and basic_tuple[0] and basic_tuple[1]:
            api_user, api_pass = basic_tuple
            credential_source = "basic_auth"
        else:
            api_user, api_pass = None, None
            credential_source = "service_account"

        # --- Create new QueryTask ---
        query_task = QueryTask.objects.create(
            session=chat_session,
            user=request.user,
            query=req.query,
            status="running",
        )

        # --- Spawn background pipeline thread ---
        task_id_str = str(query_task.task_id)
        session_id_str = str(chat_session.session_id)
        send_event = make_db_event_callback(task_id_str, session_id_str)
        adapter = DictSessionAdapter(chat_session)

        thread = threading.Thread(
            target=run_retry,
            kwargs=dict(
                adapter=adapter, req=req, send_event=send_event,
                api_user=api_user, api_pass=api_pass,
                session_id_str=session_id_str,
                # The caller's project scope for graph queries, as plain data.
                graph_scope=plain_scope(request.user),
            ),
            daemon=True,
        )
        thread.start()

        return Response(
            RetryResponse(
                task_id=query_task.task_id,
                session_id=chat_session.session_id,
                source_task_id=source_task_id,
                source_session_id=chat_session.session_id,
                source_bundle_id=source_bundle_id,
                credential_source=credential_source,
            ).model_dump(mode="json"),
            status=status.HTTP_202_ACCEPTED,
        )
