"""Thin DRF surface for batch assay-membership registration."""
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from pydantic import ValidationError
from rest_framework import exceptions, viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.endpoint_descriptions import (
    ASSAY_REGISTRATION_CREATE_DESC,
    ASSAY_REGISTRATION_JOB_CANCEL_DESC,
    ASSAY_REGISTRATION_JOB_DESC,
)
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

from . import service
from .schemas import (
    ErrorResponse,
    JobStatusResponse,
    RegistrationAcceptedResponse,
    RegistrationRequest,
    RegistrationResponse,
    RowError,
)

#: LOOKUP failures only, and the narrowness is the point. Named once because
#: both job actions need the same tuple for the same reason, so a future fix
#: cannot correct one and miss the other.
#:
#: `ObjectDoesNotExist` is the missing row. `DjangoValidationError` is the
#: malformed id: a `job_id` that will not parse reaches the ORM and Django's
#: UUIDField.to_python raises THAT -- not ValueError and not ObjectDoesNotExist.
#: The action's url_path is `[^/.]+`, so any non-empty token routes here, and
#: omitting it turns `GET /assay-registrations/jobs/garbage/` into a 500.
#:
#: A bare `ValueError` MUST NOT be added back. pydantic's ValidationError
#: subclasses ValueError, and `service.job_status` ends by validating the stored
#: terminal report, so `ValueError` here answers any drift between a persisted
#: result and the response model with 404 "Job not found" -- for a job that
#: exists and has finished. On an endpoint whose whole purpose is a truthful
#: receipt, telling a caller their completed batch was never created is the worst
#: available answer. Unparseable stored state is a server error: let it 500,
#: loudly. This is live the moment a worker starts writing terminal results.
#:
#: Note also that `DjangoValidationError` is Django's, not pydantic's, which is
#: imported unqualified above for request-body validation. Same class name,
#: unrelated classes, opposite meanings -- so the Django one is aliased.
_JOB_LOOKUP_FAILURES = (ObjectDoesNotExist, DjangoValidationError)

REQUEST_EXAMPLE = {
    "registrations": [
        {"sample_uid": "D.NHP-240115MIT-001", "assay": "Flow Cytometry"},
        {"sample_uid": "D.NHP-240115MIT-002", "assay_id": 351},
    ],
    "dry_run": False,
}

RESPONSE_EXAMPLE = {
    "mode": "synchronous",
    "overall_status": "partial",
    "counts": {"submitted": 3, "written": 1, "already_present": 1,
               "skipped": 1, "failed": 0},
    "rows": [
        {"index": 0, "sample_uid": "D.NHP-240115MIT-001", "status": "written",
         "sample_id": 48213, "assay_id": 351, "assay_title": "Flow Cytometry",
         "project_id": 3, "assay_assets_id": 414936},
        {"index": 1, "sample_uid": "D.NHP-240115MIT-002", "status": "already_present",
         "sample_id": 48214, "assay_id": 351, "assay_assets_id": 219104},
        {"index": 2, "sample_uid": "D.IMG-260311ENG-490", "status": "skipped",
         "error": {"code": "sample_uid_not_unique",
                   "message": "resolves to 2 rows in `samples`; expected exactly 1",
                   "submitted_identifier": "D.IMG-260311ENG-490"}},
    ],
    "graph": {"status": "succeeded", "edges_recomputed": 128},
}

#: `processed_rows` is 0, and it is the ONLY honest value for a running job.
#: `runner.run_one` calls `record_progress` once, with the full total, in the
#: line before `finish` -- so this field is 0 or `total_rows` and never anything
#: between. The example published 4000 of 25765, which is the one shape in this
#: schema implying granular progress the worker cannot produce, on an endpoint
#: whose entire subject is not overstating what happened.
JOB_EXAMPLE = {
    "job_id": "123e4567-e89b-12d3-a456-426614174000",
    "state": "running",
    "processed_rows": 0,
    "total_rows": 25765,
    "result": None,
}


def _envelope(code: str, message: str, identifier=None) -> dict:
    return ErrorResponse(errors=[RowError(
        code=code, message=message,
        submitted_identifier=None if identifier is None else str(identifier),
    )]).model_dump(mode="json")


def _validation_error(exc: ValidationError) -> dict:
    return ErrorResponse(errors=[
        RowError(
            code="request_validation_error",
            message=item["msg"],
            submitted_identifier=".".join(str(part) for part in item["loc"]) or None,
        )
        for item in exc.errors(include_url=False)
    ]).model_dump(mode="json")


def _not_found(job_id) -> dict:
    return _envelope("job_not_found", "Job not found", job_id)


class AssayRegistrationViewSet(viewsets.ViewSet):
    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsSuperUser]

    def get_authenticate_header(self, request):
        """Advertise a challenge DRF's default lookup would never find.

        APIView.get_authenticate_header asks ONLY authenticators[0], and
        SessionAuthentication.authenticate_header returns None. DRF then
        coerces NotAuthenticated to 403 (rest_framework/views.py,
        handle_exception: "WWW-Authenticate header for 401 responses, else
        coerce to 403"), so an anonymous caller would get 403 -- the same code
        as an authenticated non-superuser, collapsing "you sent no
        credentials" into "your credentials are insufficient", and making the
        401 this ViewSet documents unreachable.

        Every other superuser ViewSet here (services/project_export.py:264,
        services/sampletype_connections.py:804, both of which say so in a
        comment) avoids that by listing TokenAuthentication first purely for
        its challenge header. Token auth does not work in this project, so this
        endpoint asks every authenticator instead and finds BasicAuthentication's
        challenge -- a credential it genuinely accepts. Session auth stays first
        in the list, which is what decides who actually authenticates.
        """
        for authenticator in self.get_authenticators():
            header = authenticator.authenticate_header(request)
            if header:
                return header
        return None

    def handle_exception(self, exc):
        """Answer the auth gates in this endpoint's own error envelope.

        DRF's default body is ``{"detail": ...}``, which is not the
        ``ErrorResponse`` the 401 and 403 responses below are declared as, so
        without this the published schema would be wrong about two of its own
        documented responses. Mirrors AttributeViewSet.handle_exception
        (nextseek_api/attributes/views.py:132), the sibling job endpoint.
        """
        if isinstance(exc, (exceptions.NotAuthenticated,
                            exceptions.AuthenticationFailed)):
            header = self.get_authenticate_header(self.request)
            return Response(
                _envelope("authentication_failed", str(exc)), status=401,
                headers={"WWW-Authenticate": header} if header else None,
            )
        if isinstance(exc, exceptions.PermissionDenied):
            return Response(_envelope("permission_denied", str(exc)), status=403)
        return super().handle_exception(exc)

    @extend_schema(
        operation_id="Register Assay Memberships",
        description=ASSAY_REGISTRATION_CREATE_DESC,
        request=RegistrationRequest,
        responses={
            200: RegistrationResponse,
            202: RegistrationAcceptedResponse,  # the job path returns a job id, not rows
            207: RegistrationResponse,
            # 409 is the caller's case: no row was executable. 500 is ours:
            # rows WERE executable, were inserted, and were absent on read-back.
            # Both carry the same full per-row body; see service._http_status.
            409: RegistrationResponse, 500: RegistrationResponse,
            422: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse,
        },
        tags=["AssayRegistrations"],
        examples=[
            OpenApiExample("Register two samples", value=REQUEST_EXAMPLE,
                           request_only=True),
            OpenApiExample("Mixed outcome", value=RESPONSE_EXAMPLE,
                           response_only=True, status_codes=["207"]),
        ],
    )
    def create(self, request):
        try:
            payload = RegistrationRequest.model_validate(request.data)
        except ValidationError as exc:
            return Response(_validation_error(exc), status=422)
        body, code = service.register(payload, request)
        return Response(body, status=code)

    @extend_schema(
        operation_id="Get Assay Registration Job",
        description=ASSAY_REGISTRATION_JOB_DESC,
        parameters=[OpenApiParameter(
            "job_id", OpenApiTypes.UUID, OpenApiParameter.PATH,
            description="Job UUID returned by the registration call.")],
        responses={200: JobStatusResponse, 404: ErrorResponse,
                   401: ErrorResponse, 403: ErrorResponse},
        tags=["AssayRegistrations"],
        examples=[OpenApiExample("Running registration", value=JOB_EXAMPLE,
                                 response_only=True, status_codes=["200"])],
    )
    @action(detail=False, methods=["get"], url_path=r"jobs/(?P<job_id>[^/.]+)")
    def job(self, request, job_id=None):
        try:
            return Response(service.job_status(job_id), status=200)
        except _JOB_LOOKUP_FAILURES:
            return Response(_not_found(job_id), status=404)

    @extend_schema(
        operation_id="Cancel Assay Registration Job",
        description=ASSAY_REGISTRATION_JOB_CANCEL_DESC,
        parameters=[OpenApiParameter(
            "job_id", OpenApiTypes.UUID, OpenApiParameter.PATH,
            description="Job UUID to cancel.")],
        responses={202: JobStatusResponse, 404: ErrorResponse,
                   409: ErrorResponse, 401: ErrorResponse, 403: ErrorResponse},
        tags=["AssayRegistrations"],
        examples=[OpenApiExample("Cancellation accepted", value=JOB_EXAMPLE,
                                 response_only=True, status_codes=["202"])],
    )
    @action(detail=False, methods=["post"], url_path=r"jobs/(?P<job_id>[^/.]+)/cancel")
    def job_cancel(self, request, job_id=None):
        try:
            body, code = service.cancel(job_id, request.user)
        except _JOB_LOOKUP_FAILURES:
            return Response(_not_found(job_id), status=404)
        return Response(body, status=code)
