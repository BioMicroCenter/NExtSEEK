"""Nessie admin surface.

Holds the engine-agnostic session-inspection endpoint. The ``nessie`` prefix is
the namespace the assistant routes are converging on: today the assistant is
split across ``assistant/`` (the NExtSEEK engine) and ``cc-assistant/`` (the
Container-CC engine), a division that reflects how the code grew rather than
anything a caller cares about.

This ViewSet is superuser-only and deliberately ignores session ownership --
reading another user's session is the entire point. See
``nextseek_api/assistant/session_debug.py`` for the projection, which returns
sizes and paths rather than payloads.
"""
from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.authentication import BasicAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.throttles import TURN_THROTTLES, UploadRate, enforce
from nextseek_api.assistant import session_debug
from nextseek_api.assistant.descriptions_cc import (
    NESSIE_ARTIFACTS_DESC, NESSIE_CC_QUERY_ASYNC_DESC, NESSIE_QUERY_ASYNC_DESC,
    NESSIE_TASK_PROGRESS_DESC, NESSIE_TRANSCRIPT_DESC, NESSIE_UPLOAD_DESC,
    NESSIE_UPLOAD_LIST_DESC, NESSIE_UPLOAD_STATUS_DESC,
)
from nextseek_api.assistant.models_api import AsyncQueryResponse, QueryRequest, TaskProgressResponse
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.endpoint_descriptions import NESSIE_SESSION_DEBUG_DESC
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
from nextseek_api.services.cc_assistant import CCAssistantViewSet

_DEBUG_EXAMPLE = {
    "resolved_as": "session",
    "session": {
        "session_id": "4a5c12ad-9063-4df1-8439-e201b36bedaf",
        "title": "NHP sequencing samples",
        "user": {"id": 12, "username": "labuser"},
        "created_at": "2026-09-07T16:38:56",
        "updated_at": "2026-09-07T16:39:03",
        "counts": {"bundles": 1, "chat_log_entries": 1, "tasks": 1,
                   "ledger_turns": 1, "cc_transcripts": 1, "files": 2},
    },
    "sizes": {"results_history_bytes": 288291, "extra_state_bytes": 4120,
              "last_debug_bytes": 917, "largest_bundle_bytes": 288103,
              "cc_transcript_uncompressed_total": 51204},
    "warnings": [
        {"code": "column_over_sort_buffer",
         "detail": "results_history_bytes=288291 is over sort_buffer_size "
                   "(262144); a query that sorts on it can fail with error 1038."}
    ],
}


class NessieViewSet(viewsets.ViewSet):
    """Superuser-only inspection of the Nessie assistant's stored state."""

    # BasicAuthentication FIRST: DRF takes the challenge header from
    # authenticators[0], and SessionAuthentication returns None for it, which
    # turns an anonymous call into a bare 403 instead of a 401. No
    # TokenAuthentication -- token auth does not work in this project.
    authentication_classes = [BasicAuthentication, CsrfExemptSessionAuthentication]
    # IsAuthenticated first so anonymous callers get the 401. IsSuperUser, not
    # DRF's IsAdminUser: dmac/views.py sets is_staff on every SEEK user at
    # login, so IsAdminUser here would collapse to IsAuthenticated.
    permission_classes = [IsAuthenticated, IsSuperUser]

    @extend_schema(
        operation_id="Nessie: Session Debug",
        description=NESSIE_SESSION_DEBUG_DESC,
        tags=["Nessie"],
        parameters=[
            OpenApiParameter(
                name="include", location=OpenApiParameter.QUERY, required=False, type=str,
                description="Comma-separated subset of transcripts,bundles,progress,"
                            "last_debug,all. Opts into bulk payloads.",
            ),
            OpenApiParameter(
                name="turn", location=OpenApiParameter.QUERY, required=False, type=int,
                description="Limit `turns` to this single index.",
            ),
        ],
        responses={200: dict},
        examples=[
            OpenApiExample(
                name="A session whose results_history is over sort_buffer_size",
                value=_DEBUG_EXAMPLE,
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["get"],
            url_path=r"sessions/(?P<session_id>[0-9a-fA-F-]+)/debug")
    def session_debug(self, request, session_id=None):
        session = ChatSession.objects.filter(session_id=session_id).first()
        resolved_as = "session"
        if session is None:
            # A task id is what appears in logs and error reports, so accept one
            # rather than making the caller look the session up first.
            task = QueryTask.objects.filter(task_id=session_id).select_related(
                "session").first()
            if task is None:
                return Response(
                    {"errors": [{"title": "Not found",
                                 "detail": "No chat session or query task with that id."}]},
                    status=status.HTTP_404_NOT_FOUND,
                )
            session, resolved_as = task.session, "task"

        include = {p.strip() for p in request.query_params.get("include", "").split(",")
                   if p.strip()}
        payload = session_debug.collect(session, include=include)
        payload["resolved_as"] = resolved_as

        turn = request.query_params.get("turn")
        if turn is not None and turn.lstrip("-").isdigit():
            idx = int(turn)
            payload["turns"] = [t for t in payload["turns"] if t.get("index") == idx]

        return Response(payload, status=status.HTTP_200_OK)


def _delegate(viewset_cls, action_name: str, request, **kwargs):
    """Run an action of another ViewSet against this request.

    The ``nessie/`` paths are aliases, not reimplementations: there is exactly
    one copy of each behaviour and these routes reach it. Only ``request`` and
    the URL kwargs are needed by the delegated actions, so a bare instance with
    ``request``/``action`` populated is enough -- ``permission_classes`` on the
    target class are NOT re-run here, so the alias must carry the same gate as
    the route it forwards to.
    """
    vs = viewset_cls()
    vs.request = request
    vs.action = action_name
    vs.args = ()
    vs.kwargs = kwargs
    vs.format_kwarg = None
    return getattr(vs, action_name)(request, **kwargs)


@extend_schema(tags=["Nessie"])
class NessieChatViewSet(viewsets.ViewSet):
    """The ``nessie/`` names for the chat surface.

    Every action here forwards to the implementation on ``CCAssistantViewSet``.
    The old paths keep working: consumers span two container images and a
    committed JS bundle that do not deploy together, so the rename lands as an
    alias and the old routes are retired once those have moved.

    Authenticated, NOT superuser -- these are ordinary chat routes, and the
    gate must match the route each one forwards to.
    """

    authentication_classes = [BasicAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Nessie: Query",
        description=NESSIE_QUERY_ASYNC_DESC,
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[OpenApiExample(
            name="Routed query", request_only=True,
            value={"query": "Find me mice treated with NDMA", "mode": "standard"},
        )],
    )
    @action(detail=False, methods=["post"], url_path="query", throttle_classes=TURN_THROTTLES)
    def query(self, request):
        return _delegate(CCAssistantViewSet, "query_async", request)

    @extend_schema(
        operation_id="Nessie: Query (force Container-CC)",
        description=NESSIE_CC_QUERY_ASYNC_DESC,
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[OpenApiExample(
            name="Turn pinned to the Container-CC engine", request_only=True,
            value={"query": "List the files in my run directory", "mode": "standard"},
        )],
    )
    @action(detail=False, methods=["post"], url_path="query/cc", throttle_classes=TURN_THROTTLES)
    def query_cc(self, request):
        return _delegate(CCAssistantViewSet, "cc_query_async", request)

    @extend_schema(
        operation_id="Nessie: Task Progress",
        description=NESSIE_TASK_PROGRESS_DESC,
        responses={200: TaskProgressResponse},
        examples=[OpenApiExample(
            name="A finished turn", response_only=True,
            value={"task_id": "4a5c12ad-9063-4df1-8439-e201b36bedaf",
                   "session_id": "c0062000-1f4b-4a7e-9d3c-2b8e5a1d7f60",
                   "status": "completed", "progress": [], "result": {"reply": "..."}},
        )],
    )
    @action(detail=False, methods=["get"],
            url_path=r"tasks/(?P<task_id>[0-9a-f-]+)/progress")
    def task_progress(self, request, task_id=None):
        return _delegate(CCAssistantViewSet, "task_progress", request, task_id=task_id)

    # GET owns the @action and POST hangs off it via .mapping. TWO @action
    # decorators sharing one url_path do NOT combine: the router emits a pattern
    # per action and the first registered wins, so the other verb answers 405.
    # Same shape as AssistantViewSet.list_sessions / create_session.
    @extend_schema(
        operation_id="Nessie: Upload List",
        description=NESSIE_UPLOAD_LIST_DESC,
        responses={200: dict},
        examples=[OpenApiExample(
            name="Two staged inputs", response_only=True,
            value={"files": ["reads_R1.fastq.gz", "reads_R2.fastq.gz"]})],
    )
    @action(detail=False, methods=["get"], url_path="uploads")
    def uploads_list(self, request):
        return _delegate(CCAssistantViewSet, "upload_list", request)

    @extend_schema(
        operation_id="Nessie: Upload",
        description=NESSIE_UPLOAD_DESC,
        # Multipart with no serializer: declare both sides explicitly, or
        # spectacular emits no content block and the example has nowhere to land.
        request={"multipart/form-data": {
            "type": "object",
            "properties": {"file": {"type": "array",
                                    "items": {"type": "string", "format": "binary"}}},
        }},
        responses={202: OpenApiResponse(
            response={"type": "object",
                      "properties": {"job_id": {"type": "string"},
                                     "status": {"type": "string"}}},
            description="Upload accepted and queued.")},
        examples=[OpenApiExample(
            name="Queued", response_only=True,
            status_codes=["202"], media_type="application/json",
            value={"job_id": "c3f1a2b4-9e17-4a02-8d55-0b1f2c3d4e5f",
                   "status": "queued"})],
    )
    @uploads_list.mapping.post
    def uploads_create(self, request):
        enforce(request, self, UploadRate())  # the forwarded action's own throttles do not run
        return _delegate(CCAssistantViewSet, "upload", request)

    @extend_schema(
        operation_id="Nessie: Upload Status",
        description=NESSIE_UPLOAD_STATUS_DESC,
        responses={200: dict},
        examples=[OpenApiExample(
            name="In progress", response_only=True,
            value={"job_id": "c3f1a2b4", "state": "PROGRESS",
                   "meta": {"done": 3, "total": 8}, "result": None})],
    )
    @action(detail=False, methods=["get"], url_path=r"uploads/(?P<job_id>[^/.]+)")
    def uploads_status(self, request, job_id=None):
        return _delegate(CCAssistantViewSet, "upload_status", request, job_id=job_id)

    @extend_schema(
        operation_id="Nessie: Session Artifacts",
        description=NESSIE_ARTIFACTS_DESC,
        responses={200: bytes},
        examples=[OpenApiExample(
            name="One artifact by key", response_only=True,
            value="<binary attachment>")],
    )
    @action(detail=False, methods=["get"],
            url_path=r"sessions/(?P<session>[0-9a-f-]+)/artifacts")
    def session_artifacts(self, request, session=None):
        return _delegate(CCAssistantViewSet, "download_artifact", request, session=session)

    @extend_schema(
        operation_id="Nessie: Session Transcript",
        description=NESSIE_TRANSCRIPT_DESC,
        responses={200: bytes},
        examples=[OpenApiExample(
            name="One turn's transcript", response_only=True,
            value='{"type":"assistant","message":{"role":"assistant"}}')],
    )
    @action(detail=False, methods=["get"],
            url_path=r"sessions/(?P<session>[0-9a-f-]+)/transcript/(?P<turn>[^/.]+)")
    def session_transcript(self, request, session=None, turn=None):
        return _delegate(CCAssistantViewSet, "recover_transcript", request,
                         session=session, turn=turn)
