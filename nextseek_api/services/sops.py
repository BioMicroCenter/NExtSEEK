from typing import Any, Dict, Optional

import json
import logging
import requests as upstream_requests

from django.http import HttpResponse, StreamingHttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.models import (
    SopCreateRequest,
    SopUpdateRequest,
    SopSingleResponse,
    SopListResponse,
    SopDownloadRequest,
    SeekContentBlobPathParams,
    SeekContentBlobDownloadRequest,
)

log = logging.getLogger(__name__)
from seek.dbtable_sops import DBtable_sops
from django.conf import settings


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment that may be a numeric SEEK id or a NExtSEEK SOP UID.
    Returns a SEEK id as string, or None if not found/ambiguous.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        dbsop = DBtable_sops("DEFAULT")
        records = dbsop.queryRecordsByConstraint({"title": s})
        if isinstance(records, list) and len(records) == 1 and records[0].get('id') is not None:
            return str(records[0]['id'])
    except Exception:
        return None
    return None


class SopProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    # Ensure router renders /sops/{uid}
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /sops
    @extend_schema(
        operation_id="List SOPs",
        description="Retrieve all standard operating procedures (protocols) you have access to. Returns protocol titles, descriptions, associated projects, and file attachment metadata. Examples: 'Show me all available protocols'; 'What procedures are documented for sample collection?'",
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
        description="Fetch details for a specific protocol document by ID or name. Returns full metadata including title, description, version history, attached files, and linked projects. Optionally specify a version to retrieve historical revisions. Examples: 'Show me the tissue processing protocol'; 'What is the current version of the blood draw procedure?'",
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
        seek_id = _resolve_uid_to_seek_id(uid)
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
        description="Upload a new standard operating procedure document. Attach protocol files (PDF, Word, etc.) and associate with projects. Returns the created protocol with its assigned ID and metadata. Examples: 'Add a new necropsy protocol for the primate study'; 'Upload the updated sample handling procedure'",
        request=SopCreateRequest,
        responses={201: SopSingleResponse, 200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Create Sop",
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
                }
            )
        ],
    )
    def create(self, request):
        # Validate request
        try:
            payload = SopCreateRequest.model_validate(request.data).model_dump(exclude_none=True)
        except Exception as e:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_sop(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        # Validate response
        try:
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # PATCH /sops/{uid}
    @extend_schema(
        operation_id="Update a SOP",
        description="Revise an existing protocol document by ID or name. Update title, description, attached files, or project associations. Returns the updated protocol with all current metadata. Examples: 'Update the description for the imaging protocol'; 'Change the project association for the cell culture procedure'",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request=SopUpdateRequest,
        responses={200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Patch Sop",
                value={
                    "data": {
                        "type": "sops",
                        "id": "132",
                        "attributes": {"title": "A Maximally Patched SOP"},
                        "relationships": {"projects": {"data": [{"id": "2653", "type": "projects"}]}}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        # Validate and normalize body (allows uid in body)
        try:
            update_req = SopUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # Resolve path param to SEEK id
        seek_id = _resolve_uid_to_seek_id(uid)
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

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /sops/download
    @extend_schema(
        operation_id="Download SOP content blob",
        description=(
            "Download the file content of a standard operating procedure (protocol) by ID or UID. "
            "Automatically resolves the SOP identifier, fetches the latest version, discovers the "
            "attached content blob, and streams the file download. Supports optional format conversion "
            "to CSV or JSON via output_format. If a SOP has multiple content blobs, returns 409 with "
            "candidate metadata so the caller can re-request with an explicit blob_id. "
            "Examples: 'Download the GEX protocol document'; "
            "'Get the DNA adducts procedure file as a PDF'; "
            "'Fetch the sequencing analysis protocol for offline review'"
        ),
        request=SopDownloadRequest,
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
        ],
    )
    @action(detail=False, methods=["post"], url_path="download")
    def download(self, request):
        # --- Parse & validate request body ---
        try:
            req = SopDownloadRequest.model_validate(request.data)
        except Exception:
            return HttpResponse(
                b'{"errors":[{"title":"Invalid request body"}]}',
                status=422, content_type='application/json',
            )

        # --- Resolve SOP identifier ---
        seek_id_str: Optional[str] = None
        if req.seek_id is not None:
            seek_id_str = str(req.seek_id)
        else:
            seek_id_str = _resolve_uid_to_seek_id(req.uid_or_id)
        if seek_id_str is None:
            return HttpResponse(
                b'{"errors":[{"title":"SOP not found"}]}',
                status=404, content_type='application/json',
            )

        # --- Fetch SOP metadata (latest version) ---
        body, code, headers, resp = self.client.get_sop(request, seek_id_str)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
        if code >= 400:
            return HttpResponse(
                json.dumps({"errors": [{"title": "Failed to fetch SOP metadata", "status": str(code)}]}).encode(),
                status=502, content_type='application/json',
            )

        try:
            sop_data = json.loads(body or b"{}")
        except Exception:
            return HttpResponse(
                b'{"errors":[{"title":"Invalid upstream SOP response"}]}',
                status=502, content_type='application/json',
            )

        attrs = sop_data.get("data", {}).get("attributes", {})

        # If version != latest_version, refetch with the latest version
        version = attrs.get("version")
        latest_version = attrs.get("latest_version")
        if version is not None and latest_version is not None and version != latest_version:
            body, code, headers, resp = self.client.get_sop(request, seek_id_str)
            if code == 401:
                return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
            if code >= 400:
                return HttpResponse(
                    json.dumps({"errors": [{"title": "Failed to fetch latest SOP version", "status": str(code)}]}).encode(),
                    status=502, content_type='application/json',
                )
            try:
                sop_data = json.loads(body or b"{}")
            except Exception:
                return HttpResponse(
                    b'{"errors":[{"title":"Invalid upstream SOP response"}]}',
                    status=502, content_type='application/json',
                )
            attrs = sop_data.get("data", {}).get("attributes", {})

        # --- Discover content blob ---
        content_blobs = attrs.get("content_blobs", [])
        if not content_blobs:
            return HttpResponse(
                b'{"errors":[{"title":"No content blob available for SOP"}]}',
                status=404, content_type='application/json',
            )

        if req.blob_id is not None:
            # Client explicitly specified a blob_id override
            blob_id = req.blob_id
            # Find matching blob for original_filename (for Content-Disposition default)
            blob_meta = next((b for b in content_blobs if str(b.get("id")) == str(blob_id)), content_blobs[0])
        elif len(content_blobs) == 1:
            blob_meta = content_blobs[0]
            blob_link = blob_meta.get("link", "")
            # Parse blob_id from link: e.g. /sops/142/content_blobs/246
            try:
                blob_id = int(blob_link.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                return HttpResponse(
                    b'{"errors":[{"title":"Cannot parse blob id from SOP metadata"}]}',
                    status=502, content_type='application/json',
                )
        else:
            # Multiple blobs — 409 Conflict with candidate info
            candidates = []
            for b in content_blobs:
                candidates.append({
                    "link": b.get("link"),
                    "original_filename": b.get("original_filename"),
                    "content_type": b.get("content_type"),
                })
            return HttpResponse(
                json.dumps({
                    "errors": [{
                        "title": "Multiple content blobs found",
                        "detail": "SOP has multiple content blobs. Specify blob_id to select one.",
                    }],
                    "candidates": candidates,
                }).encode(),
                status=409, content_type='application/json',
            )

        # --- Build upstream path ---
        asset_types = req.asset_types or "sops"
        effective_seek_id = req.seek_id if req.seek_id is not None else int(seek_id_str)

        # Determine upstream behavior based on output_format
        fmt = req.output_format
        if fmt in (None, "original", "binary"):
            # Binary download: GET /{asset_types}/{id}/content_blobs/{blob_id}/download
            upstream_path = f"/{asset_types}/{effective_seek_id}/content_blobs/{blob_id}/download"
            accept = "*/*"
        elif fmt == "csv":
            # Read with conversion: GET /{asset_types}/{id}/content_blobs/{blob_id}
            upstream_path = f"/{asset_types}/{effective_seek_id}/content_blobs/{blob_id}"
            accept = "text/csv"
        elif fmt == "json":
            upstream_path = f"/{asset_types}/{effective_seek_id}/content_blobs/{blob_id}"
            accept = "application/json"
        else:
            return HttpResponse(
                b'{"errors":[{"title":"Unsupported output_format"}]}',
                status=422, content_type='application/json',
            )

        # --- Stream download from SEEK ---
        try:
            status_code, upstream_headers, upstream_resp = self.client.stream_content_blob(
                request, path=upstream_path, accept=accept, params=request.query_params,
            )
        except upstream_requests.Timeout:
            return HttpResponse(
                b'{"errors":[{"title":"Upstream timeout"}]}',
                status=504, content_type='application/json',
            )
        except upstream_requests.RequestException as exc:
            log.warning("stream_content_blob failed: %s", exc)
            return HttpResponse(
                b'{"errors":[{"title":"Upstream connection error"}]}',
                status=502, content_type='application/json',
            )

        if status_code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
        if status_code in (403, 404):
            if upstream_resp is not None:
                upstream_resp.close()
            return HttpResponse(
                json.dumps({"errors": [{"title": "Upstream error", "status": str(status_code)}]}).encode(),
                status=status_code, content_type='application/json',
            )
        if status_code >= 500:
            if upstream_resp is not None:
                upstream_resp.close()
            return HttpResponse(
                json.dumps({"errors": [{"title": "Upstream server error", "status": str(status_code)}]}).encode(),
                status=502, content_type='application/json',
            )
        if status_code >= 400:
            if upstream_resp is not None:
                upstream_resp.close()
            return HttpResponse(
                json.dumps({"errors": [{"title": "Upstream error", "status": str(status_code)}]}).encode(),
                status=status_code, content_type='application/json',
            )

        # --- Validate upstream response before streaming ---
        ct = (upstream_headers.get('Content-Type') or '').lower()
        if 'text/html' in ct:
            if upstream_resp is not None:
                upstream_resp.close()
            return HttpResponse(
                b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)"}]}',
                status=502, content_type='application/json',
            )

        # --- Build streaming response ---
        content_type = upstream_headers.get('Content-Type', 'application/octet-stream')
        original_filename = blob_meta.get("original_filename")
        content_disposition = upstream_headers.get('Content-Disposition')
        if not content_disposition:
            if original_filename:
                content_disposition = f'attachment; filename="{original_filename}"'
            else:
                content_disposition = 'attachment'

        def _iter_and_close():
            try:
                yield from upstream_resp.iter_content(chunk_size=8192)
            finally:
                upstream_resp.close()

        response = StreamingHttpResponse(_iter_and_close(), content_type=content_type)
        response['Content-Disposition'] = content_disposition
        content_length = upstream_headers.get('Content-Length')
        if content_length:
            response['Content-Length'] = content_length
        return response

