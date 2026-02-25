"""DRF ViewSet for batch upload endpoints."""
from __future__ import annotations

import logging
import os
import json
import time

from celery.result import AsyncResult
from django.conf import settings
from django.http import FileResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from pydantic import BaseModel, Field
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .celery_app import app as celery_app
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
    xlsx_path: str | None = Field(
        None,
        description=(
            "Absolute path to an Excel file already on the server. "
            "Only used as a fallback when no file is uploaded via multipart/form-data."
        ),
    )
    project_id: int = Field(..., description="SEEK project ID to link samples to")
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

    Requires admin privileges. Dispatches work to a Celery worker
    and provides status tracking, cancellation, and result download.
    """

    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "Excel (.xlsx) file containing samples to upload.",
                    },
                    "project_id": {
                        "type": "integer",
                        "description": "SEEK project ID to link samples to.",
                    },
                    "xlsx_path": {
                        "type": "string",
                        "description": (
                            "Fallback: absolute path to an Excel file already on the server. "
                            "Ignored when a file is uploaded."
                        ),
                    },
                    "config_overrides": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Optional config overrides (JSON object).",
                    },
                },
                "required": ["project_id"],
            },
            "application/json": BatchUploadStartRequest.model_json_schema(),
        },
        responses={202: BatchUploadStartResponse.model_json_schema()},
        description=BATCH_UPLOAD_START_DESC,
    )
    @action(detail=False, methods=["post"], url_path="start")
    def start(self, request):
        """POST /api/batch-upload/start/ — dispatch a batch upload job."""
        uploaded_file = request.FILES.get("file")
        xlsx_path = None

        if uploaded_file:
            if not (uploaded_file.name or "").lower().endswith(".xlsx"):
                return Response(
                    {"detail": "Only .xlsx files are accepted."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            xlsx_path = _save_uploaded_file(uploaded_file)
        else:
            xlsx_path = request.data.get("xlsx_path")

        if not xlsx_path:
            return Response(
                {"detail": "Either upload a .xlsx file or provide xlsx_path."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not os.path.isfile(xlsx_path):
            return Response(
                {"detail": f"File not found: {xlsx_path}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        # Resolve contributor_id and lababbv from the authenticated user
        user_ctx = _resolve_user_context(request)
        if user_ctx is None:
            return Response(
                {"detail": "Could not resolve contributor ID from session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        task = run_batch_upload_task.delay(
            xlsx_path=xlsx_path,
            project_id=project_id,
            contributor_id=user_ctx["contributor_id"],
            lababbv=user_ctx["lababbv"],
            config_overrides=config_overrides,
        )

        return Response(
            {"job_id": task.id, "status": "queued"},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        responses={200: BatchUploadStatusResponse.model_json_schema()},
        description=BATCH_UPLOAD_STATUS_DESC,
    )
    @action(detail=False, methods=["get"], url_path=r"status/(?P<job_id>[^/.]+)")
    def job_status(self, request, job_id=None):
        """GET /api/batch-upload/status/{job_id}/ — poll job status."""
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
        celery_app.control.revoke(job_id, terminate=True)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        responses={200: OpenApiResponse(description="Summary CSV file download")},
        description=BATCH_UPLOAD_SUMMARY_DESC,
    )
    @action(detail=False, methods=["get"], url_path=r"summary/(?P<job_id>[^/.]+)")
    def summary(self, request, job_id=None):
        """GET /api/batch-upload/summary/{job_id}/ — download summary CSV."""
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
        """GET /api/batch-upload/ — list recent jobs."""
        # Query the result backend for recent tasks
        # Note: full task listing requires Flower or custom tracking;
        # this provides a basic check for known job IDs
        return Response(
            {"detail": "Use /api/batch-upload/status/{job_id}/ to check specific jobs."},
            status=status.HTTP_200_OK,
        )


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

    Uses SeekDB.getSeekLogin to get person_id and lab abbreviation,
    falling back to request.user.pk for Django-authenticated users.

    Returns {"contributor_id": int, "lababbv": str} or None.
    """
    contributor_id = None
    lababbv = "NA"

    try:
        from seek.seekdb import SeekDB

        seekdb = SeekDB(None, None, None)
        # Pass True to get full profile including lababbv
        user_info = seekdb.getSeekLogin(request, True)
        if user_info and user_info.get("status"):
            person_id = user_info.get("person_id") or user_info.get("id")
            if person_id:
                contributor_id = int(person_id)
            lababbv = user_info.get("lababbv", "NA") or "NA"
    except Exception:
        log.debug("Could not resolve user context from SeekDB", exc_info=True)

    # Fallback: use Django user pk
    if contributor_id is None:
        if hasattr(request, "user") and request.user.is_authenticated:
            contributor_id = request.user.pk

    if contributor_id is None:
        return None

    return {"contributor_id": contributor_id, "lababbv": lababbv}
