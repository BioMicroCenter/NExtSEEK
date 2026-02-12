"""
DRF ViewSet for the NExtSEEK Assistant (chat) endpoints.

Provides 8 actions:
  GET  /assistant/me/
  POST /assistant/sessions/
  GET  /assistant/sessions/{session_id}/
  POST /assistant/query/                         (SSE streaming)
  POST /assistant/query/async/                   (async, returns task_id)
  GET  /assistant/tasks/{task_id}/progress/      (polling for progress)
  GET  /assistant/sessions/{sid}/bundles/{bid}/
  GET  /assistant/test-cases/
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any

from django.http import StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError

from chat_nextseek import TEST_CASES

from nextseek_api.assistant.descriptions import (
    ASSISTANT_BUNDLE_DOWNLOAD_DESC,
    ASSISTANT_ME_DESC,
    ASSISTANT_QUERY_ASYNC_DESC,
    ASSISTANT_QUERY_DESC,
    ASSISTANT_SESSION_CREATE_DESC,
    ASSISTANT_SESSION_DETAIL_DESC,
    ASSISTANT_TASK_PROGRESS_DESC,
    ASSISTANT_TEST_CASES_DESC,
)
from nextseek_api.assistant.models_api import (
    AssistantUserResponse,
    AsyncQueryResponse,
    BundleDownloadParams,
    QueryRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    TaskProgressResponse,
    TestCaseItem,
    TestCaseListResponse,
)
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback, run_query
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.helpers import resolve_seek_auth

logger = logging.getLogger(__name__)


def _error_response(title: str, detail: str, http_status: int) -> Response:
    """Return a NExtSEEK-convention error response."""
    return Response(
        {"errors": [{"title": title, "detail": detail}]},
        status=http_status,
    )


class AssistantViewSet(viewsets.ViewSet):
    """ViewSet for the NExtSEEK Assistant (multi-agent chat)."""

    permission_classes = [IsAuthenticated]

    def _check_auth(self, request):
        """Check authentication via BASIC, SESSION, or TOKEN."""
        basic_tuple, extra_headers = resolve_seek_auth(request, ["BASIC", "SESSION", "TOKEN"])
        if not basic_tuple and not extra_headers and not request.user.is_authenticated:
            return False, _error_response(
                "Authentication required",
                "Provide Basic, Session, or Token credentials.",
                status.HTTP_401_UNAUTHORIZED,
            )
        return True, None

    # ------------------------------------------------------------------
    # 1. GET /assistant/me/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Current User",
        description=ASSISTANT_ME_DESC,
        tags=["Assistant"],
        responses={200: AssistantUserResponse},
    )
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        return Response(
            AssistantUserResponse(
                username=request.user.username,
                is_admin=bool(request.user.is_staff or request.user.is_superuser),
            ).model_dump(),
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # 2. POST /assistant/sessions/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Create Session",
        description=ASSISTANT_SESSION_CREATE_DESC,
        tags=["Assistant"],
        responses={201: SessionCreateResponse},
    )
    @action(detail=False, methods=["post"], url_path="sessions")
    def create_session(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        session = ChatSession.objects.create(user=request.user)
        return Response(
            SessionCreateResponse(
                session_id=session.session_id,
                created_at=session.created_at,
            ).model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    # ------------------------------------------------------------------
    # 3. GET /assistant/sessions/{session_id}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Get Session",
        description=ASSISTANT_SESSION_DETAIL_DESC,
        tags=["Assistant"],
        responses={200: SessionDetailResponse},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"sessions/(?P<session_id>[0-9a-f-]+)",
    )
    def get_session(self, request, session_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        history = session.results_history or []
        return Response(
            SessionDetailResponse(
                session_id=session.session_id,
                created_at=session.created_at,
                query_count=len(history),
                has_results=bool(history),
            ).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # 4. POST /assistant/query/  (SSE streaming)
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Query (SSE)",
        description=ASSISTANT_QUERY_DESC,
        tags=["Assistant"],
        request=QueryRequest,
        responses={200: None},
        examples=[
            OpenApiExample(
                name="Simple query (auto-session)",
                value={"query": "Find me mice treated with NDMA"},
                request_only=True,
            ),
            OpenApiExample(
                name="Query with explicit session",
                value={"session_id": "abc12345-def6-7890-abcd-ef1234567890", "query": "Find me mice treated with NDMA"},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="query")
    def query(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            req = QueryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response(
                "Validation error",
                str(e),
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if req.session_id:
            # Explicit session_id — validate ownership
            try:
                chat_session = ChatSession.objects.get(
                    session_id=req.session_id,
                    user=request.user,
                )
            except ChatSession.DoesNotExist:
                return _error_response(
                    "Not found",
                    "Session not found or you do not own it.",
                    status.HTTP_404_NOT_FOUND,
                )
        else:
            # No session_id — reuse most recent or auto-create
            chat_session = (
                ChatSession.objects.filter(user=request.user)
                .order_by("-updated_at")
                .first()
            )
            if chat_session is None:
                chat_session = ChatSession.objects.create(user=request.user)

        # Event queue for thread → SSE generator communication
        event_queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        resolved_session_id = str(chat_session.session_id)

        def send_event(event_type: str, data: dict[str, Any]) -> None:
            if event_type in ("query_complete", "query_error"):
                data.setdefault("session_id", resolved_session_id)
            event_queue.put((event_type, data))

        adapter = DictSessionAdapter(chat_session)

        def _run_pipeline() -> None:
            try:
                run_query(adapter, req.query, send_event)
            except Exception:
                logger.exception("Unhandled pipeline error")
                send_event("query_error", {
                    "error": "Internal pipeline error",
                    "agent": "unknown",
                    "session_id": resolved_session_id,
                })
            finally:
                adapter.save()
                event_queue.put(None)  # sentinel

        thread = threading.Thread(target=_run_pipeline, daemon=True)
        thread.start()

        def event_stream():
            while True:
                item = event_queue.get()
                if item is None:
                    break
                event_type, data = item
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    # ------------------------------------------------------------------
    # 5. POST /assistant/query/async/  (returns task_id immediately)
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Query (Async)",
        description=ASSISTANT_QUERY_ASYNC_DESC,
        tags=["Assistant"],
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[
            OpenApiExample(
                name="Async query (auto-session)",
                value={"query": "Find me mice treated with NDMA"},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="query/async")
    def query_async(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            req = QueryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response(
                "Validation error",
                str(e),
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Resolve session (same logic as /query/)
        if req.session_id:
            try:
                chat_session = ChatSession.objects.get(
                    session_id=req.session_id,
                    user=request.user,
                )
            except ChatSession.DoesNotExist:
                return _error_response(
                    "Not found",
                    "Session not found or you do not own it.",
                    status.HTTP_404_NOT_FOUND,
                )
        else:
            chat_session = (
                ChatSession.objects.filter(user=request.user)
                .order_by("-updated_at")
                .first()
            )
            if chat_session is None:
                chat_session = ChatSession.objects.create(user=request.user)

        # Create task record
        query_task = QueryTask.objects.create(
            session=chat_session,
            user=request.user,
            query=req.query,
            status="running",
        )

        resolved_session_id = str(chat_session.session_id)
        task_id_str = str(query_task.task_id)

        # Build DB-backed event callback
        send_event = make_db_event_callback(task_id_str, resolved_session_id)
        adapter = DictSessionAdapter(chat_session)

        def _run_pipeline() -> None:
            try:
                run_query(adapter, req.query, send_event)
            except Exception:
                logger.exception("Unhandled pipeline error (async)")
                send_event("query_error", {
                    "error": "Internal pipeline error",
                    "agent": "unknown",
                    "session_id": resolved_session_id,
                })
            finally:
                adapter.save()

        thread = threading.Thread(target=_run_pipeline, daemon=True)
        thread.start()

        return Response(
            AsyncQueryResponse(
                task_id=query_task.task_id,
                session_id=chat_session.session_id,
            ).model_dump(mode="json"),
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------
    # 6. GET /assistant/tasks/{task_id}/progress/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Task Progress",
        description=ASSISTANT_TASK_PROGRESS_DESC,
        tags=["Assistant"],
        responses={200: TaskProgressResponse},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"tasks/(?P<task_id>[0-9a-f-]+)/progress",
    )
    def task_progress(self, request, task_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            query_task = QueryTask.objects.select_related("session").get(
                task_id=task_id,
                user=request.user,
            )
        except QueryTask.DoesNotExist:
            return _error_response(
                "Not found",
                "Task not found or you do not own it.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response(
            TaskProgressResponse(
                task_id=query_task.task_id,
                session_id=query_task.session.session_id,
                status=query_task.status,
                progress=[
                    {"event": p.get("event", ""), "data": p.get("data", {})}
                    for p in (query_task.progress or [])
                ],
                result=query_task.result if query_task.status in ("completed", "error") else None,
            ).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # 7. GET /assistant/sessions/{session_id}/bundles/{bundle_id}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Download Bundle",
        description=ASSISTANT_BUNDLE_DOWNLOAD_DESC,
        tags=["Assistant"],
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"sessions/(?P<session_id>[0-9a-f-]+)/bundles/(?P<bundle_id>\d+)",
    )
    def download_bundle(self, request, session_id=None, bundle_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            chat_session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if chat_session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        history = chat_session.results_history or []
        bundle_id_int = int(bundle_id)
        bundle = next((b for b in history if b.get("id") == bundle_id_int), None)
        if bundle is None:
            return _error_response("Not found", f"Bundle {bundle_id} not found.", status.HTTP_404_NOT_FOUND)

        return Response(bundle, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # 6. GET /assistant/test-cases/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: List Test Cases",
        description=ASSISTANT_TEST_CASES_DESC,
        tags=["Assistant"],
        responses={200: TestCaseListResponse},
    )
    @action(detail=False, methods=["get"], url_path="test-cases")
    def test_cases(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        if not (request.user.is_staff or request.user.is_superuser):
            return _error_response("Forbidden", "Admin access required.", status.HTTP_403_FORBIDDEN)

        items = [TestCaseItem(id=tc["id"], prompt=tc["prompt"]) for tc in TEST_CASES]
        return Response(
            TestCaseListResponse(total=len(items), test_cases=items).model_dump(),
            status=status.HTTP_200_OK,
        )
