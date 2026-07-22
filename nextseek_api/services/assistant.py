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

import copy
import json
import logging
import os
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

import orjson
from django.http import StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError

from django.conf import settings
from django.core.cache import cache

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
from nextseek_api.assistant.models_api import (
    ApiReadRequest,
    ApiReadResponse,
    ApiWriteRequest,
    ApiWriteResponse,
    EntityOpRequest,
    EntityOpResponse,
    GraphOpRequest,
    GraphOpResponse,
    OpErrorResponse,
    ParseOpRequest,
    ParseOpResponse,
    ReportOpRequest,
    ReportOpResponse,
    RunLsRequest,
    BuildUploadXlsxRequest,
    SubmissionRequest,
    SubmissionResponse,
)
from nextseek_api.assistant.granular import OpValidationError, run_op
from nextseek_api.assistant.write_gate import WriteBlockedError, build_gate, load_allowlist
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.excel_export import extract_table_artifacts
from rest_framework.authentication import (
    BasicAuthentication,
    SessionAuthentication,
    TokenAuthentication,
)

from nextseek_api.helpers import resolve_seek_auth, SeekAPIClient

from chat_nextseek.orchestrator import run_query, run_query_plan, run_pipeline_launch
from chat_nextseek.config import ChatConfig
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

logger = logging.getLogger(__name__)

class UserInParticipatingProject(BasePermission):
    message = "User needs to be in a participating project to use assistant"
    # SEEK project membership changes rarely; cache the positive result briefly so
    # the assistant does not re-fetch the ~16 KB /people/current on every request
    # (the batch-upload flow alone makes dozens of assistant calls per turn).
    _CACHE_TTL_SECONDS = 60

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        cache_key = None
        if user is not None and getattr(user, "is_authenticated", False):
            cache_key = f"assistant_participating:{user.pk}"
            if cache.get(cache_key):
                return True
        try:
            client = SeekAPIClient()
            person = orjson.loads(client.get_current_person(request)[0])
            projects = person['data']['relationships']['projects']['data']
            project_ids = set(map(lambda project: project['id'], projects))
            allowed = project_ids & ASSISTANT_PARTICIPATING_PROJECTS != set()
        except Exception:
            return False
        # Cache only positive results: a transient SEEK failure must re-check, and a
        # newly-added member should not be blocked for the TTL.
        if allowed and cache_key is not None:
            cache.set(cache_key, True, self._CACHE_TTL_SECONDS)
        return allowed

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


def _auto_title_if_unset(chat_session: ChatSession, fallback_query: str = "") -> None:
    """Populate ChatSession.title from the first user query if currently NULL.

    Titles from the first ``user_query`` in ``results_history`` (the NS path).
    Container-CC and out-of-scope turns persist to ``extra_state`` / the
    transcript rather than ``results_history``, so they carry no ``user_query``
    here — for those, fall back to ``fallback_query`` (this turn's query) so
    their chats title too instead of being stuck on "New chat".

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
        first_user_query = (fallback_query or "").strip()
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


# ----------------------------------------------------------------------
# Granular ops (native) — shared helpers
# ----------------------------------------------------------------------

_GRANULAR_REQUEST_MODELS = {
    "entity": EntityOpRequest,
    "parse": ParseOpRequest,
    "graph": GraphOpRequest,
    "api-read": ApiReadRequest,
    "api-write": ApiWriteRequest,
    "report": ReportOpRequest,
    "generate-submission": SubmissionRequest,
    "run-ls": RunLsRequest,
    "build-upload-xlsx": BuildUploadXlsxRequest,
}


def _op_error_response(code: str, detail: str, http_status: int) -> Response:
    """Granular-op error envelope: the NExtSEEK ``errors`` list plus the canonical
    dmac error ``code`` so the dmac thin client can map it to its CLI exit."""
    return Response(
        {"code": code, "errors": [{"title": code, "detail": detail}]},
        status=http_status,
    )


def _granular_chat_config(request, req) -> ChatConfig:
    """Per-request ChatConfig copy carrying the caller's resolved credentials.

    Mirrors the credential handling in ``query``/``query_async`` and
    ``run_query``'s ``copy.copy(config)`` so outbound NExtSEEK calls run as the
    requesting user and the shared singleton is never mutated.
    """
    chat_config = _select_chat_config(request, req)
    basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
    if basic_tuple and basic_tuple[0] and basic_tuple[1]:
        api_user, api_pass = basic_tuple
    else:
        api_user = request.session.get("username")
        api_pass = request.session.get("password")
    prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
    if prod_config is not None and chat_config is prod_config:
        if chat_config.API_USER and chat_config.API_PASS:
            api_user = chat_config.API_USER
            api_pass = chat_config.API_PASS
    cfg = copy.copy(chat_config)
    if api_user:
        cfg.API_USER = api_user
    if api_pass:
        cfg.API_PASS = api_pass
    return cfg


def _granular_args(op: str, req) -> dict:
    """Project a validated request model into the op's chat_nextseek arg dict."""
    if op in ("entity", "parse", "graph"):
        return {"query": req.query}
    if op == "api-read":
        return {"parser_plan": req.parser_plan}
    if op == "api-write":
        return {"parser_plan": req.parser_plan, "confirmed_write": req.confirmed_write,
                "query": req.query}
    if op == "report":
        return {"mode": req.mode, "project": req.project}
    if op == "generate-submission":
        return {"type": req.type, "uids": req.uids, "query": req.query}
    if op == "run-ls":
        return {"run_dir": req.run_dir}
    if op == "build-upload-xlsx":
        return {"rows": req.rows, "existing_parent_uids": req.existing_parent_uids}
    return {}


def _granular_outputs_dir() -> str:
    """A fresh writable run-root for a report op's saved_files."""
    base = getattr(settings, "BASE_DIR", None)
    root = os.path.join(str(base), "outputs", "granular") if base else "outputs/granular"
    out = os.path.join(root, uuid.uuid4().hex)
    os.makedirs(out, exist_ok=True)
    return out


# Content-type by file extension for report artifacts served from disk.
_ARTIFACT_CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
    ".tsv": "text/tab-separated-values",
    ".sdrf": "text/tab-separated-values",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".px": "text/plain",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".xml": "application/xml",
}


def _artifact_content_type(path) -> str:
    ext = os.path.splitext(str(path))[1].lower()
    return _ARTIFACT_CONTENT_TYPES.get(ext, "application/octet-stream")


def _resolve_saved_path(value):
    """A saved_files value is either a string path or a list of paths (multi-file
    keys like geo_seq_workbooks / sra_*). Serve the first concrete path."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        return first if isinstance(first, str) else None
    return None


def _artifact_roots() -> list[Path]:
    """The narrowly-scoped directories report artifacts are written under. Both the
    granular report op (``_granular_outputs_dir``) and the query pipeline
    (``_ensure_query_log_dir``) write beneath ``<BASE_DIR>/outputs`` /
    ``NEXTSEEK_OUTPUTS_DIR``. The whole BASE_DIR (contains source) and home (holds
    secrets) are deliberately excluded."""
    roots: list[Path] = []
    base = getattr(settings, "BASE_DIR", None)
    if base:
        roots.append(Path(base, "outputs").resolve())
    nod = os.environ.get("NEXTSEEK_OUTPUTS_DIR")
    if nod:
        try:
            roots.append(Path(nod).resolve())
        except (OSError, ValueError, RuntimeError):
            pass
    return roots


def _safe_artifact_path(src) -> Path | None:
    """Resolve ``src`` and require it to be *really contained* within an allowed
    artifact root. Uses ``Path.relative_to`` (no string-prefix bypass like
    ``/app-evil`` matching ``/app``) and ``Path.resolve`` (canonicalizes symlinks,
    so a symlink that escapes the root is rejected). Returns the resolved Path when
    safe, else None."""
    if not isinstance(src, str) or not src:
        return None
    try:
        filepath = Path(src).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    for root in _artifact_roots():
        try:
            filepath.relative_to(root)
            return filepath
        except ValueError:
            continue
    return None


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
                    artifacts = entry.get("artifacts") or (extract_table_artifacts(bundle) if bundle else None)
                    turns.append(
                        Turn(
                            bundle_id=bid if bid is not None else 0,
                            user_query=entry.get("user_query", ""),
                            reply=reply,
                            mode=entry.get("mode", ""),
                            ts=entry.get("ts"),
                            artifacts=artifacts or None,
                            cc_traces=entry.get("cc_traces"),
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

        # When the request routed to the prod ChatConfig, swap the
        # session-derived credentials for the prod config's baked-in
        # API_USER/API_PASS. The pipeline's outbound Basic-auth calls must hit
        # prod NExtSEEK with prod credentials — the local session user (e.g.
        # "demo") doesn't exist on prod and would otherwise produce a 401.
        prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
        if prod_config is not None and chat_config is prod_config:
            if chat_config.API_USER and chat_config.API_PASS:
                api_user = chat_config.API_USER
                api_pass = chat_config.API_PASS

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

        # When the request routed to the prod ChatConfig, swap the
        # session-derived credentials for the prod config's baked-in
        # API_USER/API_PASS. The pipeline's outbound Basic-auth calls must hit
        # prod NExtSEEK with prod credentials — the local session user (e.g.
        # "demo") doesn't exist on prod and would otherwise produce a 401.
        prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
        if prod_config is not None and chat_config is prod_config:
            if chat_config.API_USER and chat_config.API_PASS:
                api_user = chat_config.API_USER
                api_pass = chat_config.API_PASS

        def _run_pipeline() -> None:
            try:
                match getattr(req, "mode", "standard"):
                    case "plan":
                        run_query_plan(adapter, chat_config, req.query, send_event, credentials={"api_user": api_user, "api_pass": api_pass})
                    case "pipeline":
                        run_pipeline_launch(adapter, chat_config, req.query, send_event, credentials={"api_user": api_user, "api_pass": api_pass})
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
            filepath = _safe_artifact_path(workbooks[0])
            if filepath is None:
                return _error_response("Forbidden", "File path not within allowed artifact directory.", status.HTTP_403_FORBIDDEN)
            if not filepath.is_file():
                return _error_response("Not found", "GEO workbook file not found on disk.", status.HTTP_404_NOT_FOUND)
            return FileResponse(
                filepath.open("rb"),
                content_type=xlsx_content_type,
                as_attachment=True,
                filename=filepath.name,
            )

        # --- Serve PRIDE submission files (submission.px / SDRF tsv) from disk ---
        if artifact_key in ("pride_submission_px", "pride_sdrf"):
            saved = bundle.get("report_saved_files") or {}
            files = saved.get(artifact_key) or []
            if not files:
                return _error_response("Not found", f"No PRIDE {artifact_key} found.", status.HTTP_404_NOT_FOUND)
            filepath = _safe_artifact_path(files[0])
            if filepath is None:
                return _error_response("Forbidden", "File path not within allowed artifact directory.", status.HTTP_403_FORBIDDEN)
            if not filepath.is_file():
                return _error_response("Not found", "PRIDE submission file not found on disk.", status.HTTP_404_NOT_FOUND)
            content_type = "text/tab-separated-values" if artifact_key == "pride_sdrf" else "text/plain"
            return FileResponse(
                filepath.open("rb"),
                content_type=content_type,
                as_attachment=True,
                filename=filepath.name,
            )

        # --- Generic: serve ANY report_saved_files key as its real on-disk file ---
        # Covers every output type beyond the geo/pride special cases above:
        # merged_report, sra_submission_workbooks, sra_biosample_workbooks,
        # nfcore_* samplesheets, reporter_result, metadata, protocols, etc. The
        # content-type is inferred from the file extension. (geo_seq_workbooks and
        # the pride_* keys are handled by the dedicated branches above and return
        # before reaching here.)
        saved = bundle.get("report_saved_files") or {}
        if artifact_key in saved:
            src = _resolve_saved_path(saved.get(artifact_key))
            if not src:
                return _error_response(
                    "Not found", f"No file for artifact '{artifact_key}'.",
                    status.HTTP_404_NOT_FOUND,
                )
            filepath = _safe_artifact_path(src)
            if filepath is None:
                return _error_response(
                    "Forbidden", "File path not within allowed artifact directory.",
                    status.HTTP_403_FORBIDDEN,
                )
            if not filepath.is_file():
                return _error_response(
                    "Not found", f"Artifact '{artifact_key}' file not found on disk.",
                    status.HTTP_404_NOT_FOUND,
                )
            return FileResponse(
                filepath.open("rb"),
                content_type=_artifact_content_type(filepath),
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

    # ==================================================================
    # Granular ops (native) — entity / parse / graph / api-read /
    # api-write / report / generate-submission.
    #
    # Each op calls the same chat_nextseek portable function the dmac sidecar
    # calls (see nextseek_api/assistant/granular.py), reusing this viewset's
    # auth, _select_chat_config, and the per-request credential copy. Responses
    # use a typed {op, result} envelope; errors carry the canonical dmac code.
    # ==================================================================

    def _granular_session(self, request, req):
        """A read-only session for the parser agent. Reuses an owned ChatSession
        when ``session_id`` is supplied, else a transient (unsaved) one."""
        session_id = getattr(req, "session_id", None)
        chat_session = None
        if session_id:
            try:
                chat_session = ChatSession.objects.get(session_id=session_id, user=request.user)
            except ChatSession.DoesNotExist:
                chat_session = None
        if chat_session is None:
            chat_session = ChatSession(user=request.user)  # transient; not persisted
        return DictSessionAdapter(chat_session)

    def _run_granular_op(self, request, op: str) -> Response:
        authed, err = self._check_auth(request)
        if not authed:
            return err

        model = _GRANULAR_REQUEST_MODELS[op]
        try:
            req = model.model_validate(request.data)
        except ValidationError as e:
            return _op_error_response("VALIDATION", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY)

        chat_config = _granular_chat_config(request, req)
        # parse and graph both run parser_agent, which reads results_history off
        # the session — build a (transient) session for both, else parser_agent
        # crashes on None. Other ops don't touch the session.
        session = self._granular_session(request, req) if op in ("parse", "graph") else None
        gate = build_gate(load_allowlist())
        args = _granular_args(op, req)
        # report + generate-submission both persist real artifacts to disk (the
        # reporter summary / the submission-emitter workbooks), so both need a
        # writable run-root under an allowed artifact root.
        outputs_dir = _granular_outputs_dir() if op in ("report", "generate-submission", "build-upload-xlsx") else None

        try:
            result = run_op(
                op, args, config=chat_config, session=session,
                write_gate=gate, outputs_dir=outputs_dir,
            )
        except OpValidationError as e:
            return _op_error_response("VALIDATION", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY)
        except WriteBlockedError as e:
            return _op_error_response("WRITE_BLOCKED", str(e), status.HTTP_403_FORBIDDEN)
        except Exception as e:  # noqa: BLE001 — any agent failure maps to AGENT_FAILED
            logger.exception("granular op %s failed", op)
            return _op_error_response("AGENT_FAILED", str(e), status.HTTP_502_BAD_GATEWAY)

        resp_body = {"op": op, "result": result}
        # report/generate-submission produce artifacts; register a bundle so they
        # are fetchable over HTTP via download_artifact, and hand back the URLs.
        if op in ("report", "generate-submission", "build-upload-xlsx"):
            resp_body["download"] = self._register_artifact_bundle(request, req, op, result)
        return Response(resp_body, status=status.HTTP_200_OK)

    def _register_artifact_bundle(self, request, req, op: str, result) -> dict:
        """Persist a lightweight bundle in the caller's chat session so the op's
        outputs are downloadable via the (ownership-checked) download_artifact
        endpoint. Returns ``{session_id, bundle_id, artifacts:[{key,url}]}``."""
        session_id = getattr(req, "session_id", None)
        chat_session = None
        if session_id:
            try:
                chat_session = ChatSession.objects.get(session_id=session_id, user=request.user)
            except ChatSession.DoesNotExist:
                chat_session = None
        if chat_session is None:
            chat_session = ChatSession.objects.create(user=request.user)

        history = chat_session.results_history or []
        bundle_id = max((b.get("id", 0) for b in history if isinstance(b, dict)), default=0) + 1
        saved_files = result.get("saved_files") if isinstance(result, dict) else None
        if op == "generate-submission":
            # real emitter workbooks in saved_files PLUS the on-the-fly all_tables xlsx.
            bundle = {"id": bundle_id, "mode": "generate-submission",
                      "report_saved_files": saved_files or {}, "report_writer_output": result}
        else:  # report / build-upload-xlsx — saved_files (report file / reingest workbooks)
               # are served directly; no writer-output payload.
            bundle = {"id": bundle_id,
                      "mode": "reingest" if op == "build-upload-xlsx" else "reporter",
                      "report_saved_files": saved_files or {}, "report_writer_output": {}}
        history.append(bundle)
        chat_session.results_history = history
        chat_session.save(update_fields=["results_history", "updated_at"])

        base = (f"/nextseek_api/assistant/sessions/{chat_session.session_id}"
                f"/bundles/{bundle_id}/artifacts")
        artifacts = [{"key": k, "url": f"{base}/{k}/"} for k in (saved_files or {})]
        if op == "generate-submission":
            # The submission output has no on-disk file; expose it as a combined xlsx.
            artifacts.append({"key": "all_tables", "url": f"{base}/all_tables/"})
        return {"session_id": str(chat_session.session_id), "bundle_id": bundle_id,
                "artifacts": artifacts}

    @extend_schema(
        operation_id="Assistant: Entity Extract",
        description="Resolve sampletypes/assays/keywords from a query (entity_agent).",
        tags=["Assistant"],
        request=EntityOpRequest,
        responses={200: EntityOpResponse, 401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="entity")
    def entity(self, request):
        return self._run_granular_op(request, "entity")

    @extend_schema(
        operation_id="Assistant: Parse",
        description="Build a parser plan for a query (entity_agent -> parser_agent).",
        tags=["Assistant"],
        request=ParseOpRequest,
        responses={200: ParseOpResponse, 401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="parse")
    def parse(self, request):
        return self._run_granular_op(request, "parse")

    @extend_schema(
        operation_id="Assistant: Graph",
        description="Build a Cypher plan (graph_agent) and execute it against Neo4j.",
        tags=["Assistant"],
        request=GraphOpRequest,
        responses={200: GraphOpResponse, 401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="graph")
    def graph(self, request):
        return self._run_granular_op(request, "graph")

    @extend_schema(
        operation_id="Assistant: API Read",
        description="Build an API request from a parser plan and execute a read-safe call.",
        tags=["Assistant"],
        request=ApiReadRequest,
        responses={200: ApiReadResponse, 401: OpErrorResponse, 403: OpErrorResponse,
                   422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="api-read")
    def api_read(self, request):
        return self._run_granular_op(request, "api-read")

    @extend_schema(
        operation_id="Assistant: API Write",
        description=(
            "Execute a write API call from a parser plan. Gated: runs only when "
            "confirmed_write is the boolean true; otherwise returns WRITE_BLOCKED."
        ),
        tags=["Assistant"],
        request=ApiWriteRequest,
        responses={200: ApiWriteResponse, 401: OpErrorResponse, 403: OpErrorResponse,
                   422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="api-write")
    def api_write(self, request):
        return self._run_granular_op(request, "api-write")

    @extend_schema(
        operation_id="Assistant: Report",
        description="Run a summary report (samples/protocols/published/rppr) via run_reporter_summary.",
        tags=["Assistant"],
        request=ReportOpRequest,
        responses={200: ReportOpResponse, 401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="report")
    def report(self, request):
        return self._run_granular_op(request, "report")

    @extend_schema(
        operation_id="Assistant: Generate Submission",
        description="Generate a repository submission report (GEO/SRA/NFCORE/PRIDE) via report_writer_agent.",
        tags=["Assistant"],
        request=SubmissionRequest,
        responses={200: SubmissionResponse, 401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="generate-submission")
    def generate_submission(self, request):
        return self._run_granular_op(request, "generate-submission")

    @extend_schema(
        operation_id="Assistant: Run Ls",
        description="Recursive read-only listing (ls -laR) of a finished Luria run dir (reingest step 1).",
        tags=["Assistant"],
        request=RunLsRequest,
        responses={401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="run-ls")
    def run_ls(self, request):
        return self._run_granular_op(request, "run-ls")

    @extend_schema(
        operation_id="Assistant: Build Upload Xlsx",
        description="Render NExtSEEK 4-sheet upload workbook(s) from reingest rows (one per sample type).",
        tags=["Assistant"],
        request=BuildUploadXlsxRequest,
        responses={401: OpErrorResponse, 422: OpErrorResponse},
    )
    @action(detail=False, methods=["post"], url_path="build-upload-xlsx")
    def build_upload_xlsx(self, request):
        return self._run_granular_op(request, "build-upload-xlsx")
