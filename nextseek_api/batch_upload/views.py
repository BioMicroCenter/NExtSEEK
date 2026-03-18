"""DRF ViewSet for batch upload endpoints."""
from __future__ import annotations

import logging
import os
import json
import time

from celery.result import AsyncResult
from django.conf import settings
from django.http import FileResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from pydantic import BaseModel, Field
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, TokenAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

from .celery_app import app as celery_app
from .job_index import list_jobs, register_job, user_owns_job
from .models import InputRowModel
from .tasks import run_batch_upload_task
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
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
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
            202: BatchUploadStartResponse,
            400: OpenApiResponse(description="Missing input or structural error"),
            401: OpenApiResponse(description="Authentication required"),
            413: OpenApiResponse(description="Total upload size exceeds limit"),
            422: OpenApiResponse(description="Pydantic validation error on rows or request body"),
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

        # ── Mode 1: Direct rows (JSON, checked first -- rows wins) ──
        if raw_rows is not None:
            # Validate entire JSON body via request model
            from pydantic import ValidationError as PydanticValidationError

            try:
                req = BatchUploadStartRequest.model_validate(request.data)
            except PydanticValidationError as e:
                return Response(
                    {"detail": f"Request validation failed: {e.error_count()} error(s)", "errors": e.errors()},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            validated_rows = req.rows
            if not validated_rows:
                return Response(
                    {"detail": "rows must be a non-empty list of InputRowModel objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if uploaded_files:
                input_warnings.append("Both 'rows' and file uploads provided; using rows, ignoring files.")

        # ── Mode 2: File upload (multipart) ──
        elif uploaded_files:
            total_size = 0
            for uf in uploaded_files:
                if not (uf.name or "").lower().endswith(".xlsx"):
                    return Response(
                        {"detail": "Only .xlsx files are accepted."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                total_size += uf.size
            max_bytes = getattr(settings, "BATCH_UPLOAD_MAX_TOTAL_BYTES", 200 * 1024 * 1024)
            if total_size > max_bytes:
                mb_limit = max_bytes / (1024 * 1024)
                mb_actual = total_size / (1024 * 1024)
                return Response(
                    {"detail": f"Total upload size ({mb_actual:.1f} MB) exceeds limit ({mb_limit:.0f} MB)."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
            for uf in uploaded_files:
                xlsx_paths.append(_save_uploaded_file(uf))

        # ── Neither provided ──
        else:
            return Response(
                {"detail": "Either upload .xlsx file(s) or provide rows in JSON body."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate project_id
        project_id = request.data.get("project_id")
        if project_id is None:
            return Response(
                {"detail": "project_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "project_id must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
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
                    return Response(
                        {"detail": "config_overrides must be a JSON object"},
                        status=status.HTTP_400_BAD_REQUEST,
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

        # Resolve contributor_id and lababbv from the authenticated user
        user_ctx = _resolve_user_context(request)
        if user_ctx is None:
            return Response(
                {"detail": "Could not resolve contributor ID from session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Dispatch to Celery
        task_kwargs = dict(
            project_id=project_id,
            contributor_id=user_ctx["contributor_id"],
            lababbv=user_ctx["lababbv"],
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
        responses={200: BatchUploadStatusResponse},
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
        responses={204: None},
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
        responses={200: OpenApiResponse(description="Summary CSV file download")},
        description=BATCH_UPLOAD_SUMMARY_DESC,
    )
    @action(detail=False, methods=["get"], url_path=r"summary/(?P<job_id>[^/.]+)")
    def summary(self, request, job_id=None):
        """GET /api/batch-upload/summary/{job_id}/ — download summary CSV."""
        denied = self._check_ownership(request, job_id)
        if denied:
            return denied
        result = AsyncResult(job_id, app=celery_app)

        if result.state != "SUCCESS":
            return Response(
                {"detail": f"Job not complete (state={result.state})"},
                status=status.HTTP_404_NOT_FOUND,
            )

        summary_path = (result.result or {}).get("summary_path", "")
        if not summary_path or not os.path.isfile(summary_path):
            return Response(
                {"detail": "Summary file not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return FileResponse(
            open(summary_path, "rb"),
            content_type="text/csv",
            as_attachment=True,
            filename=f"batch_upload_summary_{job_id}.csv",
        )

    @extend_schema(
        responses={200: {"type": "array", "items": {"type": "object"}}},
        description=BATCH_UPLOAD_LIST_DESC,
    )
    def list(self, request):
        """GET /api/batch-upload/ — list user's jobs with pagination."""
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 20))
        except (TypeError, ValueError):
            return Response(
                {"detail": "page and page_size must be integers"},
                status=status.HTTP_400_BAD_REQUEST,
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

    # 1a. resolve_seek_auth → Users ORM → getUserInfo
    try:
        from nextseek_api.helpers import resolve_seek_auth
        from seek.models import Users
        from seek.seekdb import SeekDB

        creds, _headers = resolve_seek_auth(request)
        if creds is not None:
            username, _password = creds
            seek_user = Users.objects.using(
                getattr(Users, "_DATABASE", "default")
            ).get(login=username)
            authenticated_person_id = int(seek_user.person_id)
            seekdb = SeekDB(None, None, None)
            user_info, ui_status, _msg = seekdb.getUserInfo(authenticated_person_id)
            if ui_status and user_info.get("lababbv"):
                authenticated_lababbv = user_info["lababbv"]
            log.debug("Phase 1a resolved person_id=%s via resolve_seek_auth", authenticated_person_id)
    except Exception:
        log.debug("Phase 1a (resolve_seek_auth) failed", exc_info=True)

    # 1b. SeekDB.getSeekLogin (session auth)
    if authenticated_person_id is None:
        try:
            from seek.seekdb import SeekDB

            seekdb = SeekDB(None, None, None)
            login_info = seekdb.getSeekLogin(request, True)
            if login_info and login_info.get("status"):
                pid = login_info.get("person_id") or login_info.get("id")
                if pid:
                    authenticated_person_id = int(pid)
                    authenticated_lababbv = login_info.get("lababbv", "NA") or "NA"
                    log.debug("Phase 1b resolved person_id=%s via getSeekLogin", authenticated_person_id)
        except Exception:
            log.debug("Phase 1b (getSeekLogin) failed", exc_info=True)

    # 1c. Users ORM lookup by Django username → getUserInfo
    if authenticated_person_id is None:
        try:
            from seek.models import Users
            from seek.seekdb import SeekDB

            if hasattr(request, "user") and request.user.is_authenticated:
                seek_user = Users.objects.using(
                    getattr(Users, "_DATABASE", "default")
                ).get(login=request.user.username)
                authenticated_person_id = int(seek_user.person_id)
                seekdb = SeekDB(None, None, None)
                user_info, ui_status, _msg = seekdb.getUserInfo(authenticated_person_id)
                if ui_status and user_info.get("lababbv"):
                    authenticated_lababbv = user_info["lababbv"]
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
        effective_person_id = int(request_person_id)
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
        # Admin override — resolve target's lababbv
        try:
            from seek.seekdb import SeekDB

            seekdb = SeekDB(None, None, None)
            target_info, t_status, _msg = seekdb.getUserInfo(effective_person_id)
            if t_status and target_info.get("lababbv"):
                lababbv = target_info["lababbv"]
            else:
                # Target's lababbv unavailable — fall back to admin's own
                lababbv = authenticated_lababbv
                log.debug(
                    "getUserInfo(%s) returned no lababbv; using admin's lababbv=%s",
                    effective_person_id,
                    authenticated_lababbv,
                )
        except Exception:
            lababbv = authenticated_lababbv
            log.debug(
                "getUserInfo(%s) failed; using admin's lababbv=%s",
                effective_person_id,
                authenticated_lababbv,
                exc_info=True,
            )

    result = {"contributor_id": effective_person_id, "lababbv": lababbv}
    if person_id_ignored:
        result["person_id_ignored"] = True
    return result
