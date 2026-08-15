"""Thin DRF surface for the native attribute API."""
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_view,
)
from pydantic import ValidationError
from rest_framework import exceptions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

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


@extend_schema_view(
    list=extend_schema(responses={200: schemas.AttributeListResponse, **ERROR_RESPONSES}),
    retrieve=extend_schema(
        parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        responses={200: schemas.AttributeRecord, 404: schemas.AttributeErrorResponse, **ERROR_RESPONSES},
    ),
    search=extend_schema(request=schemas.SearchRequest, responses={200: schemas.AttributeListResponse, **ERROR_RESPONSES}),
    batch_create=extend_schema(request=schemas.BatchCreateRequest, responses=MUTATION_RESPONSES),
    batch_patch=extend_schema(request=schemas.BatchPatchRequest, responses=MUTATION_RESPONSES),
    batch_delete=extend_schema(request=schemas.BatchDeleteRequest, responses=MUTATION_RESPONSES),
    job=extend_schema(
        parameters=[OpenApiParameter("job_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
        responses={200: schemas.MutationJobStatusResponse, 404: schemas.AttributeErrorResponse, **ERROR_RESPONSES},
    ),
    job_cancel=extend_schema(
        parameters=[OpenApiParameter("job_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
        responses={202: schemas.MutationJobStatusResponse, 404: schemas.AttributeErrorResponse, **ERROR_RESPONSES},
    ),
)
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

    def list(self, request):
        try:
            return Response(attribute_services().list(request.query_params), status=200)
        except ScalarInputError as exc:
            return Response(exc.as_attribute_error_response(), status=400)

    def retrieve(self, request, pk=None):
        try:
            identifier = parse_positive_int(pk, field="id")
        except ScalarInputError as exc:
            return Response(exc.as_attribute_error_response(), status=400)
        value = attribute_services().retrieve(identifier)
        if value is None:
            return Response(public_error("attribute_not_found", "Attribute not found", field="id", submitted_identifier=identifier), status=404)
        return Response(value, status=200)

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

    @action(detail=False, methods=["post"], url_path="batch-create")
    def batch_create(self, request):
        return self._mutate(request, CREATE_REQUEST_ADAPTER, "create")

    @action(detail=False, methods=["patch"], url_path="batch-patch")
    def batch_patch(self, request):
        return self._mutate(request, PATCH_REQUEST_ADAPTER, "patch")

    @action(detail=False, methods=["post"], url_path="batch-delete")
    def batch_delete(self, request):
        return self._mutate(request, DELETE_REQUEST_ADAPTER, "delete")

    @action(detail=False, methods=["get"], url_path=r"jobs/(?P<job_id>[^/.]+)")
    def job(self, request, job_id=None):
        try:
            body = attribute_services().get_job(job_id, request)
        except (ObjectDoesNotExist, ValueError):
            return Response(public_error("job_not_found", "Job not found", field="job_id", submitted_identifier=job_id), status=404)
        return Response(body, status=200)

    @action(detail=False, methods=["post"], url_path=r"jobs/(?P<job_id>[^/.]+)/cancel")
    def job_cancel(self, request, job_id=None):
        try:
            job = attribute_services().get_job_object(job_id)
        except (ObjectDoesNotExist, ValueError):
            return Response(public_error("job_not_found", "Job not found", field="job_id", submitted_identifier=job_id), status=404)
        self.check_object_permissions(request, job)
        body, code = attribute_services().cancel_job(job_id, request)
        return Response(body, status=code)
