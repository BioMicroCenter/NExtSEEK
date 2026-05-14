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
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError

from django.conf import settings

ASSISTANT_PARTICIPATING_PROJECTS = settings.ASSISTANT_PARTICIPATING_PROJECTS
TEST_CASES = settings.TEST_CASES

from nextseek_api.assistant.descriptions import (
    ASSISTANT_BUNDLE_DOWNLOAD_DESC,
    ASSISTANT_ME_DESC,
    ASSISTANT_QUERY_ASYNC_DESC,
    ASSISTANT_QUERY_DESC,
    ASSISTANT_SESSION_CREATE_DESC,
    ASSISTANT_SESSION_DELETE_DESC,
    ASSISTANT_SESSION_DETAIL_DESC,
    ASSISTANT_SESSION_PATCH_DESC,
    ASSISTANT_SESSIONS_LIST_DESC,
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
    SessionListItem,
    SessionListResponse,
    SessionPatchRequest,
    TaskProgressResponse,
    TestCaseItem,
    TestCaseListResponse,
    Turn,
)
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.excel_export import extract_table_artifacts
from rest_framework.authentication import (
    BasicAuthentication,
    SessionAuthentication,
    TokenAuthentication,
)

from nextseek_api.helpers import resolve_seek_auth, SeekAPIClient

from chat_nextseek.orchestrator import run_query, run_query_plan
from chat_nextseek.config import ChatConfig
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

logger = logging.getLogger(__name__)

class UserInParticipatingProject(BasePermission):
    message = "User needs to be in a participating project to use assistant"
    def has_permission(self, request, view):
        try:
            client = SeekAPIClient()
            person = json.loads(client.get_current_person(request)[0])
            projects = person['data']['relationships']['projects']['data']
            project_ids = set(map(lambda project: project['id'], projects))
            if project_ids & ASSISTANT_PARTICIPATING_PROJECTS != set():
                return True
            else:
                return False
        except Exception:
            return False
        
    def has_object_permission(self, request, view):
        return self.has_permissions(request, view)

class CsrfExemptSessionAuthentication(SessionAuthentication):
    """SessionAuthentication without CSRF enforcement.

    DRF's SessionAuthentication.enforce_csrf() runs Django's CSRFCheck
    independently of the global CsrfViewMiddleware (which is disabled in
    this project).  Since no middleware sets the ``csrftoken`` cookie,
    browser-based session users always fail CSRF validation -> 403.

    This subclass skips that check.  The ViewSet is still protected by
    ``IsAuthenticated`` and the custom ``_check_auth`` method.
    """

    def enforce_csrf(self, request):
        return  # CSRF cookie is never set; skip the check


def _error_response(title: str, detail: str, http_status: int) -> Response:
    """Return a NExtSEEK-convention error response."""
    return Response(
        {"errors": [{"title": title, "detail": detail}]},
        status=http_status,
    )


def _auto_title_if_unset(chat_session: ChatSession) -> None:
    """Populate ChatSession.title from the first user query if currently NULL.

    Idempotent: subsequent calls on a session with a title set are a no-op.
    A manually-set title is therefore never overwritten — frontend rename
    always wins.
    """
    if chat_session.title:
        return
    history = chat_session.results_history or []
    first_user_query = ""
    for bundle in history:
        uq = (bundle or {}).get("user_query")
        if uq:
            first_user_query = uq
            break
    if not first_user_query:
        return
    title = " ".join(first_user_query.split())[:60]
    if not title:
        return
    chat_session.title = title
    chat_session.save(update_fields=["title", "updated_at"])


def _select_chat_config(request, req) -> ChatConfig:
    """Pick the ChatConfig instance for this request.

    Returns ``settings.NEXTSEEK_CHAT_CONFIG_PROD`` when the request asked for
    ``use_prod=True`` AND the caller is admin AND a prod config was actually
    built in ``local_settings.py``. Falls back to the default
    ``NEXTSEEK_CHAT_CONFIG`` in every other case.
    """
    if not getattr(req, "use_prod", False):
        return settings.NEXTSEEK_CHAT_CONFIG
    user = getattr(request, "user", None)
    is_admin = bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    if not is_admin:
        return settings.NEXTSEEK_CHAT_CONFIG
    prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
    if prod_config is None:
        return settings.NEXTSEEK_CHAT_CONFIG
    return prod_config


class AssistantViewSet(viewsets.ViewSet):
    """ViewSet for the NExtSEEK Assistant (multi-agent chat)."""

    authentication_classes = [TokenAuthentication, CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, UserInParticipatingProject]

    # ------------------------------------------------------------------
    # Helpers for the sessions list/detail
    # ------------------------------------------------------------------

    @staticmethod
    def _project_session_list_row(cs: ChatSession) -> dict:
        """Project a ChatSession into the SessionListItem shape.

        Reads `results_history` once to compute `query_count` and `preview`.
        Falls back to "New chat" when `title` is null.
        """
        history = cs.results_history or []
        first_user_query = ""
        for bundle in history:
            uq = (bundle or {}).get("user_query")
            if uq:
                first_user_query = uq
                break
        preview = " ".join(first_user_query.split())[:80]
        return SessionListItem(
            session_id=cs.session_id,
            title=cs.title or "New chat",
            created_at=cs.created_at,
            updated_at=cs.updated_at,
            query_count=len(history),
            preview=preview,
        ).model_dump(mode="json")

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
    # 1b. GET /assistant/sessions/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: List Sessions",
        description=ASSISTANT_SESSIONS_LIST_DESC,
        tags=["Assistant"],
        responses={200: SessionListResponse},
    )
    @action(detail=False, methods=["get"], url_path="sessions")
    def list_sessions(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        # Two-step lookup: only the PK enters the ORDER BY query so the
        # JSON columns (results_history / last_debug) never land in the
        # MySQL sort buffer (regression: error 1038 once results_history
        # grew beyond sort_buffer_size).
        ids = list(
            ChatSession.objects.filter(user=request.user)
            .order_by("-updated_at")
            .values_list("session_id", flat=True)[:50]
        )
        rows = []
        for sid in ids:
            cs = ChatSession.objects.get(session_id=sid)
            rows.append(self._project_session_list_row(cs))
        return Response(
            {"total": len(rows), "sessions": rows},
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
    @list_sessions.mapping.post
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
        payload = SessionDetailResponse(
            session_id=session.session_id,
            created_at=session.created_at,
            query_count=len(history),
            has_results=bool(history),
        ).model_dump(mode="json")

        include = request.query_params.get("include", "")
        include_set = {p.strip() for p in include.split(",") if p.strip()}
        if "turns" in include_set:
            payload["title"] = session.title or "New chat"
            chat_log = (session.extra_state or {}).get("chat_log") or []
            bundles_by_id = {b.get("id"): b for b in history if isinstance(b, dict)}
            turns: list[dict[str, Any]] = []
            if chat_log:
                for entry in chat_log:
                    if not (entry or {}).get("user_query"):
                        continue
                    bid = entry.get("bundle_id")
                    bundle = bundles_by_id.get(bid) if bid is not None else None
                    # Prefer the full reply stored directly on the chat_log entry
                    # (wizard turns don't produce bundles, so this is the only
                    # full-text source for them). Fall back to the bundle's
                    # terminal_reply for legacy entries written before
                    # assistant_reply existed, then to the 280-char preview.
                    reply = (
                        entry.get("assistant_reply")
                        or (bundle.get("terminal_reply") or bundle.get("reply") if bundle else None)
                        or entry.get("assistant_reply_preview", "")
                    ) or ""
                    artifacts = extract_table_artifacts(bundle) if bundle else None
                    turns.append(
                        Turn(
                            bundle_id=bid if bid is not None else 0,
                            user_query=entry.get("user_query", ""),
                            reply=reply,
                            mode=entry.get("mode", ""),
                            ts=entry.get("ts"),
                            artifacts=artifacts or None,
                        ).model_dump(mode="json")
                    )
            else:
                turns = [
                    Turn(
                        bundle_id=b.get("id", 0),
                        user_query=b.get("user_query", ""),
                        reply=b.get("terminal_reply") or b.get("reply") or "",
                        mode=b.get("mode", ""),
                        ts=b.get("ts"),
                        artifacts=(extract_table_artifacts(b) or None),
                    ).model_dump(mode="json")
                    for b in history
                    if (b or {}).get("user_query")
                ]
            payload["turns"] = turns

        return Response(payload, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # 3b. PATCH /assistant/sessions/{session_id}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Rename Session",
        description=ASSISTANT_SESSION_PATCH_DESC,
        tags=["Assistant"],
        request=SessionPatchRequest,
        responses={200: SessionListItem},
    )
    @get_session.mapping.patch
    def patch_session(self, request, session_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        raw_title = (request.data or {}).get("title")
        if not isinstance(raw_title, str):
            return _error_response("Validation error", "Field 'title' is required and must be a string.", status.HTTP_422_UNPROCESSABLE_ENTITY)
        trimmed = raw_title.strip()
        if not trimmed:
            return _error_response("Validation error", "Field 'title' must not be empty after trim.", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if len(trimmed) > 200:
            return _error_response("Validation error", "Field 'title' is too long (max 200 chars).", status.HTTP_422_UNPROCESSABLE_ENTITY)

        session.title = trimmed
        session.save(update_fields=["title", "updated_at"])
        return Response(
            self._project_session_list_row(session),
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # 3c. DELETE /assistant/sessions/{session_id}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Delete Session",
        description=ASSISTANT_SESSION_DELETE_DESC,
        tags=["Assistant"],
        responses={204: None},
    )
    @get_session.mapping.delete
    def delete_session(self, request, session_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

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
        elif req.force_new:
            # Frontend "New chat" path — unconditionally create.
            chat_session = ChatSession.objects.create(user=request.user)
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

        # Resolve credentials: try Basic auth header first, fall back to session
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if basic_tuple and basic_tuple[0] and basic_tuple[1]:
            api_user, api_pass = basic_tuple
        else:
            api_user = request.session.get("username")
            api_pass = request.session.get("password")

        chat_config = _select_chat_config(request, req)

        def _run_pipeline() -> None:
            try:
                match getattr(req, "mode", "standard"):
                    case "plan":
                        run_query_plan(adapter, chat_config, req.query, send_event, credentials={"api_user": api_user, "api_pass": api_pass})
                    case _:
                        run_query(adapter, chat_config, req.query, send_event, credentials={"api_user": api_user, "api_pass": api_pass})
            except Exception:
                logger.exception("Unhandled pipeline error")
                send_event("query_error", {
                    "error": "Internal pipeline error",
                    "agent": "unknown",
                    "session_id": resolved_session_id,
                })
            finally:
                adapter.save()
                _auto_title_if_unset(chat_session)
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
        elif req.force_new:
            # Frontend "New chat" path — unconditionally create.
            chat_session = ChatSession.objects.create(user=request.user)
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

        # Resolve credentials: try Basic auth header first, fall back to session
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if basic_tuple and basic_tuple[0] and basic_tuple[1]:
            api_user, api_pass = basic_tuple
        else:
            api_user = request.session.get("username")
            api_pass = request.session.get("password")

        chat_config = _select_chat_config(request, req)

        def _run_pipeline() -> None:
            try:
                match getattr(req, "mode", "standard"):
                    case "plan":
                        run_query_plan(adapter, chat_config, req.query, send_event, credentials={"api_user": api_user, "api_pass": api_pass})
                    case _:
                        run_query(adapter, chat_config, req.query, send_event, credentials={"api_user": api_user, "api_pass": api_pass})
            except Exception:
                logger.exception("Unhandled pipeline error (async)")
                send_event("query_error", {
                    "error": "Internal pipeline error",
                    "agent": "unknown",
                    "session_id": resolved_session_id,
                })
            finally:
                adapter.save()
                _auto_title_if_unset(chat_session)

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
    # 8. GET /assistant/sessions/{sid}/bundles/{bid}/artifacts/{key}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Download Artifact",
        description="Download a specific artifact from a bundle as an Excel file.",
        tags=["Assistant"],
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"sessions/(?P<session_id>[0-9a-f-]+)/bundles/(?P<bundle_id>\d+)/artifacts/(?P<artifact_key>[\w]+)",
    )
    def download_artifact(self, request, session_id=None, bundle_id=None, artifact_key=None):
        """Download a specific artifact from a bundle."""
        import io
        from pathlib import Path
        from django.http import FileResponse
        from nextseek_api.assistant.excel_export import (
            build_tables_from_bundle,
            generate_table_xlsx,
            generate_search_xlsx,
        )

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

        xlsx_content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        # --- Serve GEO workbook from disk ---
        if artifact_key == "geo_seq_workbooks":
            saved = bundle.get("report_saved_files") or {}
            workbooks = saved.get("geo_seq_workbooks") or []
            if not workbooks:
                return _error_response("Not found", "No GEO workbooks found.", status.HTTP_404_NOT_FOUND)
            filepath = Path(workbooks[0]).resolve()
            # Path traversal protection: only serve files under project dir or home dir
            from django.conf import settings
            allowed_dirs = [Path(settings.BASE_DIR).resolve(), Path.home().resolve()]
            if not any(str(filepath).startswith(str(d)) for d in allowed_dirs):
                return _error_response("Forbidden", "File path not within allowed directory.", status.HTTP_403_FORBIDDEN)
            if not filepath.is_file():
                return _error_response("Not found", "GEO workbook file not found on disk.", status.HTTP_404_NOT_FOUND)
            return FileResponse(
                filepath.open("rb"),
                content_type=xlsx_content_type,
                as_attachment=True,
                filename=filepath.name,
            )

        # --- Generate search results xlsx ---
        if artifact_key == "search_results":
            mode = bundle.get("mode", "")
            if mode not in ("new_search", "refine_last_search"):
                return _error_response("Not found", "Not a search bundle.", status.HTTP_404_NOT_FOUND)
            xlsx_bytes = generate_search_xlsx(bundle)
            return FileResponse(
                io.BytesIO(xlsx_bytes),
                content_type=xlsx_content_type,
                as_attachment=True,
                filename=f"search_results_{bundle_id}.xlsx",
            )

        # --- Generate all tables combined xlsx ---
        if artifact_key == "all_tables":
            tables = build_tables_from_bundle(bundle)
            if not tables:
                return _error_response("Not found", "No report data.", status.HTTP_404_NOT_FOUND)
            try:
                xlsx_bytes = generate_table_xlsx(tables)
            except Exception:
                logger.exception("Failed to generate xlsx for bundle %s", bundle_id)
                return _error_response("Error", "Failed to generate Excel file.", status.HTTP_500_INTERNAL_SERVER_ERROR)
            return FileResponse(
                io.BytesIO(xlsx_bytes),
                content_type=xlsx_content_type,
                as_attachment=True,
                filename=f"report_{bundle_id}.xlsx",
            )

        # --- Generate single table xlsx ---
        tables = build_tables_from_bundle(bundle)
        table = next((t for t in tables if t["key"] == artifact_key), None)
        if table:
            try:
                xlsx_bytes = generate_table_xlsx([table])
            except Exception:
                logger.exception("Failed to generate xlsx for artifact %s", artifact_key)
                return _error_response("Error", "Failed to generate Excel file.", status.HTTP_500_INTERNAL_SERVER_ERROR)
            return FileResponse(
                io.BytesIO(xlsx_bytes),
                content_type=xlsx_content_type,
                as_attachment=True,
                filename=f"{artifact_key}_{bundle_id}.xlsx",
            )

        return _error_response("Not found", f"Artifact '{artifact_key}' not found.", status.HTTP_404_NOT_FOUND)

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

        test_cases = {}
        for tc in TEST_CASES.values():
            test_cases = test_cases | tc
        items = [TestCaseItem(id=tc_id, prompt=tc["prompt"]) for tc_id, tc in test_cases.items()]
        return Response(
            TestCaseListResponse(total=len(items), test_cases=items).model_dump(),
            status=status.HTTP_200_OK,
        )
