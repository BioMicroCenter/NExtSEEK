"""
DRF ViewSet for Schema RAG endpoints.

Provides HTTP endpoints for:
- Ingesting OpenAPI schemas for RAG retrieval
- Retrieving relevant API endpoints using semantic search

Implementation: Phase 7
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError

from nextseek_api.helpers import resolve_seek_auth
from nextseek_api.models import (
    IngestRequest,
    IngestResponse,
    IngestResult,
    RetrieveRequest,
    RetrieveResponse,
)
from nextseek_api.schema_rag import service as schema_rag_service
from nextseek_api.schema_rag.errors import (
    SCHEMA_FETCH_FAILED,
    SCHEMA_TOO_LARGE,
    SESSION_MISSING_OR_EXPIRED,
)


# Error code to HTTP status mapping
ERROR_CODE_TO_STATUS = {
    SCHEMA_FETCH_FAILED: status.HTTP_502_BAD_GATEWAY,
    SCHEMA_TOO_LARGE: status.HTTP_400_BAD_REQUEST,
    SESSION_MISSING_OR_EXPIRED: status.HTTP_404_NOT_FOUND,
}


class SchemaRAGViewSet(viewsets.ViewSet):
    """
    ViewSet for Schema RAG operations.
    
    Provides endpoints for ingesting OpenAPI schemas and retrieving
    relevant endpoints using semantic search.
    """
    permission_classes = [IsAuthenticated]

    def _check_auth(self, request):
        """
        Check authentication using BASIC, SESSION, or TOKEN auth.
        
        Returns:
            tuple: (is_authenticated, error_response)
            - If authenticated: (True, None)
            - If not authenticated: (False, Response with 401)
        """
        basic_tuple, extra_headers = resolve_seek_auth(request, ["BASIC", "SESSION", "TOKEN"])
        if not basic_tuple and not extra_headers and not request.user.is_authenticated:
            return False, Response(
                {"detail": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED
            )
        return True, None

    @extend_schema(
        operation_id="Ingest OpenAPI Schema",
        description=(
            "Ingest an OpenAPI schema from a URL for RAG retrieval. "
            "Creates a session that can be used for subsequent retrieve calls. "
            "The session has a configurable TTL (default 15 minutes)."
        ),
        request=IngestRequest,
        responses={
            201: IngestResponse,
            400: {"description": "Schema too large - exceeds maximum endpoint limit"},
            401: {"description": "Authentication required"},
            422: {"description": "Invalid request payload"},
            502: {"description": "Failed to fetch or parse schema from URL"},
        },
        tags=['Schema RAG'],
        examples=[
            OpenApiExample(
                name="Ingest SEEK OpenAPI Schema",
                value={
                    "schema_url": "https://fairdomhub.org/api/definitions/openapi-v3-resolved.yaml",
                    "ttl_minutes": 30
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Ingest Success Response",
                value={
                    "session_id": "abc123def456",
                    "schema_url": "https://fairdomhub.org/api/definitions/openapi-v3-resolved.yaml",
                    "ttl_minutes": 30,
                    "expires_at": "2024-01-15T12:30:00Z",
                    "num_endpoints": 150
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="ingest")
    def ingest(self, request):
        """
        Ingest an OpenAPI schema from a URL.
        
        Fetches the schema, parses endpoints, and stores them in a per-session
        DuckDB database for semantic retrieval.
        """
        # 1. Auth gating
        is_auth, error_response = self._check_auth(request)
        if not is_auth:
            return error_response

        # 2. Parse IngestRequest from request.data
        try:
            req = IngestRequest.model_validate(request.data)
        except ValidationError as e:
            return Response(
                {"detail": "Invalid request", "errors": e.errors()},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # 3. Call schema_rag_service.ingest_schema(req)
        result: IngestResult = schema_rag_service.ingest_schema(req)

        # 4. Map result to HTTP response
        if result.success:
            # Return response with 201 Created
            return Response(
                result.response.model_dump(mode='json'),
                status=status.HTTP_201_CREATED
            )
        else:
            # Map error_code to HTTP status
            http_status = ERROR_CODE_TO_STATUS.get(
                result.error.error_code,
                status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            return Response(
                {
                    "error_code": result.error.error_code,
                    "message": result.error.message,
                    "details": result.error.details,
                },
                status=http_status
            )

    @extend_schema(
        operation_id="Retrieve API Endpoints",
        description=(
            "Retrieve relevant API endpoints from an ingested schema using semantic search. "
            "Uses multi-pass scoring with embedding similarity and optional term boosting. "
            "Returns endpoints in minimal or full mode based on request."
        ),
        request=RetrieveRequest,
        responses={
            200: RetrieveResponse,
            401: {"description": "Authentication required"},
            422: {"description": "Invalid request payload"},
        },
        tags=['Schema RAG'],
        examples=[
            OpenApiExample(
                name="Retrieve by Session ID",
                value={
                    "session_id": "abc123def456",
                    "query": "How do I create a new sample?",
                    "mode": "minimal",
                    "top_k": 5,
                    "include_debug": True
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Retrieve by Schema URL",
                value={
                    "schema_url": "https://fairdomhub.org/api/definitions/openapi-v3-resolved.yaml",
                    "query": "Get all projects for a user",
                    "mode": "full",
                    "top_k": 3,
                    "resolved_terms": {"project": "research project", "user": "person"}
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Minimal Mode Response",
                value={
                    "session_id": "abc123def456",
                    "mode": "minimal",
                    "endpoints_minimal": [
                        {
                            "operationId": "createSample",
                            "method": "POST",
                            "path": "/samples",
                            "tags": ["Samples"],
                            "description": "Create a new sample"
                        }
                    ],
                    "debug": {
                        "used_fallback_full": False,
                        "used_enriched_minimal_examples": False,
                        "similarity_threshold_effective": 0.7,
                        "num_candidates_initial": 150,
                        "num_candidates_final": 1,
                        "scores": [0.85]
                    }
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="retrieve")
    def retrieve_endpoints(self, request):
        """
        Retrieve relevant API endpoints using semantic search.
        
        Supports retrieval by session_id or schema_url. Uses multi-pass
        scoring with embedding similarity and optional resolved_terms boosting.
        """
        # 1. Auth gating
        is_auth, error_response = self._check_auth(request)
        if not is_auth:
            return error_response

        # 2. Parse RetrieveRequest from request.data
        try:
            req = RetrieveRequest.model_validate(request.data)
        except ValidationError as e:
            return Response(
                {"detail": "Invalid request", "errors": e.errors()},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # 3. Call schema_rag_service.retrieve_endpoints(req)
        response: RetrieveResponse = schema_rag_service.retrieve_endpoints(req)

        # 4. Return serialized RetrieveResponse
        # Use mode='json' to properly serialize datetime and other types
        return Response(
            response.model_dump(mode='json', exclude_none=True),
            status=status.HTTP_200_OK
        )

