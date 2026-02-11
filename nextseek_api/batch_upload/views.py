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
        description=(
            "Start a batch upload job for bulk sample creation.\n\n"
            "**Preferred:** Upload an Excel (.xlsx) file via multipart/form-data using the `file` field.\n\n"
            "**Fallback:** Send a JSON body with `xlsx_path` pointing to a file already on the server.\n\n"
            "## Required Excel Columns\n\n"
            "- **uid** — Sample UID (unique identifier)\n"
            "- **sampletype** — Sample type name (alias: `sample_type`)\n"
            "- **json_metadata** — JSON metadata string (alias: `jsonmetadata`)\n"
            "- **assay_ids** — Comma-separated assay IDs (alias: `assayids`)\n\n"
            "## Optional Excel Columns\n\n"
            "- **project_id** — Per-row project ID override (alias: `projectid`)\n"
            "- **study_title** — Accepted but not used by the pipeline\n"
            "- **study_id** — Accepted but not used by the pipeline\n"
            "- **sop_id** — Optional SOP id override. If provided and `json_metadata.Protocol` contains an SOP id, they must match or the row is rejected.\n"
            "- **assay_titles** — Optional assay title(s). If provided, parsed into a list and must match `assay_ids` length (or both singleton) or the row is rejected.\n\n"
            "## Notes\n\n"
            "- Column headers are case-insensitive and whitespace is trimmed.\n"
            "- Unknown extra columns are ignored, but a warning listing them is included in logs, the job status payload, and the summary CSV.\n"
            "- Some validation conflicts (e.g., UID mismatch between `uid` and `json_metadata.UID`) reject only that row; the job continues."
        ),
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

        # Resolve contributor_id from the authenticated user
        contributor_id = _resolve_contributor_id(request)
        if contributor_id is None:
            return Response(
                {"detail": "Could not resolve contributor ID from session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        task = run_batch_upload_task.delay(
            xlsx_path=xlsx_path,
            project_id=project_id,
            contributor_id=contributor_id,
            config_overrides=config_overrides,
        )

        return Response(
            {"job_id": task.id, "status": "queued"},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        responses={200: BatchUploadStatusResponse.model_json_schema()},
        description="Get the status and progress of a batch upload job.",
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
        description="Cancel (revoke) a running batch upload job.",
    )
    @action(detail=False, methods=["delete"], url_path=r"cancel/(?P<job_id>[^/.]+)")
    def cancel(self, request, job_id=None):
        """DELETE /api/batch-upload/cancel/{job_id}/ — revoke a job."""
        celery_app.control.revoke(job_id, terminate=True)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        responses={200: OpenApiResponse(description="Summary CSV file download")},
        description="Download the summary CSV for a completed batch upload job.",
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
        description="List recent batch upload jobs.",
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


def _resolve_contributor_id(request) -> int | None:
    """Resolve the SEEK person/contributor ID from the authenticated user.

    Uses SeekDB.getSeekLogin to get the person_id from the session,
    falling back to request.user.pk for Django-authenticated users.
    """
    try:
        from seek.seekdb import SeekDB

        seekdb = SeekDB(None, None, None)
        user_info = seekdb.getSeekLogin(request, False)
        if user_info and user_info.get("status"):
            # SeekDB returns person_id in the user info dict
            person_id = user_info.get("person_id") or user_info.get("id")
            if person_id:
                return int(person_id)
    except Exception:
        log.debug("Could not resolve contributor_id from SeekDB", exc_info=True)

    # Fallback: use Django user pk
    if hasattr(request, "user") and request.user.is_authenticated:
        return request.user.pk

    return None
