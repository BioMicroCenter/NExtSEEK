"""DRF ViewSet for batch upload endpoints."""
from __future__ import annotations

import logging
import os

from celery.result import AsyncResult
from django.conf import settings
from django.http import FileResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from pydantic import BaseModel, Field
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .celery_app import app as celery_app
from .tasks import run_batch_upload_task

log = logging.getLogger(__name__)


# ── Request/Response models for OpenAPI docs ──────────────────────────────


class BatchUploadStartRequest(BaseModel):
    xlsx_path: str = Field(..., description="Absolute path to the Excel file on the server")
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

    @extend_schema(
        request={"application/json": BatchUploadStartRequest.model_json_schema()},
        responses={202: BatchUploadStartResponse.model_json_schema()},
        description="Start a batch upload job from an Excel file on the server.",
    )
    @action(detail=False, methods=["post"], url_path="start")
    def start(self, request):
        """POST /api/batch-upload/start/ — dispatch a batch upload job."""
        xlsx_path = request.data.get("xlsx_path")
        project_id = request.data.get("project_id")
        config_overrides = request.data.get("config_overrides", {})

        if not xlsx_path or project_id is None:
            return Response(
                {"detail": "xlsx_path and project_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not os.path.isfile(xlsx_path):
            return Response(
                {"detail": f"File not found: {xlsx_path}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "project_id must be an integer"},
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
