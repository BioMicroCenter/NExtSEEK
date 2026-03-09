from typing import Any, Dict, List, Optional

import json
import logging

from django.conf import settings
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    SOP_LIST_DESC,
    SOP_FETCH_DESC,
    SOP_CREATE_DESC,
    SOP_UPDATE_DESC,
    SOP_DOWNLOAD_DESC,
)
from nextseek_api.models import (
    SopCreateRequest,
    SopUpdateRequest,
    SopSingleResponse,
    SopListResponse,
    SopDownloadRequest,
    SeekContentBlobPathParams,
    SeekContentBlobDownloadRequest,
    ContentBlobUploadResponse,
)
from nextseek_api.services.content_blobs import (
    _resolve_uid_to_seek_id,
    download_single,
    download_batch,
    upload_content_blobs,
    check_unmatched_files,
    auto_populate_content_blobs,
)

log = logging.getLogger(__name__)


class SopProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    client = SeekAPIClient()
    # Ensure router renders /sops/{uid}
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /sops
    @extend_schema(
        operation_id="List SOPs",
        description=SOP_LIST_DESC,
        responses={200: SopListResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="List Sops",
                value={
                    "data": [
                        {
                            "id": "131",
                            "type": "sops",
                            "attributes": {"title": "This Sop"},
                            "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}},
                            "links": {"self": "/sops/131"},
                            "meta": {}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/sops?page[number]=1&page[size]=100"},
                    "meta": {"base_url": settings.SEEK_URL, "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_sops(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        # Validate response
        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SopListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # GET /sops/{uid}
    @extend_schema(
        description=SOP_FETCH_DESC,
        operation_id="Fetch a SOP",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)'),
            OpenApiParameter(name='version', type=int, location=OpenApiParameter.QUERY, required=False, description='Optional SOP version to fetch')
        ],
        responses={200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Get Sop",
                value={
                    "data": {
                        "id": "132",
                        "type": "sops",
                        "attributes": {"title": "A Maximal SOP"},
                        "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}},
                        "links": {"self": "/sops/132"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        # Optional version query param (proxied as-is by client layer if SEEK supports it)
        _ = request.query_params.get('version')
        seek_id = _resolve_uid_to_seek_id(uid, "sops")
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_sop(request, seek_id)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /sops
    @extend_schema(
        operation_id="Create a SOP",
        description=SOP_CREATE_DESC,
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "One or more files to upload as content blobs.",
                    },
                    "metadata": {
                        "type": "string",
                        "description": (
                            'JSON string with SOP metadata. Minimum: '
                            '{"data":{"type":"sops","attributes":{"title":"..."},'
                            '"relationships":{"projects":{"data":[{"id":"...","type":"projects"}]}}}}'
                            ' — content_blobs are auto-generated from uploaded files if omitted.'
                        ),
                    },
                },
                "required": ["metadata"],
            },
            "application/json": SopCreateRequest,
        },
        responses={201: SopSingleResponse, 207: ContentBlobUploadResponse, 200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Create SOP (JSON only, no file)",
                value={
                    "data": {
                        "type": "sops",
                        "attributes": {
                            "title": "A Maximal SOP",
                            "content_blobs": [
                                {"original_filename": "a_pdf_file.pdf", "content_type": "application/pdf"}
                            ],
                            "policy": {"access": "download", "permissions": [{"resource": {"id": "2558", "type": "projects"}, "access": "edit"}]}
                        },
                        "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}}
                    }
                },
                media_type="application/json",
            ),
        ],
    )
    def create(self, request):
        has_files = bool(request.FILES)

        if has_files:
            metadata_str = request.data.get('metadata', '{}')
            try:
                metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                metadata = auto_populate_content_blobs(metadata, request.FILES.getlist('file'))
                payload = SopCreateRequest.model_validate(metadata).model_dump(exclude_none=True)
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid metadata"}]}', status=422, content_type='application/json')

            max_bytes = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', 200 * 1024 * 1024)
            total_size = sum(f.size for f in request.FILES.getlist('file'))
            if total_size > max_bytes:
                return HttpResponse(
                    json.dumps({"errors": [{"title": f"Total upload size {total_size} exceeds limit {max_bytes}"}]}).encode(),
                    status=413, content_type='application/json')
        else:
            try:
                payload = SopCreateRequest.model_validate(request.data).model_dump(exclude_none=True)
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_sop(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        if not has_files or code >= 400:
            ct = headers.get('Content-Type', 'application/json')
            return HttpResponse(body, status=code, content_type=ct)

        # Upload files to content blob endpoints
        asset_id = data.get("data", {}).get("id")
        content_blobs_meta = data.get("data", {}).get("attributes", {}).get("content_blobs", [])
        files = request.FILES.getlist('file')

        unmatched = check_unmatched_files(content_blobs_meta, files)
        if unmatched:
            return HttpResponse(
                json.dumps({"errors": [{"title": "Uploaded files do not match any content_blob placeholder",
                                        "detail": f"Unmatched filenames: {unmatched}"}]}).encode(),
                status=400, content_type='application/json')

        blob_results = upload_content_blobs(self.client, request, "sops", asset_id, content_blobs_meta, files)

        any_failed = any(r.status == "failed" for r in blob_results)
        upload_resp = ContentBlobUploadResponse(
            asset_id=asset_id, asset_type="sops",
            blob_uploads=blob_results, asset_data=data)

        resp_status = 207 if any_failed else 201
        return HttpResponse(
            upload_resp.model_dump_json(), status=resp_status,
            content_type='application/json')

    # PATCH /sops/{uid}
    @extend_schema(
        operation_id="Update a SOP",
        description=SOP_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "One or more files to replace content blobs.",
                    },
                    "metadata": {
                        "type": "string",
                        "description": 'JSON string with SOP patch metadata (e.g., {"data":{"type":"sops","id":"42","attributes":{"title":"..."}}}). content_blobs auto-generated from files.',
                    },
                },
                "required": ["metadata"],
            },
            "application/json": SopUpdateRequest,
        },
        responses={200: SopSingleResponse, 207: ContentBlobUploadResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Patch SOP (JSON only)",
                value={
                    "data": {
                        "type": "sops",
                        "id": "132",
                        "attributes": {"title": "A Maximally Patched SOP"},
                        "relationships": {"projects": {"data": [{"id": "2653", "type": "projects"}]}}
                    }
                },
                media_type="application/json",
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        has_files = bool(request.FILES)

        if has_files:
            metadata_str = request.data.get('metadata', '{}')
            try:
                metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                metadata = auto_populate_content_blobs(metadata, request.FILES.getlist('file'))
                update_req = SopUpdateRequest.model_validate(metadata)
                payload = update_req.to_seek_payload()
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid metadata"}]}', status=422, content_type='application/json')

            max_bytes = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', 200 * 1024 * 1024)
            total_size = sum(f.size for f in request.FILES.getlist('file'))
            if total_size > max_bytes:
                return HttpResponse(
                    json.dumps({"errors": [{"title": f"Total upload size {total_size} exceeds limit {max_bytes}"}]}).encode(),
                    status=413, content_type='application/json')
        else:
            try:
                update_req = SopUpdateRequest.model_validate(request.data)
                payload = update_req.to_seek_payload()
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # Resolve path param to SEEK id
        seek_id = _resolve_uid_to_seek_id(uid, "sops")
        if seek_id is None and isinstance(update_req.data.id, str):
            seek_id = update_req.data.id
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_sop(request, seek_id, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        # Validate response
        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        if not has_files or code >= 400:
            ct = headers.get('Content-Type', 'application/json')
            return HttpResponse(body, status=code, content_type=ct)

        # Upload files to content blob endpoints
        asset_id = data.get("data", {}).get("id")
        content_blobs_meta = data.get("data", {}).get("attributes", {}).get("content_blobs", [])
        files = request.FILES.getlist('file')

        unmatched = check_unmatched_files(content_blobs_meta, files)
        if unmatched:
            return HttpResponse(
                json.dumps({"errors": [{"title": "Uploaded files do not match any content_blob placeholder",
                                        "detail": f"Unmatched filenames: {unmatched}"}]}).encode(),
                status=400, content_type='application/json')

        blob_results = upload_content_blobs(self.client, request, "sops", asset_id, content_blobs_meta, files)

        any_failed = any(r.status == "failed" for r in blob_results)
        upload_resp = ContentBlobUploadResponse(
            asset_id=asset_id, asset_type="sops",
            blob_uploads=blob_results, asset_data=data)

        resp_status = 207 if any_failed else 200
        return HttpResponse(
            upload_resp.model_dump_json(), status=resp_status,
            content_type='application/json')

    # POST /sops/download
    @extend_schema(
        operation_id="Download SOP content blob",
        description=SOP_DOWNLOAD_DESC,
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.BINARY},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Download by SEEK ID",
                description="Download a SOP's content blob using its numeric SEEK ID",
                value={"uid_or_id": "142"},
                request_only=True,
            ),
            OpenApiExample(
                name="Download by SOP title/UID",
                description="Download a SOP's content blob using its NExtSEEK UID (title)",
                value={"uid_or_id": "P.ESS-251028-V1_NG_8-GEX.docx"},
                request_only=True,
            ),
            OpenApiExample(
                name="Download as CSV",
                description="Download content blob with CSV conversion (SEEK readContentBlob with Accept: text/csv)",
                value={"uid_or_id": "142", "output_format": "csv"},
                request_only=True,
            ),
            OpenApiExample(
                name="Download with explicit blob_id",
                description="Specify blob_id when a SOP has multiple content blobs",
                value={"uid_or_id": "142", "blob_id": 246},
                request_only=True,
            ),
            OpenApiExample(
                name="Batch download (zip)",
                description="Download multiple SOPs as a zip archive",
                value=[{"uid_or_id": "142"}, {"uid_or_id": "P.ESS-251028-V1_NG_8-GEX.docx"}],
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="download")
    def download(self, request):
        """Dispatcher: single dict → streaming file; list → zip archive."""
        data = request.data
        if isinstance(data, dict):
            return download_single(self.client, request, "sops", data)
        elif isinstance(data, list):
            return download_batch(self.client, request, "sops", data)
        else:
            return HttpResponse(
                b'{"errors":[{"title":"Request body must be a JSON object or array"}]}',
                status=422, content_type='application/json',
            )
