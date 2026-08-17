"""Thin DRF surface for the native attribute API."""
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from pydantic import ValidationError
from rest_framework import exceptions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from nextseek_api.endpoint_descriptions import (
    ATTRIBUTE_BATCH_CREATE_DESC,
    ATTRIBUTE_BATCH_DELETE_DESC,
    ATTRIBUTE_BATCH_PATCH_DESC,
    ATTRIBUTE_FETCH_DESC,
    ATTRIBUTE_JOB_CANCEL_DESC,
    ATTRIBUTE_JOB_DESC,
    ATTRIBUTE_LIST_DESC,
    ATTRIBUTE_SEARCH_DESC,
)

from . import openapi as _openapi  # noqa: F401 - registers authentication schema extension
from . import schemas
from .auth import CanCancelAttributeJob, IsSeekAdmin, SeekAuthenticated, SeekPersonAuthentication
from .scalars import ScalarInputError, parse_positive_int
from .schemas import CREATE_REQUEST_ADAPTER, DELETE_REQUEST_ADAPTER, PATCH_REQUEST_ADAPTER, SEARCH_REQUEST_ADAPTER


def attribute_services():
    from .service import AttributeServices
    return AttributeServices.build()


def request_validation_error(exc):
    errors = [schemas.MutationError(
        code="request_validation_error", message=item["msg"],
        field=".".join(str(part) for part in item["loc"]) or None,
    ) for item in exc.errors(include_url=False)]
    return schemas.AttributeErrorResponse(errors=errors).model_dump(mode="json")


def public_error(code, message, *, field=None, submitted_identifier=None):
    return schemas.AttributeErrorResponse(errors=[schemas.MutationError(
        code=code, message=message, field=field, submitted_identifier=submitted_identifier,
    )]).model_dump(mode="json")


ERROR_RESPONSES = {
    400: OpenApiResponse(response=schemas.AttributeErrorResponse),
    401: OpenApiResponse(response=schemas.AttributeErrorResponse),
    403: OpenApiResponse(response=schemas.AttributeErrorResponse),
    409: OpenApiResponse(response=schemas.AttributeErrorResponse),
    422: OpenApiResponse(response=schemas.AttributeErrorResponse),
}
MUTATION_UNION = PolymorphicProxySerializer(
    component_name="AttributeMutationResponse",
    serializers={
        "dry_run": schemas.MutationPreviewResponse,
        "synchronous": schemas.MutationCompletedResponse,
    },
    resource_type_field_name="mode",
    many=False,
)
COMPLETED_OR_ERROR = PolymorphicProxySerializer(
    component_name="AttributeMutationCompletedOrErrorResponse",
    serializers=[
        schemas.MutationCompletedResponse,
        schemas.AttributeErrorResponse,
    ],
    resource_type_field_name=None,
    many=False,
)
MUTATION_RESPONSES = {
    200: OpenApiResponse(response=MUTATION_UNION),
    202: schemas.MutationAcceptedResponse,
    207: OpenApiResponse(response=MUTATION_UNION),
    400: ERROR_RESPONSES[400], 401: ERROR_RESPONSES[401], 403: ERROR_RESPONSES[403],
    409: OpenApiResponse(response=COMPLETED_OR_ERROR),
    422: OpenApiResponse(response=COMPLETED_OR_ERROR),
}
PAGINATION_PARAMETERS = [
    OpenApiParameter(
        "page", OpenApiTypes.INT, OpenApiParameter.QUERY,
        description="One-based page number (default 1).", required=False,
    ),
    OpenApiParameter(
        "page_size", OpenApiTypes.INT, OpenApiParameter.QUERY,
        description="Records per page (default 500, maximum 5000).", required=False,
    ),
]
ATTRIBUTE_RECORD_EXAMPLE = _openapi.ATTRIBUTE_EXAMPLES[2]["value"]
ATTRIBUTE_LIST_EXAMPLE = {
    "attributes": [ATTRIBUTE_RECORD_EXAMPLE],
    "pagination": {"page": 1, "page_size": 500, "total_records": 1, "total_pages": 1},
}
ATTRIBUTE_JOB_EXAMPLE = {
    "job_id": "123e4567-e89b-12d3-a456-426614174000",
    "state": "running",
    "completed_sample_types": 1,
    "total_sample_types": 3,
    "processed_samples": 24,
    "total_samples": 80,
    "result": None,
}


class AttributeViewSet(viewsets.ViewSet):
    authentication_classes = [SeekPersonAuthentication]
    permission_classes = [SeekAuthenticated]

    def get_permissions(self):
        if self.action in {"batch_create", "batch_patch", "batch_delete", "job"}:
            return [SeekAuthenticated(), IsSeekAdmin()]
        if self.action == "job_cancel":
            return [SeekAuthenticated(), IsSeekAdmin(), CanCancelAttributeJob()]
        return [SeekAuthenticated()]

    def handle_exception(self, exc):
        if isinstance(exc, (exceptions.AuthenticationFailed, exceptions.NotAuthenticated)):
            return Response(
                public_error("authentication_failed", str(exc)), status=401,
                headers={"WWW-Authenticate": self.get_authenticate_header(self.request)},
            )
        if isinstance(exc, exceptions.PermissionDenied):
            return Response(public_error("permission_denied", str(exc)), status=403)
        return super().handle_exception(exc)

    @extend_schema(
        operation_id="List Attributes",
        description=ATTRIBUTE_LIST_DESC,
        parameters=PAGINATION_PARAMETERS,
        responses={200: schemas.AttributeListResponse, **ERROR_RESPONSES},
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Attribute catalog page", value=ATTRIBUTE_LIST_EXAMPLE,
            response_only=True, status_codes=["200"],
        )],
    )
    def list(self, request):
        try:
            return Response(attribute_services().list(request.query_params), status=200)
        except ScalarInputError as exc:
            return Response(exc.as_attribute_error_response(), status=400)

    @extend_schema(
        operation_id="Fetch an Attribute",
        description=ATTRIBUTE_FETCH_DESC,
        parameters=[OpenApiParameter(
            "id", OpenApiTypes.INT, OpenApiParameter.PATH,
            description="Positive database ID of the attribute definition.",
        )],
        responses={200: schemas.AttributeRecord, 404: schemas.AttributeErrorResponse, **ERROR_RESPONSES},
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Attribute definition", value=ATTRIBUTE_RECORD_EXAMPLE,
            response_only=True, status_codes=["200"],
        )],
    )
    def retrieve(self, request, pk=None):
        try:
            identifier = parse_positive_int(pk, field="id")
        except ScalarInputError as exc:
            return Response(exc.as_attribute_error_response(), status=400)
        value = attribute_services().retrieve(identifier)
        if value is None:
            return Response(public_error("attribute_not_found", "Attribute not found", field="id", submitted_identifier=identifier), status=404)
        return Response(value, status=200)

    @extend_schema(
        operation_id="Search Attributes",
        description=ATTRIBUTE_SEARCH_DESC,
        parameters=PAGINATION_PARAMETERS,
        request=schemas.SearchRequest,
        responses={200: schemas.AttributeListResponse, **ERROR_RESPONSES},
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Nested attribute search", value=_openapi.ATTRIBUTE_EXAMPLES[0]["value"],
            request_only=True,
        )],
    )
    @action(detail=False, methods=["post"], url_path="search")
    def search(self, request):
        try:
            payload = SEARCH_REQUEST_ADAPTER.validate_python(request.data)
            body = attribute_services().search(payload, request.query_params)
        except ValidationError as exc:
            return Response(request_validation_error(exc), status=422)
        except ScalarInputError as exc:
            return Response(exc.as_attribute_error_response(), status=400)
        if "errors" in body:
            return Response(body, status=409)
        return Response(body, status=200)

    def _mutate(self, request, adapter, operation):
        try:
            payload = adapter.validate_python(request.data)
        except ValidationError as exc:
            return Response(request_validation_error(exc), status=422)
        body, http_status = attribute_services().mutate(
            operation=operation, payload=payload, dry_run=payload.dry_run, request=request,
        )
        return Response(body, status=http_status)

    @extend_schema(
        operation_id="Batch Create Attributes",
        description=ATTRIBUTE_BATCH_CREATE_DESC,
        request=schemas.BatchCreateRequest,
        responses=MUTATION_RESPONSES,
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Preview attribute creation",
            value={
                "targets": [{
                    "sample_type": "Serum",
                    "attributes": [{
                        "title": "Concentration",
                        "sample_attribute_type": "Float",
                        "required": False,
                    }],
                }],
                "dry_run": True,
            },
            request_only=True,
        )],
    )
    @action(detail=False, methods=["post"], url_path="batch-create")
    def batch_create(self, request):
        return self._mutate(request, CREATE_REQUEST_ADAPTER, "create")

    @extend_schema(
        operation_id="Batch Patch Attributes",
        description=ATTRIBUTE_BATCH_PATCH_DESC,
        request=schemas.BatchPatchRequest,
        responses=MUTATION_RESPONSES,
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Preview clearing an attribute unit",
            value=_openapi.ATTRIBUTE_EXAMPLES[1]["value"],
            request_only=True,
        )],
    )
    @action(detail=False, methods=["patch"], url_path="batch-patch")
    def batch_patch(self, request):
        return self._mutate(request, PATCH_REQUEST_ADAPTER, "patch")

    @extend_schema(
        operation_id="Batch Delete Attributes",
        description=ATTRIBUTE_BATCH_DELETE_DESC,
        request=schemas.BatchDeleteRequest,
        responses=MUTATION_RESPONSES,
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Preview attribute deletion",
            value={
                "targets": [{"sample_type": "Serum", "attributes": ["Legacy Marker"]}],
                "dry_run": True,
            },
            request_only=True,
        )],
    )
    @action(detail=False, methods=["post"], url_path="batch-delete")
    def batch_delete(self, request):
        return self._mutate(request, DELETE_REQUEST_ADAPTER, "delete")

    @extend_schema(
        operation_id="Get Attribute Mutation Job",
        description=ATTRIBUTE_JOB_DESC,
        parameters=[OpenApiParameter(
            "job_id", OpenApiTypes.UUID, OpenApiParameter.PATH,
            description="Durable attribute-mutation job UUID.",
        )],
        responses={200: schemas.MutationJobStatusResponse, 404: schemas.AttributeErrorResponse, **ERROR_RESPONSES},
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Running attribute mutation", value=ATTRIBUTE_JOB_EXAMPLE,
            response_only=True, status_codes=["200"],
        )],
    )
    @action(detail=False, methods=["get"], url_path=r"jobs/(?P<job_id>[^/.]+)")
    def job(self, request, job_id=None):
        try:
            body = attribute_services().get_job(job_id, request)
        except (ObjectDoesNotExist, ValueError):
            return Response(public_error("job_not_found", "Job not found", field="job_id", submitted_identifier=job_id), status=404)
        return Response(body, status=200)

    @extend_schema(
        operation_id="Cancel Attribute Mutation Job",
        description=ATTRIBUTE_JOB_CANCEL_DESC,
        parameters=[OpenApiParameter(
            "job_id", OpenApiTypes.UUID, OpenApiParameter.PATH,
            description="Durable attribute-mutation job UUID to cancel.",
        )],
        responses={202: schemas.MutationJobStatusResponse, 404: schemas.AttributeErrorResponse, **ERROR_RESPONSES},
        tags=["Attributes"],
        examples=[OpenApiExample(
            "Cancellation accepted", value=ATTRIBUTE_JOB_EXAMPLE,
            response_only=True, status_codes=["202"],
        )],
    )
    @action(detail=False, methods=["post"], url_path=r"jobs/(?P<job_id>[^/.]+)/cancel")
    def job_cancel(self, request, job_id=None):
        try:
            job = attribute_services().get_job_object(job_id)
        except (ObjectDoesNotExist, ValueError):
            return Response(public_error("job_not_found", "Job not found", field="job_id", submitted_identifier=job_id), status=404)
        self.check_object_permissions(request, job)
        body, code = attribute_services().cancel_job(job_id, request)
        return Response(body, status=code)
