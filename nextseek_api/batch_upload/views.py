"""DRF ViewSet for batch upload endpoints."""
from __future__ import annotations

import logging
import os
import json
import time

from celery.result import AsyncResult
from django.conf import settings
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from pydantic import BaseModel, Field
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, TokenAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

from .celery_app import app as celery_app
from .error_helpers import (
    pydantic_errors_to_api_errors,
    v1_or_v2_error,
)
from .job_index import list_jobs, register_job, user_owns_job
from .models import InputRowModel
from .tasks import run_batch_upload_task
from nextseek_api.errors import api_errors
from nextseek_api.endpoint_descriptions import (
    BATCH_UPLOAD_START_DESC,
    BATCH_UPLOAD_STATUS_DESC,
    BATCH_UPLOAD_CANCEL_DESC,
    BATCH_UPLOAD_SUMMARY_DESC,
    BATCH_UPLOAD_LIST_DESC,
)

log = logging.getLogger(__name__)


# ── Request/Response models for OpenAPI docs ──────────────────────────────


class BatchUploadStartRequest(BaseModel):
    rows: list[InputRowModel] | None = Field(
        None,
        description=(
            "List of InputRowModel objects for direct programmatic upload. "
            "Bypasses Excel file parsing. Each object must have at minimum: "
            "SampleType (str), json_metadata (str or dict). "
            "Optional: UID, assay_ids, project_id (overrides top-level), "
            "study_title, study_id, sop_id, assay_titles."
        ),
    )
    project_id: int = Field(..., description="SEEK project ID to link samples to.")
    update_existing: bool = Field(
        False,
        description="If true, existing samples (by UUID or Name match) are updated instead of skipped.",
    )
    neo4j_only: bool = Field(
        False,
        description=(
            "If true, skip SQL INSERT/UPDATE stages and only sync existing samples to Neo4j. "
            "Assumes all samples already exist in the database. Rows must have pre-assigned UIDs."
        ),
    )
    person_id: int | None = Field(
        None,
        description="SEEK person ID for contributor. Optional; defaults to authenticated user.",
    )
    config_overrides: dict = Field(
        default_factory=dict,
        description="Optional config overrides (e.g., max_rows_per_batch, enable_auto_permissions)",
    )


class BatchUploadStartResponse(BaseModel):
    job_id: str = Field(..., description="Celery task ID for tracking")
    status: str = Field("queued", description="Initial job status")


class BatchUploadStatusResponse(BaseModel):
    job_id: str
    state: str = Field(..., description="PENDING, STARTED, PROGRESS, SUCCESS, FAILURE, REVOKED")
    meta: dict = Field(default_factory=dict, description="Progress metadata")
    result: dict = Field(default=None, description="Final result (when SUCCESS)")


# ── ViewSet ───────────────────────────────────────────────────────────────


class BatchUploadViewSet(viewsets.ViewSet):
    """Batch upload pipeline for bulk sample creation.

    Requires authentication. Dispatches work to a Celery worker
    and provides status tracking, cancellation, and result download.
    """

    authentication_classes = [TokenAuthentication, CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _check_ownership(self, request, job_id):
        """Return 404 Response if user doesn't own this job, else None."""
        if not user_owns_job(request.user.pk, job_id):
            return v1_or_v2_error(
                request,
                v1_body={"detail": "Not found."},
                v1_status=status.HTTP_404_NOT_FOUND,
                v2={"title": "Not found", "detail": "No job with that id for this user."},
            )
        return None

    @extend_schema(
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "One or more Excel (.xlsx) files to upload.",
                    },
                    "project_id": {
                        "type": "integer",
                        "description": "SEEK project ID to link samples to.",
                    },
                    "person_id": {
                        "type": "integer",
                        "description": "SEEK person ID for contributor. Optional; defaults to authenticated user.",
                    },
                    "update_existing": {
                        "type": "boolean",
                        "description": "If true, update existing samples instead of skipping. Default: false.",
                        "default": False,
                    },
                    "neo4j_only": {
                        "type": "boolean",
                        "description": "If true, skip SQL INSERT/UPDATE and only sync existing samples to Neo4j. Default: false.",
                        "default": False,
                    },
                    "config_overrides": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Optional config overrides (JSON object).",
                    },
                },
                "required": ["project_id"],
            },
            "application/json": BatchUploadStartRequest,
        },
        responses={
            202: OpenApiResponse(
                response=BatchUploadStartResponse,
                examples=[
                    OpenApiExample(
                        name="v2 start accepted",
                        value={"job_id": "abc-123", "status": "queued"},
                    ),
                ],
            ),
            400: OpenApiResponse(description="Missing input or structural error"),
            401: OpenApiResponse(description="Authentication required"),
            413: OpenApiResponse(description="Total upload size exceeds limit"),
            422: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Pydantic validation error on rows or request body",
                examples=[
                    OpenApiExample(
                        name="v2 validation error (422)",
                        value={"errors": [{
                            "status": "422",
                            "title": "Field required",
                            "source": {"pointer": "/data/attributes/rows/0/SampleType"},
                            "meta": {"pydantic_type": "missing"},
                        }]},
                    ),
                ],
            ),
        },
        description=BATCH_UPLOAD_START_DESC,
    )
    @action(detail=False, methods=["post"], url_path="start")
    def start(self, request):
        """POST /api/batch-upload/start/ -- dispatch a batch upload job.

        Two input modes:
        1. Direct rows: JSON body with 'rows' containing List[InputRowModel]
        2. File upload: multipart/form-data with one or more .xlsx files under 'file' key

        If both are provided, rows takes priority (files are ignored with a warning).
        """
        uploaded_files = request.FILES.getlist("file")
        raw_rows = request.data.get("rows")
        xlsx_paths = []
        validated_rows = None
        input_warnings = []

        # Capture raw project_id for downstream use. Pre-validation below fires
        # only when rows is well-shaped (a list or omitted); when rows is the
        # wrong outer type, Pydantic owns the 422 surface.
        raw_pid = request.data.get("project_id") if hasattr(request.data, "get") else None
        rows_is_list_or_absent = (raw_rows is None) or isinstance(raw_rows, list)

        # Pre-validate project_id: missing or non-int returns 400 (not 422), per v2 contract.
        # Only fires when rows has correct outer shape — preserves Pydantic 422 path for
        # genuinely malformed rows payloads.
        if rows_is_list_or_absent:
            if raw_pid is None:
                return v1_or_v2_error(
                    request,
                    v1_body={"detail": "project_id is required"},
                    v1_status=status.HTTP_400_BAD_REQUEST,
                    v2={
                        "title": "project_id is required",
                        "pointer": "/data/attributes/project_id",
                        "example": 12,
                    },
                )
            try:
                int(raw_pid)
            except (TypeError, ValueError):
                return v1_or_v2_error(
                    request,
                    v1_body={"detail": "project_id must be an integer"},
                    v1_status=status.HTTP_400_BAD_REQUEST,
                    v2={
                        "title": "project_id must be an integer",
                        "pointer": "/data/attributes/project_id",
                        "example": 12,
                        "valid_values": ["12", "47", "(any positive integer SEEK project id)"],
                    },
                )

        # ── Mode 1: Direct rows (JSON, checked first -- rows wins) ──
        if raw_rows is not None:
            # Validate entire JSON body via request model
            from pydantic import ValidationError as PydanticValidationError

            try:
                req = BatchUploadStartRequest.model_validate(request.data)
            except PydanticValidationError as e:
                if getattr(request, "version", None) == "v2":
                    return api_errors(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        pydantic_errors_to_api_errors(e),
                    )
                return Response(
                    {"detail": f"Request validation failed: {e.error_count()} error(s)", "errors": e.errors()},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            validated_rows = req.rows
            if not validated_rows:
                return v1_or_v2_error(
                    request,
                    v1_body={"detail": "rows must be a non-empty list of InputRowModel objects."},
                    v1_status=status.HTTP_400_BAD_REQUEST,
                    v2={
                        "title": "rows must be a non-empty list of InputRowModel objects.",
                        "pointer": "/data/attributes/rows",
                    },
                )
            if uploaded_files:
                input_warnings.append("Both 'rows' and file uploads provided; using rows, ignoring files.")

        # ── Mode 2: File upload (multipart) ──
        elif uploaded_files:
            total_size = 0
            for uf in uploaded_files:
                if not (uf.name or "").lower().endswith(".xlsx"):
                    return v1_or_v2_error(
                        request,
                        v1_body={"detail": "Only .xlsx files are accepted."},
                        v1_status=status.HTTP_400_BAD_REQUEST,
                        v2={
                            "title": "Only .xlsx files are accepted.",
                            "pointer": "/data/attributes/file",
                            "valid_values": [".xlsx"],
                        },
                    )
                total_size += uf.size
            max_bytes = getattr(settings, "BATCH_UPLOAD_MAX_TOTAL_BYTES", 200 * 1024 * 1024)
            if total_size > max_bytes:
                mb_limit = max_bytes / (1024 * 1024)
                mb_actual = total_size / (1024 * 1024)
                return v1_or_v2_error(
                    request,
                    v1_body={"detail": f"Total upload size ({mb_actual:.1f} MB) exceeds limit ({mb_limit:.0f} MB)."},
                    v1_status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    v2={
                        "title": "Upload too large",
                        "detail": f"Total upload size ({mb_actual:.1f} MB) exceeds limit ({mb_limit:.0f} MB).",
                        "pointer": "/data/attributes/file",
                    },
                )
            for uf in uploaded_files:
                xlsx_paths.append(_save_uploaded_file(uf))

        # ── Neither provided ──
        else:
            return v1_or_v2_error(
                request,
                v1_body={"detail": "Either upload .xlsx file(s) or provide rows in JSON body."},
                v1_status=status.HTTP_400_BAD_REQUEST,
                v2={
                    "title": "Either upload .xlsx file(s) or provide rows in JSON body.",
                    "example": {"rows": [{"SampleType": "NHP"}], "project_id": 12},
                },
            )

        # Validate project_id. For mode 1, the in-Pydantic-handler path above already
        # short-circuits when project_id is the sole error; for mode 2 (file upload)
        # we still need an explicit check here.
        if raw_pid is None:
            return v1_or_v2_error(
                request,
                v1_body={"detail": "project_id is required"},
                v1_status=status.HTTP_400_BAD_REQUEST,
                v2={
                    "title": "project_id is required",
                    "pointer": "/data/attributes/project_id",
                    "example": 12,
                },
            )
        try:
            project_id = int(raw_pid)
        except (TypeError, ValueError):
            return v1_or_v2_error(
                request,
                v1_body={"detail": "project_id must be an integer"},
                v1_status=status.HTTP_400_BAD_REQUEST,
                v2={
                    "title": "project_id must be an integer",
                    "pointer": "/data/attributes/project_id",
                    "example": 12,
                    "valid_values": ["12", "47", "(any positive integer SEEK project id)"],
                },
            )

        # config_overrides + update_existing handling
        config_overrides = request.data.get("config_overrides", {})
        if isinstance(config_overrides, str):
            s = config_overrides.strip()
            if not s:
                config_overrides = {}
            else:
                try:
                    config_overrides = json.loads(s)
                except Exception:
                    return v1_or_v2_error(
                        request,
                        v1_body={"detail": "config_overrides must be a JSON object"},
                        v1_status=status.HTTP_400_BAD_REQUEST,
                        v2={
                            "title": "config_overrides must be a JSON object",
                            "pointer": "/data/attributes/config_overrides",
                        },
                    )

        update_existing = request.data.get("update_existing")
        if update_existing is not None:
            if isinstance(update_existing, str):
                config_overrides["update_existing"] = update_existing.strip().lower() in ("1", "true", "yes")
            elif isinstance(update_existing, bool):
                config_overrides["update_existing"] = update_existing

        neo4j_only = request.data.get("neo4j_only")
        if neo4j_only is not None:
            if isinstance(neo4j_only, str):
                neo4j_only = neo4j_only.strip().lower() in ("1", "true", "yes")
            elif not isinstance(neo4j_only, bool):
                neo4j_only = False
        else:
            neo4j_only = False

        # Sanitize person_id: empty string → None (defense in depth;
        # _resolve_user_context also sanitizes, but guard here in case
        # future code between this point and that call reads person_id).
        _raw_pid = request.data.get("person_id")
        if isinstance(_raw_pid, str) and not _raw_pid.strip():
            try:
                request.data["person_id"] = None  # works for JSON (dict)
            except AttributeError:
                pass  # QueryDict (multipart) is immutable; _resolve_user_context handles it

        # Resolve contributor_id and lababbv from the authenticated user
        user_ctx = _resolve_user_context(request)
        if user_ctx is None:
            return v1_or_v2_error(
                request,
                v1_body={"detail": "Could not resolve contributor ID from session"},
                v1_status=status.HTTP_401_UNAUTHORIZED,
                v2={
                    "title": "Authentication required",
                    "detail": "Could not resolve contributor ID from session",
                },
            )

        # Optional explicit lababbv override — admin only (non-admin silently ignored)
        explicit_lababbv = request.data.get("lababbv")
        is_admin = bool(
            getattr(request.user, "is_authenticated", False)
            and (request.user.is_staff or request.user.is_superuser)
        )
        if is_admin and isinstance(explicit_lababbv, str) and explicit_lababbv.strip():
            effective_lababbv = explicit_lababbv.strip().upper()
        else:
            effective_lababbv = user_ctx["lababbv"]

        # Dispatch to Celery
        task_kwargs = dict(
            project_id=project_id,
            contributor_id=user_ctx["contributor_id"],
            lababbv=effective_lababbv,
            user_id=request.user.pk,
            config_overrides=config_overrides,
            neo4j_only=neo4j_only,
        )
        if validated_rows is not None:
            task_kwargs["rows"] = [r.model_dump() for r in validated_rows]
        else:
            task_kwargs["xlsx_paths"] = xlsx_paths

        if input_warnings:
            task_kwargs.setdefault("config_overrides", {})["_input_warnings"] = input_warnings

        task = run_batch_upload_task.delay(**task_kwargs)
        register_job(user_id=request.user.pk, job_id=task.id, project_id=project_id)

        return Response(
            {"job_id": task.id, "status": "queued"},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=BatchUploadStatusResponse,
                examples=[
                    OpenApiExample(
                        name="Minimal v2 status response (queued)",
                        value={"job_id": "abc-123", "state": "PENDING", "meta": {}, "result": None},
                    ),
                ],
            ),
            404: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="No job with that id for this user.",
                examples=[
                    OpenApiExample(
                        name="v2 not found error (404)",
                        value={"errors": [{
                            "status": "404",
                            "title": "Not found",
                            "detail": "No job with that id for this user.",
                        }]},
                    ),
                ],
            ),
        },
        description=BATCH_UPLOAD_STATUS_DESC,
    )
    @action(detail=False, methods=["get"], url_path=r"status/(?P<job_id>[^/.]+)")
    def job_status(self, request, job_id=None):
        """GET /api/batch-upload/status/{job_id}/ — poll job status."""
        denied = self._check_ownership(request, job_id)
        if denied:
            return denied
        result = AsyncResult(job_id, app=celery_app)
        response = {
            "job_id": job_id,
            "state": result.state,
            "meta": {},
            "result": None,
        }

        if result.state == "PROGRESS":
            response["meta"] = result.info or {}
        elif result.state == "SUCCESS":
            response["result"] = result.result
        elif result.state == "FAILURE":
            response["meta"] = {"error": str(result.result)}

        return Response(response)

    @extend_schema(
        responses={
            204: None,
            404: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="No job with that id for this user.",
                examples=[
                    OpenApiExample(
                        name="v2 not found error (404)",
                        value={"errors": [{
                            "status": "404",
                            "title": "Not found",
                            "detail": "No job with that id for this user.",
                        }]},
                    ),
                ],
            ),
        },
        description=BATCH_UPLOAD_CANCEL_DESC,
    )
    @action(detail=False, methods=["delete"], url_path=r"cancel/(?P<job_id>[^/.]+)")
    def cancel(self, request, job_id=None):
        """DELETE /api/batch-upload/cancel/{job_id}/ — revoke a job."""
        denied = self._check_ownership(request, job_id)
        if denied:
            return denied
        celery_app.control.revoke(job_id, terminate=True)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.STR,
                description="Summary CSV file download",
                examples=[
                    OpenApiExample(
                        name="Minimal v2 summary header (CSV)",
                        value="job_id,state,rows_inserted\nabc-123,SUCCESS,50",
                    ),
                ],
            ),
            404: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Job not complete or summary not found.",
                examples=[
                    OpenApiExample(
                        name="v2 not found error (404)",
                        value={"errors": [{
                            "status": "404",
                            "title": "Job not complete",
                            "detail": "Job not complete (state=PROGRESS)",
                        }]},
                        media_type="application/vnd.nextseek.v2+json",
                    ),
                ],
            ),
        },
        description=BATCH_UPLOAD_SUMMARY_DESC,
    )
    @action(detail=False, methods=["get"], url_path=r"summary/(?P<job_id>[^/.]+)")
    def summary(self, request, job_id=None):
        """GET /api/batch-upload/summary/{job_id}/ — download summary CSV."""
        denied = self._check_ownership(request, job_id)
        if denied:
            return denied
        result = AsyncResult(job_id, app=celery_app)

        if result.state not in {"SUCCESS", "FAILURE"}:
            return v1_or_v2_error(
                request,
                v1_body={"detail": f"Job not complete (state={result.state})"},
                v1_status=status.HTTP_404_NOT_FOUND,
                v2={
                    "title": "Job not complete",
                    "detail": f"Job not complete (state={result.state})",
                },
            )

        summary_path = (result.result or {}).get("summary_path", "")
        if not summary_path or not os.path.isfile(summary_path):
            return v1_or_v2_error(
                request,
                v1_body={"detail": "Summary file not found"},
                v1_status=status.HTTP_404_NOT_FOUND,
                v2={
                    "title": "Summary file not found",
                },
            )

        return FileResponse(
            open(summary_path, "rb"),
            content_type="text/csv",
            as_attachment=True,
            filename=f"batch_upload_summary_{job_id}.csv",
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response={"type": "array", "items": {"type": "object"}},
                examples=[
                    OpenApiExample(
                        name="Minimal v2 list response",
                        value={"jobs": [], "total": 0, "page": 1, "page_size": 20},
                    ),
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Invalid pagination parameters.",
                examples=[
                    OpenApiExample(
                        name="v2 validation error (400)",
                        value={"errors": [{
                            "status": "400",
                            "title": "page and page_size must be integers",
                            "source": {"parameter": "page"},
                        }]},
                    ),
                ],
            ),
        },
        description=BATCH_UPLOAD_LIST_DESC,
    )
    def list(self, request):
        """GET /api/batch-upload/ — list user's jobs with pagination."""
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 20))
        except (TypeError, ValueError):
            return v1_or_v2_error(
                request,
                v1_body={"detail": "page and page_size must be integers"},
                v1_status=status.HTTP_400_BAD_REQUEST,
                v2={
                    "title": "page and page_size must be integers",
                    "parameter": "page",
                },
            )
        page_size = min(page_size, 100)  # cap

        result = list_jobs(
            user_id=request.user.pk,
            page=page,
            page_size=page_size,
        )

        # Enrich with current Celery state
        enriched_jobs = []
        for job_entry in result["jobs"]:
            job_id = job_entry["job_id"]
            celery_result = AsyncResult(job_id, app=celery_app)
            enriched_jobs.append({
                "job_id": job_id,
                "project_id": job_entry.get("project_id"),
                "created_at": job_entry.get("created_at"),
                "state": celery_result.state,
            })

        return Response({
            "jobs": enriched_jobs,
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        })


# ── helpers ───────────────────────────────────────────────────────────────

_UPLOAD_DIR = "batch_upload_uploads"


def _save_uploaded_file(uploaded_file) -> str:
    """Save an uploaded Django file to MEDIA_ROOT/batch_upload_uploads/ and return the path."""
    upload_dir = os.path.join(getattr(settings, "MEDIA_ROOT", "/tmp"), _UPLOAD_DIR)
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = os.path.basename(getattr(uploaded_file, "name", "upload.xlsx") or "upload.xlsx")
    filename = f"{int(time.time())}_{safe_name}"
    dest_path = os.path.join(upload_dir, filename)
    with open(dest_path, "wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)
    return dest_path


def _resolve_user_context(request) -> dict | None:
    """Resolve contributor_id and lababbv from the authenticated user.

    Three-phase resolution:
      Phase 1 — Resolve authenticated user's SEEK identity (person_id + lababbv)
                via resolve_seek_auth, getSeekLogin, or Users ORM lookup.
      Phase 2 — Determine effective person_id (admin override vs own identity).
      Phase 3 — Resolve lababbv for the effective person.

    Returns {"contributor_id": int, "lababbv": str} or None.
    May include "person_id_ignored": True when a non-admin's override was rejected.
    """
    # ── PHASE 1: Resolve authenticated user's SEEK identity ──────────────
    authenticated_person_id = None
    authenticated_lababbv = "NA"

    # 1a. resolve_seek_auth → Users ORM (person_id only; lababbv via Phase 1b)
    _auth_creds = None  # Store creds from resolve_seek_auth for fallback
    try:
        from nextseek_api.helpers import resolve_seek_auth
        from seek.models import Users

        creds, _headers = resolve_seek_auth(request)
        if creds is not None:
            _auth_creds = creds
            username, _password = creds
            seek_user = Users.objects.using(
                getattr(Users, "_DATABASE", "default")
            ).get(login=username)
            authenticated_person_id = int(seek_user.person_id)
            log.debug("Phase 1a resolved person_id=%s via resolve_seek_auth", authenticated_person_id)
    except Exception:
        log.debug("Phase 1a (resolve_seek_auth) failed", exc_info=True)

    # 1b. SeekDB.getSeekLogin (session auth) — also initializes __seekapi for Phase 3
    _seekdb = None  # Will hold an initialized SeekDB if getSeekLogin succeeds
    if authenticated_lababbv == "NA":
        try:
            from seek.seekdb import SeekDB

            seekdb = SeekDB(None, None, None)
            login_info = seekdb.getSeekLogin(request, True)
            if login_info and login_info.get("status"):
                _seekdb = seekdb  # Store for Phase 3 getUserInfo calls
                pid = login_info.get("person_id") or login_info.get("id")
                if pid and authenticated_person_id is None:
                    authenticated_person_id = int(pid)
                authenticated_lababbv = login_info.get("lababbv", "NA") or "NA"
                log.debug("Phase 1b resolved lababbv=%s via getSeekLogin", authenticated_lababbv)
        except Exception:
            log.debug("Phase 1b (getSeekLogin) failed", exc_info=True)

    # 1b-fallback: Phase 1a got creds but 1b failed (e.g., Basic Auth, no session).
    # Initialize SeekDB with the credentials directly so getUserInfo works.
    if authenticated_lababbv == "NA" and _auth_creds is not None and authenticated_person_id is not None:
        try:
            from seek.seekdb import SeekDB
            from django.conf import settings

            username, password = _auth_creds
            seekdb = SeekDB(settings.SEEK_URL, username, password)
            _seekdb = seekdb
            user_info, ui_status, _msg = seekdb.getUserInfo(authenticated_person_id)
            if ui_status and user_info.get("lababbv"):
                authenticated_lababbv = user_info["lababbv"]
                log.debug(
                    "Phase 1b-fallback resolved lababbv=%s via creds from resolve_seek_auth",
                    authenticated_lababbv,
                )
        except Exception:
            log.debug("Phase 1b-fallback (creds-based SeekDB) failed", exc_info=True)

    # 1c. Users ORM lookup by Django username (person_id only; lababbv via Phase 1b)
    if authenticated_person_id is None:
        try:
            from seek.models import Users

            if hasattr(request, "user") and request.user.is_authenticated:
                seek_user = Users.objects.using(
                    getattr(Users, "_DATABASE", "default")
                ).get(login=request.user.username)
                authenticated_person_id = int(seek_user.person_id)
                log.debug("Phase 1c resolved person_id=%s via Users ORM", authenticated_person_id)
        except Exception:
            log.debug("Phase 1c (Users ORM) failed", exc_info=True)

    # 1d. All resolution paths failed — NO Django pk fallback
    if authenticated_person_id is None:
        log.debug("Phase 1 failed: could not resolve SEEK identity")

    # ── PHASE 2: Determine effective person_id ───────────────────────────
    # Sanitize person_id: empty string → None
    raw_person_id = request.data.get("person_id")
    if isinstance(raw_person_id, str) and not raw_person_id.strip():
        request_person_id = None
    else:
        request_person_id = raw_person_id

    is_admin = (
        hasattr(request, "user")
        and getattr(request.user, "is_authenticated", False)
        and bool(request.user.is_staff or request.user.is_superuser)
    )
    person_id_ignored = False

    if is_admin and request_person_id is not None:
        try:
            effective_person_id = int(request_person_id)
        except (TypeError, ValueError):
            log.warning("Invalid person_id value: %r", request_person_id)
            return None
    elif not is_admin and request_person_id is not None:
        # Non-admin tried to override — silently ignore, use own identity
        effective_person_id = authenticated_person_id
        person_id_ignored = True
        log.warning(
            "Non-admin user %s attempted person_id override=%s; using own identity %s",
            getattr(request.user, "username", "?"),
            request_person_id,
            authenticated_person_id,
        )
    else:
        effective_person_id = authenticated_person_id

    # If we have no effective person, we cannot proceed
    if effective_person_id is None:
        return None

    # ── PHASE 3: Resolve lababbv for effective person ────────────────────
    if effective_person_id == authenticated_person_id:
        lababbv = authenticated_lababbv
    else:
        # Admin override — resolve target's lababbv using initialized SeekDB
        lababbv = authenticated_lababbv  # default fallback
        if _seekdb is not None:
            try:
                target_info, t_status, _msg = _seekdb.getUserInfo(effective_person_id)
                if t_status and target_info.get("lababbv"):
                    lababbv = target_info["lababbv"]
                else:
                    log.debug(
                        "getUserInfo(%s) returned no lababbv; using admin's lababbv=%s",
                        effective_person_id,
                        authenticated_lababbv,
                    )
            except Exception:
                log.debug(
                    "getUserInfo(%s) failed; using admin's lababbv=%s",
                    effective_person_id,
                    authenticated_lababbv,
                    exc_info=True,
                )
        else:
            log.debug(
                "No initialized SeekDB for getUserInfo(%s); using lababbv=%s",
                effective_person_id,
                authenticated_lababbv,
            )

    result = {"contributor_id": effective_person_id, "lababbv": lababbv}
    if person_id_ignored:
        result["person_id_ignored"] = True
    return result
