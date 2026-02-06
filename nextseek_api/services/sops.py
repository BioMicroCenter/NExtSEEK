from typing import Any, Dict, List, Optional

import datetime
import json
import logging
import os
import tempfile
import zipfile

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
    SopBatchDownloadManifest,
    SopBatchDownloadManifestEntry,
    SopBatchDownloadManifestFailure,
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


def _resolve_sop_and_blob(client, request, req, allow_multi_blob_auto_select=False):
    """Resolve a SopDownloadRequest into SOP metadata and blob info.

    Returns a dict with:
      On success: success=True, seek_id, blob_meta, upstream_path, accept
      On multi_blob (409): success=False, multi_blob=True, candidates
      On failure: success=False, error (str), status (int)
    """
    # 1. Resolve identifier
    seek_id_str = None
    if req.seek_id is not None:
        seek_id_str = str(req.seek_id)
    else:
        seek_id_str = _resolve_uid_to_seek_id(req.uid_or_id)
    if seek_id_str is None:
        return {"success": False, "error": "SOP not found", "status": 404}

    # 2. Fetch SOP metadata
    body, code, headers, resp = client.get_sop(request, seek_id_str)
    if code == 401:
        return {"success": False, "error": "Authentication required", "status": 401}
    if code >= 400:
        return {"success": False, "error": f"Failed to fetch SOP metadata (status {code})", "status": 502}

    try:
        sop_data = json.loads(body or b"{}")
    except Exception:
        return {"success": False, "error": "Invalid upstream SOP response", "status": 502}

    attrs = sop_data.get("data", {}).get("attributes", {})

    # 3. Handle version mismatch
    version = attrs.get("version")
    latest_version = attrs.get("latest_version")
    if version is not None and latest_version is not None and version != latest_version:
        body, code, headers, resp = client.get_sop(request, seek_id_str)
        if code == 401:
            return {"success": False, "error": "Authentication required", "status": 401}
        if code >= 400:
            return {"success": False, "error": f"Failed to fetch latest SOP version (status {code})", "status": 502}
        try:
            sop_data = json.loads(body or b"{}")
        except Exception:
            return {"success": False, "error": "Invalid upstream SOP response", "status": 502}
        attrs = sop_data.get("data", {}).get("attributes", {})

    # 4. Discover content blob
    content_blobs = attrs.get("content_blobs", [])
    if not content_blobs:
        return {"success": False, "error": "No content blob available for SOP", "status": 404}

    if req.blob_id is not None:
        blob_id = req.blob_id
        blob_meta = next((b for b in content_blobs if str(b.get("id")) == str(blob_id)), content_blobs[0])
    elif len(content_blobs) == 1:
        blob_meta = content_blobs[0]
        blob_link = blob_meta.get("link", "")
        try:
            blob_id = int(blob_link.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            return {"success": False, "error": "Cannot parse blob id from SOP metadata", "status": 502}
    else:
        # Multiple blobs
        if allow_multi_blob_auto_select:
            blob_meta = content_blobs[0]
            blob_link = blob_meta.get("link", "")
            try:
                blob_id = int(blob_link.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                return {"success": False, "error": "Cannot parse blob id from SOP metadata", "status": 502}
        else:
            candidates = []
            for b in content_blobs:
                candidates.append({
                    "link": b.get("link"),
                    "original_filename": b.get("original_filename"),
                    "content_type": b.get("content_type"),
                })
            return {"success": False, "multi_blob": True, "candidates": candidates}

    # 5. Build upstream path and accept header
    asset_types = req.asset_types or "sops"
    effective_seek_id = req.seek_id if req.seek_id is not None else int(seek_id_str)

    fmt = req.output_format
    if fmt in (None, "original", "binary"):
        upstream_path = f"/{asset_types}/{effective_seek_id}/content_blobs/{blob_id}/download"
        accept = "*/*"
    elif fmt == "csv":
        upstream_path = f"/{asset_types}/{effective_seek_id}/content_blobs/{blob_id}"
        accept = "text/csv"
    elif fmt == "json":
        upstream_path = f"/{asset_types}/{effective_seek_id}/content_blobs/{blob_id}"
        accept = "application/json"
    else:
        return {"success": False, "error": "Unsupported output_format", "status": 422}

    return {
        "success": True,
        "seek_id": str(effective_seek_id),
        "blob_meta": blob_meta,
        "upstream_path": upstream_path,
        "accept": accept,
    }


def _deduplicate_filename(original_filename, seek_id, used_filenames):
    """Return a unique filename for the zip archive.

    If original_filename is already in used_filenames, return '{stem}_{seek_id}{ext}'.
    Adds the returned filename to used_filenames (mutates the set).
    """
    if original_filename not in used_filenames:
        used_filenames.add(original_filename)
        return original_filename
    stem, ext = os.path.splitext(original_filename)
    deduped = f"{stem}_{seek_id}{ext}"
    used_filenames.add(deduped)
    return deduped


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
            "Download SOP content blobs — supports single and batch modes.\n\n"
            "**Single mode** (JSON object): Pass a single SopDownloadRequest object. "
            "Returns the file as a streaming binary download. If a SOP has multiple "
            "content blobs, returns 409 with candidate metadata so the caller can "
            "re-request with an explicit blob_id.\n\n"
            "**Batch mode** (JSON array): Pass an array of SopDownloadRequest objects. "
            "Returns a zip archive containing all successfully downloaded files plus a "
            "manifest.json documenting successes and failures. Multi-blob SOPs "
            "auto-select the first content blob. The zip filename follows the pattern "
            "sops-{YYYY-MM-DD_HH}.zip. Duplicate filenames are resolved by appending "
            "the SEEK id.\n\n"
            "Examples: 'Download the GEX protocol document'; "
            "'Get the DNA adducts procedure file as a PDF'; "
            "'Fetch multiple protocols at once as a zip archive'"
        ),
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
            return self._download_single(request, data)
        elif isinstance(data, list):
            return self._download_batch(request, data)
        else:
            return HttpResponse(
                b'{"errors":[{"title":"Request body must be a JSON object or array"}]}',
                status=422, content_type='application/json',
            )

    def _download_single(self, request, data):
        """Handle single SOP download (original behavior)."""
        # --- Parse & validate request body ---
        try:
            req = SopDownloadRequest.model_validate(data)
        except Exception:
            return HttpResponse(
                b'{"errors":[{"title":"Invalid request body"}]}',
                status=422, content_type='application/json',
            )

        # --- Resolve SOP + blob via helper ---
        result = _resolve_sop_and_blob(self.client, request, req, allow_multi_blob_auto_select=False)

        if not result["success"]:
            if result.get("multi_blob"):
                return HttpResponse(
                    json.dumps({
                        "errors": [{
                            "title": "Multiple content blobs found",
                            "detail": "SOP has multiple content blobs. Specify blob_id to select one.",
                        }],
                        "candidates": result["candidates"],
                    }).encode(),
                    status=409, content_type='application/json',
                )
            st = result.get("status", 502)
            if st == 401:
                return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
            return HttpResponse(
                json.dumps({"errors": [{"title": result["error"]}]}).encode(),
                status=st, content_type='application/json',
            )

        blob_meta = result["blob_meta"]
        upstream_path = result["upstream_path"]
        accept = result["accept"]

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

    def _download_batch(self, request, data: list):
        """Handle batch SOP download — returns a zip archive with manifest.json."""
        if not data:
            return HttpResponse(
                b'{"errors":[{"title":"Empty list: provide at least one SOP to download"}]}',
                status=422, content_type='application/json',
            )

        successes: List[SopBatchDownloadManifestEntry] = []
        failures: List[SopBatchDownloadManifestFailure] = []
        used_filenames: set = set()

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        tmp_path = tmp.name
        tmp.close()

        try:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for item in data:
                    uid_or_id_label = str(item.get("uid_or_id", "unknown")) if isinstance(item, dict) else "unknown"

                    # Validate individual request
                    try:
                        req = SopDownloadRequest.model_validate(item)
                    except Exception as e:
                        failures.append(SopBatchDownloadManifestFailure(
                            uid_or_id=uid_or_id_label,
                            error=f"Validation error: {e}",
                        ))
                        continue

                    uid_or_id_label = req.uid_or_id or str(req.seek_id or "unknown")

                    # Resolve SOP + blob
                    result = _resolve_sop_and_blob(
                        self.client, request, req, allow_multi_blob_auto_select=True,
                    )

                    if not result["success"]:
                        error_msg = result.get("error", "Unknown resolution error")
                        if result.get("multi_blob"):
                            error_msg = "Multiple content blobs; auto-select failed"
                        failures.append(SopBatchDownloadManifestFailure(
                            uid_or_id=uid_or_id_label,
                            error=error_msg,
                        ))
                        continue

                    blob_meta = result["blob_meta"]
                    upstream_path = result["upstream_path"]
                    accept = result["accept"]
                    seek_id = result["seek_id"]

                    # Stream content blob
                    upstream_resp = None
                    try:
                        status_code, upstream_headers, upstream_resp = self.client.stream_content_blob(
                            request, path=upstream_path, accept=accept, params=request.query_params,
                        )
                    except Exception as exc:
                        failures.append(SopBatchDownloadManifestFailure(
                            uid_or_id=uid_or_id_label,
                            error=f"Upstream error: {exc}",
                        ))
                        continue

                    if status_code >= 400:
                        if upstream_resp is not None:
                            upstream_resp.close()
                        failures.append(SopBatchDownloadManifestFailure(
                            uid_or_id=uid_or_id_label,
                            error=f"Upstream HTTP {status_code}",
                        ))
                        continue

                    # Validate response content type
                    ct = (upstream_headers.get('Content-Type') or '').lower()
                    if 'text/html' in ct:
                        if upstream_resp is not None:
                            upstream_resp.close()
                        failures.append(SopBatchDownloadManifestFailure(
                            uid_or_id=uid_or_id_label,
                            error="Upstream returned HTML (likely unauthenticated)",
                        ))
                        continue

                    # Read content and write to zip
                    try:
                        content = upstream_resp.content
                    finally:
                        upstream_resp.close()

                    original_filename = blob_meta.get("original_filename") or f"sop_{seek_id}"
                    filename = _deduplicate_filename(original_filename, seek_id, used_filenames)
                    zf.writestr(filename, content)

                    successes.append(SopBatchDownloadManifestEntry(
                        filename=filename,
                        seek_id=seek_id,
                        uid_or_id=uid_or_id_label,
                    ))

                # Write manifest.json
                manifest = SopBatchDownloadManifest(
                    generated_at=datetime.datetime.utcnow().isoformat() + "Z",
                    total_requested=len(data),
                    total_success=len(successes),
                    total_failed=len(failures),
                    successes=successes,
                    failures=failures,
                )
                zf.writestr("manifest.json", manifest.model_dump_json(indent=2))

        except Exception:
            # Clean up temp file on unexpected error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Stream the zip file and clean up
        zip_size = os.path.getsize(tmp_path)
        zip_filename = f"sops-{datetime.datetime.utcnow().strftime('%Y-%m-%d_%H')}.zip"

        def _iter_and_cleanup(path):
            try:
                with open(path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        response = StreamingHttpResponse(
            _iter_and_cleanup(tmp_path),
            content_type='application/zip',
        )
        response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
        response['Content-Length'] = zip_size
        return response

