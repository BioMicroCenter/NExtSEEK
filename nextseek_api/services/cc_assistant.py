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

The turn itself is ``start_task`` in ``NessieAI/cc/turn.py``: the routing
policy (``NessieAI/router/policy.py``, around dmac_assistant's BAML RouteQuery
with a heuristic fallback), then either the in-process
``chat_nextseek.run_query``, exactly as ``AssistantViewSet`` runs it, or a
sandboxed ``claude`` container via ``NessieAI.cc.cc_engine``. This module keeps
the HTTP surface: auth, session resolution, the ``QueryTask`` row, the event
callback, credentials and the 202 response.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from django.http import Http404, StreamingHttpResponse
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
from nextseek_api.permissions import may_read_any_users_data
from nextseek_api.assistant.descriptions_cc import (
    NESSIE_CC_QUERY_ASYNC_DESC, NESSIE_QUERY_ASYNC_DESC, NESSIE_TASK_PROGRESS_DESC,
)
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback
from nextseek_api.helpers import resolve_seek_auth
from nextseek_api.graph_search.scope import plain_scope

# Reuse the existing assistant's helpers (do NOT redefine its behavior).
from nextseek_api.services.assistant import (
    CsrfExemptSessionAuthentication,
    _error_response,
    _most_recent_session,
)

# The turn body and its helpers live in NessieAI/cc/turn.py (NessieAI Phase B).
# Deliberately no re-export of run_query, run_query_plan or the moved helpers:
# a test patching one of them here must fail loudly, not patch a dead name.
from NessieAI.cc import turn as cc_turn
from NessieAI.cc.cc_provision import ProjectResolutionError


def _iter_and_cleanup(path: Path):
    """Mirror content_blobs._iter_and_cleanup — unlink temp zip after stream."""
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                yield chunk
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _iter_file(path: Path):
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            yield chunk


@extend_schema(tags=["Nessie"])
class CCAssistantViewSet(viewsets.ViewSet):
    """Router + Container-Claude-Code assistant (additive to AssistantViewSet)."""

    authentication_classes = [TokenAuthentication, CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

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
        existing = _most_recent_session(request.user)
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

        # The turn itself (routing, the NS or CC run, the chat_log writes) runs
        # on a daemon thread that NessieAI/cc/turn.py starts. The caller's project
        # scope for graph queries is resolved here, in the request thread, and
        # handed down as plain data (None refuses every graph query).
        cc_turn.start_task(
            request, req, force_cc=force_cc, chat_session=chat_session,
            query_task=query_task, send_event=send_event, adapter=adapter,
            api_user=api_user, api_pass=api_pass,
            resolved_session_id=resolved_session_id,
            graph_scope=plain_scope(request.user),
        )

        return Response(
            AsyncQueryResponse(
                task_id=query_task.task_id, session_id=chat_session.session_id,
            ).model_dump(mode="json"),
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------ routes
    @extend_schema(
        operation_id="CC Assistant: Query (Async, routed)",
        description=NESSIE_QUERY_ASYNC_DESC,
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[OpenApiExample(
            name="Routed query",
            value={"query": "Find me mice treated with NDMA", "mode": "standard"},
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
        description=NESSIE_CC_QUERY_ASYNC_DESC,
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[OpenApiExample(
            name="Turn pinned to the Container-CC engine",
            value={"query": "List the files in my run directory", "mode": "standard"},
            request_only=True,
        )],
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
        description=NESSIE_TASK_PROGRESS_DESC,
        responses={200: TaskProgressResponse},
        examples=[OpenApiExample(
            name="A finished turn",
            value={"task_id": "4a5c12ad-9063-4df1-8439-e201b36bedaf",
                   "session_id": "c0062000-1f4b-4a7e-9d3c-2b8e5a1d7f60",
                   "status": "completed",
                   "progress": [{"event": "route_decided",
                                 "data": {"route": "container_cc", "source": "forced"}}],
                   "result": {"reply": "..."}},
            response_only=True,
        )],
    )
    @action(detail=False, methods=["get"], url_path=r"tasks/(?P<task_id>[0-9a-f-]+)/progress")
    def task_progress(self, request, task_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            _tasks = QueryTask.objects.select_related("session")
            if not may_read_any_users_data(request.user):
                _tasks = _tasks.filter(user=request.user)
            query_task = _tasks.get(task_id=task_id)
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

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        from django.conf import settings
        from rest_framework.response import Response
        from rest_framework import status as drf_status
        from NessieAI.cc.cc_config import CCPaths
        from NessieAI.cc.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_tasks import run_cc_upload_task
        from NessieAI.cc.cc_upload_validate import validate_upload_filename

        uploaded = request.FILES.getlist("file")
        if not uploaded:
            return Response({"error": "no files"}, status=400)
        cap = getattr(settings, "BATCH_UPLOAD_MAX_TOTAL_BYTES", 200 * 1024 * 1024)
        if sum(f.size for f in uploaded) > cap:
            return Response({"error": "upload too large"}, status=413)

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)

        staged = []
        seen_names: set[str] = set()
        stage_root = os.path.join(getattr(settings, "MEDIA_ROOT", "/tmp"), "cc_upload_staging")
        os.makedirs(stage_root, exist_ok=True)
        for f in uploaded:
            safe = validate_upload_filename(getattr(f, "name", ""))
            if safe in seen_names:
                return Response({"error": f"duplicate filename in batch: {safe}"}, status=400)
            seen_names.add(safe)
            tmp = os.path.join(stage_root, f"{int(time.time() * 1000)}_{safe}")
            with open(tmp, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            staged.append({"name": safe, "tmp_path": tmp})

        from nextseek_api.batch_upload.job_index import register_job

        task = run_cc_upload_task.delay(input_mnt=dirs.input_mnt, files=staged)
        register_job(user_id=request.user.pk, job_id=task.id, project_id=int(project.id) if str(project.id).isdigit() else 0)
        return Response({"job_id": task.id, "status": "queued"},
                        status=drf_status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"upload/status/(?P<job_id>[^/.]+)")
    def upload_status(self, request, job_id=None):
        from rest_framework.response import Response
        from celery.result import AsyncResult
        from nextseek_api.batch_upload.celery_app import app as celery_app
        from nextseek_api.batch_upload.job_index import user_owns_job

        if not (user_owns_job(request.user.pk, job_id)
                or may_read_any_users_data(request.user)):
            return Response({"error": "not found"}, status=404)
        r = AsyncResult(job_id, app=celery_app)
        resp = {"job_id": job_id, "state": r.state, "meta": {}, "result": None}
        if r.state == "PROGRESS":
            resp["meta"] = r.info or {}
        elif r.state == "SUCCESS":
            resp["result"] = r.result
        elif r.state == "FAILURE":
            resp["meta"] = {"error": str(r.result)}
        return Response(resp)

    @action(detail=False, methods=["get"], url_path="upload/list")
    def upload_list(self, request):
        from NessieAI.cc.cc_config import CCPaths
        from NessieAI.cc.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from NessieAI.cc.cc_upload_list import list_input_files

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        return Response({"files": list_input_files(dirs.input_mnt)})

    @action(detail=False, methods=["get"], url_path=r"artifacts/(?P<session>[0-9a-f-]+)/download")
    def download_artifact(self, request, session=None):
        from nextseek_api.assistant.models_db import ChatSession
        from NessieAI.cc.cc_config import CCPaths
        from NessieAI.cc.cc_provision import resolve_user_project, build_user_dirs
        from NessieAI.cc.cc_engine import _safe_relpath

        _sessions = ChatSession.objects.all()
        if not may_read_any_users_data(request.user):
            _sessions = _sessions.filter(user=request.user)
        cs = _sessions.filter(session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        key = request.query_params.get("key", "")
        if not key or (key != "all" and not _safe_relpath(key)):
            raise Http404("bad key")

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
        art_dir = Path(dirs.output_mnt) / "artifacts"
        if key == "all":
            turn_id = request.query_params.get("turn_id", "")
            if not turn_id or not _safe_relpath(turn_id):
                raise Http404("bad turn_id")
            art_dir = art_dir / turn_id
            import tempfile
            from NessieAI.cc.cc_artifacts import build_artifact_zip
            # Exclude the per-turn artifacts.zip written by Task 6 into this same
            # art_dir, else key="all" nests the prior zip inside the new one.
            files = [p for p in art_dir.rglob("*") if p.is_file() and p.name != "artifacts.zip"]
            tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
            build_artifact_zip(files, tmp, arc_prefix=art_dir)
            resp = StreamingHttpResponse(_iter_and_cleanup(tmp), content_type="application/zip")
            resp["Content-Disposition"] = 'attachment; filename="artifacts.zip"'
            return resp

        target = resolve_artifact_path(str(art_dir), key)
        if not target.is_file():
            raise Http404("not found")
        resp = StreamingHttpResponse(_iter_file(target), content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="{target.name}"'
        return resp

    @action(detail=False, methods=["get"], url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")
    def recover_transcript(self, request, session=None, turn=None):
        from django.conf import settings
        from django.http import HttpResponse
        from nextseek_api.assistant.models_db import ChatSession, CCSessionTranscript
        from NessieAI.cc.cc_transcript_store import decompress

        _sessions = ChatSession.objects.all()
        if not may_read_any_users_data(request.user):
            _sessions = _sessions.filter(user=request.user)
        cs = _sessions.filter(session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        cc_sid = request.query_params.get("cc_session_id")
        qs = CCSessionTranscript.objects.filter(chat_session=cs, turn_id=turn)
        if cc_sid:
            qs = qs.filter(cc_session_id=cc_sid)
        elif qs.count() > 1:
            return Response({"error": "cc_session_id required"}, status=400)
        row = qs.order_by("-created_at").first()
        if row is None:
            raise Http404("no transcript")
        jsonl = decompress(bytes(row.blob), max_bytes=getattr(settings, "CC_TRANSCRIPT_MAX_BYTES", 256 * 1024 * 1024))
        resp = HttpResponse(jsonl, content_type="application/x-ndjson")
        resp["Content-Disposition"] = f'attachment; filename="transcript-{turn}.jsonl"'
        return resp
