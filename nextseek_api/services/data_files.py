from typing import Optional

import json
from django.conf import settings
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    DATAFILE_LIST_DESC,
    DATAFILE_FETCH_DESC,
    DATAFILE_CREATE_DESC,
    DATAFILE_UPDATE_DESC,
    DATAFILE_DOWNLOAD_DESC,
)
from nextseek_api.models import (
    DataFileListResponse,
    DataFileSingleResponse,
    DataFileCreateRequest,
    DataFileUpdateRequest,
    DataFileDownloadRequest,
    ContentBlobUploadResponse,
)
from nextseek_api.services.content_blobs import (
    _resolve_uid_to_seek_id as _resolve_uid,
    download_single,
    download_batch,
    upload_content_blobs,
    check_unmatched_files,
)


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Backward-compat wrapper — delegates to shared resolver."""
    return _resolve_uid(uid_or_id, "data_files")


class DataFileProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /data_files
    @extend_schema(
        operation_id="List DataFiles",
        description=DATAFILE_LIST_DESC,
        responses={200: DataFileListResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="List DataFiles",
                value={
                    "data": [
                        {
                            "id": "560",
                            "type": "data_files",
                            "attributes": {"title": "DF-20240101-01_Sample-X.csv"},
                            "links": {"self": "/data_files/560"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/data_files?page[number]=1&page[size]=100"},
                    "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_data_files(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # GET /data_files/{uid}?version=int
    @extend_schema(
        description=DATAFILE_FETCH_DESC,
        operation_id="Fetch a DataFile",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)'),
            OpenApiParameter(name='version', type=int, location=OpenApiParameter.QUERY, required=True, description='Required DataFile version to fetch', default=1),
        ],
        responses={200: DataFileSingleResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Get DataFile",
                value={
                    "data": {
                        "id": "560",
                        "type": "data_files",
                        "attributes": {"title": "DF-20240101-01_Sample-X.csv"},
                        "relationships": {},
                        "links": {"self": "/data_files/560"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        version = request.query_params.get('version')
        try:
            # Default to 1 if not provided; still documented as required with default
            version_int = int(version) if version is not None else 1
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"version must be an integer"}]}', status=422, content_type='application/json')

        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"DataFile not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_data_file(request, seek_id, version_int)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /data_files
    @extend_schema(
        operation_id="Create a DataFile",
        description=DATAFILE_CREATE_DESC,
        request=DataFileCreateRequest,
        responses={201: DataFileSingleResponse, 200: DataFileSingleResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Create DataFile",
                value={
                    "data": {
                        "type": "data_files",
                        "attributes": {
                            "title": "DF-20240101-01_Sample-X.csv",
                            "content_blobs": [
                                {"url": "https://example.com/file.csv", "content_type": "text/csv"}
                            ]
                        },
                        "relationships": {"projects": {"data": [{"id": "560", "type": "projects"}]}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        has_files = bool(request.FILES)

        if has_files:
            metadata_str = request.data.get('metadata', '{}')
            try:
                metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                payload = DataFileCreateRequest.model_validate(metadata).to_seek_payload()
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
                payload = DataFileCreateRequest.model_validate(request.data).to_seek_payload()
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_data_file(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileSingleResponse.model_validate(data)
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

        blob_results = upload_content_blobs(self.client, request, "data_files", asset_id, content_blobs_meta, files)

        any_failed = any(r.status == "failed" for r in blob_results)
        upload_resp = ContentBlobUploadResponse(
            asset_id=asset_id, asset_type="data_files",
            blob_uploads=blob_results, asset_data=data)

        resp_status = 207 if any_failed else 201
        return HttpResponse(
            upload_resp.model_dump_json(), status=resp_status,
            content_type='application/json')

    # PATCH /data_files/{uid}
    @extend_schema(
        operation_id="Update a DataFile",
        description=DATAFILE_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request=DataFileUpdateRequest,
        responses={200: DataFileSingleResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Patch DataFile",
                value={
                    "data": {
                        "type": "data_files",
                        "id": "560",
                        "attributes": {"description": "Updated description"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = DataFileUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        seek_id = _resolve_uid_to_seek_id(uid)

        if seek_id is not None:
            body_id = payload.get('data', {}).get('id')
            if body_id is None:
                payload['data']['id'] = seek_id
            elif str(body_id) != str(seek_id):
                return HttpResponse(b'{"errors":[{"title":"Payload id does not match resolved uid"}]}', status=422, content_type='application/json')
        else:
            if not payload.get('data', {}).get('id'):
                return HttpResponse(b'{"errors":[{"title":"DataFile not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_data_file(request, payload['data']['id'], payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /data_files/download
    @extend_schema(
        operation_id="Download DataFile content blob",
        description=DATAFILE_DOWNLOAD_DESC,
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.BINARY},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Download by SEEK ID",
                value={"uid_or_id": "560"},
                request_only=True,
            ),
            OpenApiExample(
                name="Batch download (zip)",
                value=[{"uid_or_id": "560"}, {"uid_or_id": "561"}],
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="download")
    def download(self, request):
        """Dispatcher: single dict → streaming file; list → zip archive."""
        data = request.data
        if isinstance(data, dict):
            return download_single(self.client, request, "data_files", data)
        elif isinstance(data, list):
            return download_batch(self.client, request, "data_files", data)
        else:
            return HttpResponse(
                b'{"errors":[{"title":"Request body must be a JSON object or array"}]}',
                status=422, content_type='application/json',
            )
