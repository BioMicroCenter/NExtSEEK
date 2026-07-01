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
import os
import threading
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
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback
from nextseek_api.helpers import resolve_seek_auth

# Reuse the existing assistant's helpers (do NOT redefine its behavior).
from nextseek_api.services.assistant import (
    CsrfExemptSessionAuthentication,
    _auto_title_if_unset,
    _error_response,
    _select_chat_config,
)

from chat_nextseek.orchestrator import run_query, run_query_plan

from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant import cc_config
from nextseek_api.cc_assistant import cc_session
from nextseek_api.cc_assistant import cc_summary
from nextseek_api.cc_assistant import cc_memory
from nextseek_api.cc_assistant import cc_memory_io
from nextseek_api.cc_assistant.cc_provision import ProjectResolutionError

from nextseek_api.cc_assistant.cc_turn_complete import (
    TurnCompletePayload,
    apply_turn_to_extra_state,
)
from nextseek_api.cc_assistant import cc_transcript_store
from nextseek_api.assistant.models_db import CCSessionTranscript

MAX_CC_CHAT_LOG_TURNS = 50  # match chat_nextseek/chat_memory.py MAX_TURNS


def _append_cc_turn_complete(payload: TurnCompletePayload) -> None:
    session = payload.chat_session
    session.extra_state = apply_turn_to_extra_state(
        session.extra_state, payload, cap=MAX_CC_CHAT_LOG_TURNS)
    session.save(update_fields=["extra_state", "updated_at"])
    CCSessionTranscript.objects.update_or_create(
        chat_session=session,
        cc_session_id=payload.cc_session_id or "",
        turn_id=payload.turn_id,
        defaults={
            "blob": cc_transcript_store.compress(payload.raw_jsonl),
            "uncompressed_size": len(payload.raw_jsonl),
        },
    )


logger = logging.getLogger(__name__)


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


def _persist_summary_standalone(user, session_id, summary_dict, fp):
    """Single-key read-modify-write on extra_state; never clobber sibling keys."""
    try:
        sess = ChatSession.objects.get(session_id=session_id, user=user)
        es = dict(sess.extra_state or {})
        es["summary"] = summary_dict
        es["summary_fingerprint"] = fp
        sess.extra_state = es
        sess.save(update_fields=["extra_state", "updated_at"])
    except Exception:
        logger.exception("cc-1c: failed to persist summary for %s", session_id)


def _session_metas(user, current_id, paths, mem_cfg, project_dirname=None):
    """Build cc_memory.SessionMeta for the user's sessions (own sessions only)."""
    from pathlib import Path
    from nextseek_api.cc_assistant.cc_provision import build_user_dirs

    metas = []
    qs = ChatSession.objects.filter(user=user).order_by("-updated_at")
    for s in qs:
        sid = str(s.session_id)
        es = s.extra_state or {}
        session_project = es.get("cc_project_dirname") or project_dirname
        if not session_project:
            continue
        dirs = build_user_dirs(paths, session_project, user.username, session_id=sid)
        store = Path(dirs.cc_state_mnt) / "projects"
        jsonls = sorted(store.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                        reverse=True) if store.is_dir() else []
        transcript_mount_path = str(jsonls[0]) if jsonls else None
        host_path = None
        if transcript_mount_path:
            host_path = transcript_mount_path.replace(
                paths.user_root_mount.rstrip("/"), paths.host_user_root.rstrip("/"), 1)
        prev_fp = es.get("summary_fingerprint")
        changed = False
        if transcript_mount_path:
            try:
                raw = Path(transcript_mount_path).read_bytes()
                changed = cc_summary.is_changed(prev_fp, cc_summary.fingerprint(raw))
            except OSError:
                changed = False
        metas.append(cc_memory.SessionMeta(
            session_id=sid, updated_at=s.updated_at.timestamp(),
            fingerprint=prev_fp, summary=es.get("summary"),
            transcript_path=host_path, changed=changed))
    return metas


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
        user_api_user, user_api_pass = api_user, api_pass
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
                        try:
                            chat_session.extra_state["cc_session_id"] = cc_sid
                            chat_session.save(update_fields=["extra_state", "updated_at"])
                        except Exception:
                            logger.exception(
                                "cc: failed to persist cc_session_id=%r; resume unavailable this turn",
                                cc_sid,
                            )

                    cc_send = cc_session.make_session_sniffer(send_event, _persist_cc_session)

                    paths = cc_config.CCPaths.from_env()
                    from nextseek_api.cc_assistant.cc_provision import (
                        ProjectResolutionError,
                        build_user_dirs,
                        resolve_user_project,
                    )
                    try:
                        project = resolve_user_project(user_api_user, user_api_pass)
                    except ProjectResolutionError as exc:
                        logger.warning("cc-step2: project resolution failed: %s", exc)
                        send_event("query_error", {
                            "error": (
                                "Could not resolve your SEEK project. "
                                "Please try again shortly."
                            ),
                            "agent": "container_cc",
                            "session_id": resolved_session_id,
                        })
                        return
                    stored_project_dirname = (chat_session.extra_state or {}).get("cc_project_dirname")
                    if stored_project_dirname and stored_project_dirname != project.dirname:
                        logger.warning(
                            "cc-step2: stored project dirname %r no longer matches resolved %r",
                            stored_project_dirname,
                            project.dirname,
                        )
                        send_event("query_error", {
                            "error": (
                                "Your SEEK project membership changed for this chat. "
                                "Please start a new chat."
                            ),
                            "agent": "container_cc",
                            "session_id": resolved_session_id,
                        })
                        return
                    project_dirname = stored_project_dirname or project.dirname
                    try:
                        if not (chat_session.extra_state or {}).get("cc_project_dirname"):
                            chat_session.extra_state["cc_project_dirname"] = project_dirname
                            chat_session.save(update_fields=["extra_state", "updated_at"])
                    except Exception:
                        logger.exception("cc-step2: failed to persist project dirname")

                    mem_cfg = cc_config.CCMemoryConfig.from_env()
                    fresh = bool(getattr(req, "fresh_session", False))
                    user_memory_file = None
                    transcripts_dir = None
                    if not fresh:
                        from pathlib import Path
                        from django.utils import timezone

                        metas = _session_metas(
                            request.user, cc_state_key, paths, mem_cfg, project_dirname)
                        tgt = cc_memory.select_sync_target(metas, current_id=cc_state_key)
                        if tgt is not None and tgt.transcript_path:
                            try:
                                mount_path = tgt.transcript_path.replace(
                                    paths.host_user_root.rstrip("/"),
                                    paths.user_root_mount.rstrip("/"), 1)
                                raw = Path(mount_path).read_bytes()
                                prov = cc_summary.SummaryProvenance(
                                    chat_session_id=tgt.session_id,
                                    claude_session_id=(tgt.summary or {}).get("claude_session_id"),
                                    transcript_path=tgt.transcript_path,
                                    chat_model=cc_router._resolve_cc_model_id() or "",
                                    generated_at=timezone.now().isoformat())
                                summary = cc_summary.summarize_transcript(raw, prov, mem_cfg)
                                _persist_summary_standalone(
                                    request.user, tgt.session_id,
                                    summary.model_dump(),
                                    cc_summary.fingerprint(raw))
                                metas = _session_metas(
                                    request.user, cc_state_key, paths, mem_cfg, project_dirname)
                            except Exception:
                                logger.exception("cc-1c: sync summarize failed; continuing")

                        window = cc_memory.select_window(
                            metas, current_id=cc_state_key, window_size=mem_cfg.window_size)
                        dirs = build_user_dirs(
                            paths, project_dirname, request.user.username,
                            session_id=cc_state_key)
                        mem_root = Path(dirs.memory_mnt)
                        md = cc_memory.render_memory(
                            window, fresh_session=False,
                            transcripts_mount=cc_engine._CONTAINER_MEMORY_TRANSCRIPTS)
                        written = cc_memory_io.write_memory_file(mem_root / "CLAUDE.md", md)
                        staged = cc_memory_io.stage_transcripts(window, mem_root / "transcripts")
                        if written:
                            user_memory_file = str(written).replace(
                                paths.user_root_mount.rstrip("/"),
                                paths.host_user_root.rstrip("/"), 1)
                        if staged:
                            transcripts_dir = str(staged).replace(
                                paths.user_root_mount.rstrip("/"),
                                paths.host_user_root.rstrip("/"), 1)

                    cc_engine.run_cc_turn(
                        query=req.query, model_id=decision.model_id,
                        send_event=cc_send,
                        user_id=cc_user_id,
                        project_dirname=project_dirname,
                        run_id=cc_run_id,
                        paths=paths,
                        session_id=prior_id,
                        cc_state_key=cc_state_key,
                        user_memory_file=user_memory_file,
                        transcripts_dir=transcripts_dir,
                        api_user=user_api_user, api_pass=user_api_pass,
                        chat_session=chat_session,
                        user_query=req.query or "",
                        on_turn_complete=_append_cc_turn_complete,
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

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        from django.conf import settings
        from rest_framework.response import Response
        from rest_framework import status as drf_status
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_tasks import run_cc_upload_task
        from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename

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

        if not user_owns_job(request.user.pk, job_id):
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
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_list import list_input_files

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
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import resolve_user_project, build_user_dirs
        from nextseek_api.cc_assistant.cc_engine import _safe_relpath

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
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
            from nextseek_api.cc_assistant.cc_artifacts import build_artifact_zip
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
        from nextseek_api.cc_assistant.cc_transcript_store import decompress

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
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
