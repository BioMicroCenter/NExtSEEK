"""DRF ViewSet for the additive dmac_assistant integration (router + Container-CC).

NEW endpoints, fully additive — the existing ``AssistantViewSet`` (chat_nextseek
wrapper) is untouched and reused only via import:

  POST /nextseek_api/cc-assistant/query/async/        router-dispatched (NS or CC)
  POST /nextseek_api/cc-assistant/cc/query/async/     force the Container-CC route
  GET  /nextseek_api/cc-assistant/tasks/{id}/progress/  poll fallback (same shape)

All three create/read the SAME ``QueryTask`` model the existing assistant uses,
and drive ``make_db_event_callback`` — so the EXISTING ``TaskProgressConsumer``
websocket (``ws/assistant/progress/{task_id}/``) streams them to the unchanged
chat_frontend with no new consumer or routing entry.

The router (``cc_assistant.router.decide``) is dmac_assistant's BAML RouteQuery
(with a heuristic fallback). The NS route reuses the in-process
``chat_nextseek.run_query`` exactly as ``AssistantViewSet`` does; the CC route
runs a sandboxed ``claude`` container via ``cc_assistant.cc_engine``.
"""
from __future__ import annotations

import logging
import threading

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import (
    BasicAuthentication,
    SessionAuthentication,
    TokenAuthentication,
)
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError

from nextseek_api.assistant.models_api import AsyncQueryResponse, QueryRequest, TaskProgressResponse
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback
from nextseek_api.helpers import resolve_seek_auth

# Reuse the existing assistant's helpers (do NOT redefine its behavior).
from nextseek_api.services.assistant import (
    CsrfExemptSessionAuthentication,
    UserInParticipatingProject,
    _auto_title_if_unset,
    _error_response,
    _select_chat_config,
)

from chat_nextseek.orchestrator import run_query, run_query_plan

from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant import cc_config
from nextseek_api.cc_assistant import cc_session

logger = logging.getLogger(__name__)


class CCAssistantViewSet(viewsets.ViewSet):
    """Router + Container-Claude-Code assistant (additive to AssistantViewSet)."""

    authentication_classes = [TokenAuthentication, CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, UserInParticipatingProject]

    # ------------------------------------------------------------------ auth
    def _check_auth(self, request):
        basic_tuple, extra_headers = resolve_seek_auth(request, ["BASIC", "SESSION", "TOKEN"])
        if not basic_tuple and not extra_headers and not request.user.is_authenticated:
            return False, _error_response(
                "Authentication required",
                "Provide Basic, Session, or Token credentials.",
                status.HTTP_401_UNAUTHORIZED,
            )
        return True, None

    # ------------------------------------------------------------------ session
    def _resolve_session(self, request, req) -> ChatSession:
        if req.session_id:
            return ChatSession.objects.get(session_id=req.session_id, user=request.user)
        if getattr(req, "force_new", False):
            return ChatSession.objects.create(user=request.user)
        existing = (
            ChatSession.objects.filter(user=request.user).order_by("-updated_at").first()
        )
        return existing or ChatSession.objects.create(user=request.user)

    def _resolve_credentials(self, request):
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if basic_tuple and basic_tuple[0] and basic_tuple[1]:
            return basic_tuple
        return request.session.get("username"), request.session.get("password")

    # ------------------------------------------------------------------ dispatch
    def _start_task(self, request, req, *, force_cc: bool) -> Response:
        try:
            chat_session = self._resolve_session(request, req)
        except ChatSession.DoesNotExist:
            return _error_response(
                "Not found", "Session not found or you do not own it.", status.HTTP_404_NOT_FOUND
            )

        query_task = QueryTask.objects.create(
            session=chat_session, user=request.user, query=req.query, status="running",
        )
        resolved_session_id = str(chat_session.session_id)
        send_event = make_db_event_callback(str(query_task.task_id), resolved_session_id)
        adapter = DictSessionAdapter(chat_session)
        api_user, api_pass = self._resolve_credentials(request)
        chat_config = _select_chat_config(request, req)

        # Prod-config credential swap (mirror AssistantViewSet).
        from django.conf import settings
        prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
        if prod_config is not None and chat_config is prod_config:
            if chat_config.API_USER and chat_config.API_PASS:
                api_user, api_pass = chat_config.API_USER, chat_config.API_PASS

        mode = getattr(req, "mode", "standard")
        # Capture identity for the CC route (scoped Dropbox mounts + output).
        cc_user_id = request.user.username
        cc_run_id = str(query_task.task_id)

        def _run() -> None:
            ran_ns = False
            try:
                if force_cc:
                    # CC always runs Opus (the only proxy-allowlisted model);
                    # hardcoding sonnet here would 403 at the Bedrock proxy.
                    decision = cc_router.RouteDecision(
                        route=cc_router.ROUTE_CC, model_class="opus",
                        model_id=cc_router._resolve_cc_model_id(),
                        reasoning="forced", source="forced",
                    )
                else:
                    decision = cc_router.decide(req.query)

                send_event("route_decided", {
                    "route": decision.route, "model_class": decision.model_class,
                    "source": decision.source,
                })

                if decision.route == cc_router.ROUTE_UNRELATED:
                    # OI-4: out-of-scope query — never runs NS or CC; emit the
                    # canned out-of-scope reply and finish (mirrors dmac ws.py).
                    send_event("query_complete", {
                        "reply": cc_router.UNRELATED_CANNED_TEXT,
                        "bundle_id": None,
                        "session_id": resolved_session_id,
                    })
                    return

                if decision.route == cc_router.ROUTE_NS:
                    ran_ns = True
                    creds = {"api_user": api_user, "api_pass": api_pass}
                    if mode == "plan":
                        run_query_plan(adapter, chat_config, req.query, send_event, credentials=creds)
                    else:
                        run_query(adapter, chat_config, req.query, send_event, credentials=creds)
                else:
                    ok, detail = cc_engine.cc_runner_available()
                    if not ok:
                        send_event("query_error", {
                            "error": f"Container-CC route is not available: {detail}",
                            "agent": "container_cc", "session_id": resolved_session_id,
                        })
                        return
                    cc_state_key = str(chat_session.session_id)
                    prior_id = cc_session.resume_id_from_state(chat_session.extra_state)

                    def _persist_cc_session(cc_sid: str) -> None:
                        # Single-key read-modify-write; never clobber other
                        # extra_state keys. Re-captured every turn (robust if the
                        # claude id rotates under -p --resume).
                        chat_session.extra_state["cc_session_id"] = cc_sid
                        chat_session.save(update_fields=["extra_state", "updated_at"])

                    cc_send = cc_session.make_session_sniffer(send_event, _persist_cc_session)

                    cc_engine.run_cc_turn(
                        query=req.query, model_id=decision.model_id,
                        send_event=cc_send,
                        user_id=cc_user_id,
                        projects=cc_config.projects_for(cc_user_id),
                        run_id=cc_run_id,
                        paths=cc_config.CCPaths.from_env(),
                        session_id=prior_id,
                        cc_state_key=cc_state_key,
                        # NExtSEEK login is per-request (Basic auth), not env;
                        # inject so the in-container chat_nextseek can authenticate.
                        api_user=api_user, api_pass=api_pass,
                    )
            except Exception:
                logger.exception("cc-assistant pipeline error")
                send_event("query_error", {
                    "error": "Internal pipeline error", "agent": "unknown",
                    "session_id": resolved_session_id,
                })
            finally:
                if ran_ns:
                    adapter.save()
                    _auto_title_if_unset(chat_session)

        threading.Thread(target=_run, daemon=True).start()

        return Response(
            AsyncQueryResponse(
                task_id=query_task.task_id, session_id=chat_session.session_id,
            ).model_dump(mode="json"),
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------ routes
    @extend_schema(
        operation_id="CC Assistant: Query (Async, routed)",
        description="Router-dispatched async query. The dmac_assistant BAML router "
                    "decides between the deterministic NExtSEEK pipeline (chat_nextseek) "
                    "and the sandboxed Container-Claude-Code agent. Returns a task_id; "
                    "stream progress over the existing ws/assistant/progress/{task_id}/.",
        tags=["Assistant (CC)"],
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[OpenApiExample(
            name="Routed query", value={"query": "Find me mice treated with NDMA"},
            request_only=True,
        )],
    )
    @action(detail=False, methods=["post"], url_path="query/async")
    def query_async(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            req = QueryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response("Validation error", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY)
        return self._start_task(request, req, force_cc=False)

    @extend_schema(
        operation_id="CC Assistant: Query (Async, force Container-CC)",
        description="Force the Container-Claude-Code route (bypass the router). "
                    "Runs a sandboxed claude container; streams progress over the "
                    "existing assistant websocket.",
        tags=["Assistant (CC)"],
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
    )
    @action(detail=False, methods=["post"], url_path="cc/query/async")
    def cc_query_async(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            req = QueryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response("Validation error", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY)
        return self._start_task(request, req, force_cc=True)

    @extend_schema(
        operation_id="CC Assistant: Task Progress (poll fallback)",
        description="Poll a routed/CC task's progress (same shape as the existing "
                    "assistant). The websocket is the primary channel; this is the fallback.",
        tags=["Assistant (CC)"],
        responses={200: TaskProgressResponse},
    )
    @action(detail=False, methods=["get"], url_path=r"tasks/(?P<task_id>[0-9a-f-]+)/progress")
    def task_progress(self, request, task_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            query_task = QueryTask.objects.select_related("session").get(
                task_id=task_id, user=request.user,
            )
        except QueryTask.DoesNotExist:
            return _error_response("Not found", "Task not found or you do not own it.", status.HTTP_404_NOT_FOUND)
        return Response(
            TaskProgressResponse(
                task_id=query_task.task_id,
                session_id=query_task.session.session_id,
                status=query_task.status,
                progress=[{"event": p.get("event", ""), "data": p.get("data", {})}
                          for p in (query_task.progress or [])],
                result=query_task.result if query_task.status in ("completed", "error") else None,
            ).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )
