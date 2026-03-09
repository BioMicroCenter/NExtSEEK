"""Shared content blob download/upload logic for SOPs and DataFiles."""
from typing import Any, Dict, List, Optional
import datetime
import json
import logging
import os
import tempfile
import zipfile

import requests as upstream_requests
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.models import (
    SopDownloadRequest,
    DataFileDownloadRequest,
    BatchDownloadManifest,
    BatchDownloadManifestEntry,
    BatchDownloadManifestFailure,
    ContentBlobUploadStatus,
)

log = logging.getLogger(__name__)

# --- Asset-type dispatch config ---
_ASSET_CONFIG = {
    "sops": {
        "get_method": "get_sop",
        "default_fallback_name": "sop",
        "request_model": SopDownloadRequest,
    },
    "data_files": {
        "get_method": "get_data_file",
        "default_fallback_name": "datafile",
        "request_model": DataFileDownloadRequest,
    },
}


def _resolve_uid_to_seek_id(uid_or_id: str, asset_type: str) -> Optional[str]:
    """Resolve UID/title to SEEK numeric ID for the given asset type."""
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        if asset_type == "sops":
            from seek.dbtable_sops import DBtable_sops
            db = DBtable_sops("DEFAULT")
            records = db.queryRecordsByConstraint({"title": s})
            if isinstance(records, list) and len(records) == 1 and records[0].get('id') is not None:
                return str(records[0]['id'])
        elif asset_type == "data_files":
            from seek.dbtable_data_files import DBtable_data_files
            from django.db.models import Q
            db = DBtable_data_files("DEFAULT")
            exact = db.queryRecordsByConstraint({"title": s}) or []
            if isinstance(exact, list) and len(exact) == 1 and exact[0].get('id') is not None:
                return str(exact[0]['id'])
            if "_" not in s:
                prefix_rows = db.queryRecordsCustom(Q(title__startswith=s + "_")) or []
                if len(prefix_rows) == 1 and prefix_rows[0].get('id') is not None:
                    return str(prefix_rows[0]['id'])
                if len(prefix_rows) > 1:
                    best = max(prefix_rows, key=lambda r: int(r.get('id', -1)))
                    if best and best.get('id') is not None:
                        return str(best['id'])
    except Exception:
        return None
    return None


def _get_asset(client, request, asset_type: str, seek_id: str):
    """Fetch asset metadata from SEEK, dispatching to correct client method."""
    if asset_type == "sops":
        return client.get_sop(request, seek_id)
    elif asset_type == "data_files":
        return client.get_data_file(request, seek_id, 1)
    raise ValueError(f"Unknown asset_type: {asset_type}")


def resolve_asset_and_blobs(client, request, asset_type: str, req,
                            allow_multi_blob_auto_select=False):
    """Resolve asset + content blob. Returns dict with success/error info.

    Generic version of sops.py:_resolve_sop_and_blob, parameterized by asset_type.
    """
    # 1. Resolve identifier
    seek_id_str = None
    if req.seek_id is not None:
        seek_id_str = str(req.seek_id)
    else:
        seek_id_str = _resolve_uid_to_seek_id(req.uid_or_id, asset_type)
    if seek_id_str is None:
        return {"success": False, "error": f"{asset_type} not found", "status": 404}

    # 2. Fetch asset metadata
    body, code, headers, resp = _get_asset(client, request, asset_type, seek_id_str)
    if code == 401:
        return {"success": False, "error": "Authentication required", "status": 401}
    if code >= 400:
        return {"success": False, "error": f"Failed to fetch metadata (status {code})", "status": 502}

    try:
        asset_data = json.loads(body or b"{}")
    except Exception:
        return {"success": False, "error": "Invalid upstream response", "status": 502}

    attrs = asset_data.get("data", {}).get("attributes", {})

    # 3. Handle version mismatch
    version = attrs.get("version")
    latest_version = attrs.get("latest_version")
    if version is not None and latest_version is not None and version != latest_version:
        body, code, headers, resp = _get_asset(client, request, asset_type, seek_id_str)
        if code == 401:
            return {"success": False, "error": "Authentication required", "status": 401}
        if code >= 400:
            return {"success": False, "error": f"Failed to fetch latest version (status {code})", "status": 502}
        try:
            asset_data = json.loads(body or b"{}")
        except Exception:
            return {"success": False, "error": "Invalid upstream response", "status": 502}
        attrs = asset_data.get("data", {}).get("attributes", {})

    # 4. Discover content blob
    content_blobs = attrs.get("content_blobs", [])
    if not content_blobs:
        return {"success": False, "error": "No content blob available", "status": 404}

    if req.blob_id is not None:
        blob_id = req.blob_id
        blob_meta = next((b for b in content_blobs if str(b.get("id")) == str(blob_id)), content_blobs[0])
    elif len(content_blobs) == 1:
        blob_meta = content_blobs[0]
        blob_link = blob_meta.get("link", "")
        try:
            blob_id = int(blob_link.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            return {"success": False, "error": "Cannot parse blob id from metadata", "status": 502}
    else:
        if allow_multi_blob_auto_select:
            blob_meta = content_blobs[0]
            blob_link = blob_meta.get("link", "")
            try:
                blob_id = int(blob_link.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                return {"success": False, "error": "Cannot parse blob id from metadata", "status": 502}
        else:
            candidates = [{"link": b.get("link"), "original_filename": b.get("original_filename"),
                           "content_type": b.get("content_type")} for b in content_blobs]
            return {"success": False, "multi_blob": True, "candidates": candidates}

    # 5. Build upstream path
    effective_asset_types = req.asset_types or asset_type
    effective_seek_id = req.seek_id if req.seek_id is not None else int(seek_id_str)

    fmt = req.output_format
    if fmt in (None, "original", "binary"):
        upstream_path = f"/{effective_asset_types}/{effective_seek_id}/content_blobs/{blob_id}/download"
        accept = "*/*"
    elif fmt == "csv":
        upstream_path = f"/{effective_asset_types}/{effective_seek_id}/content_blobs/{blob_id}"
        accept = "text/csv"
    elif fmt == "json":
        upstream_path = f"/{effective_asset_types}/{effective_seek_id}/content_blobs/{blob_id}"
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


def deduplicate_filename(original_filename, seek_id, used_filenames):
    """Return a unique filename for zip archive. Mutates used_filenames set."""
    if original_filename not in used_filenames:
        used_filenames.add(original_filename)
        return original_filename
    stem, ext = os.path.splitext(original_filename)
    deduped = f"{stem}_{seek_id}{ext}"
    used_filenames.add(deduped)
    return deduped


def download_single(client, request, asset_type: str, data: dict):
    """Handle single asset download. Returns HttpResponse."""
    request_model = _ASSET_CONFIG[asset_type]["request_model"]
    try:
        req = request_model.model_validate(data)
    except Exception:
        return HttpResponse(b'{"errors":[{"title":"Invalid request body"}]}',
                            status=422, content_type='application/json')

    result = resolve_asset_and_blobs(client, request, asset_type, req)

    if not result["success"]:
        if result.get("multi_blob"):
            return HttpResponse(
                json.dumps({"errors": [{"title": "Multiple content blobs found",
                                        "detail": "Specify blob_id to select one."}],
                            "candidates": result["candidates"]}).encode(),
                status=409, content_type='application/json')
        st = result.get("status", 502)
        if st == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
        return HttpResponse(json.dumps({"errors": [{"title": result["error"]}]}).encode(),
                            status=st, content_type='application/json')

    blob_meta = result["blob_meta"]
    upstream_path = result["upstream_path"]
    accept = result["accept"]

    try:
        status_code, upstream_headers, upstream_resp = client.stream_content_blob(
            request, path=upstream_path, accept=accept, params=request.query_params)
    except upstream_requests.Timeout:
        return HttpResponse(b'{"errors":[{"title":"Upstream timeout"}]}', status=504, content_type='application/json')
    except upstream_requests.RequestException as exc:
        log.warning("stream_content_blob failed: %s", exc)
        return HttpResponse(b'{"errors":[{"title":"Upstream connection error"}]}', status=502, content_type='application/json')

    if status_code == 401:
        return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
    if status_code in (403, 404):
        if upstream_resp is not None:
            upstream_resp.close()
        return HttpResponse(
            json.dumps({"errors": [{"title": "Upstream error", "status": str(status_code)}]}).encode(),
            status=status_code, content_type='application/json')
    if status_code >= 500:
        if upstream_resp is not None:
            upstream_resp.close()
        return HttpResponse(
            json.dumps({"errors": [{"title": "Upstream server error", "status": str(status_code)}]}).encode(),
            status=502, content_type='application/json')
    if status_code >= 400:
        if upstream_resp is not None:
            upstream_resp.close()
        return HttpResponse(
            json.dumps({"errors": [{"title": "Upstream error", "status": str(status_code)}]}).encode(),
            status=status_code, content_type='application/json')

    ct = (upstream_headers.get('Content-Type') or '').lower()
    if 'text/html' in ct:
        if upstream_resp is not None:
            upstream_resp.close()
        return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)"}]}',
                            status=502, content_type='application/json')

    content_type = upstream_headers.get('Content-Type', 'application/octet-stream')
    original_filename = blob_meta.get("original_filename")
    content_disposition = upstream_headers.get('Content-Disposition')
    if not content_disposition:
        content_disposition = f'attachment; filename="{original_filename}"' if original_filename else 'attachment'

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


def download_batch(client, request, asset_type: str, data: list):
    """Handle batch download — returns zip with manifest.json."""
    if not data:
        return HttpResponse(b'{"errors":[{"title":"Empty list: provide at least one item to download"}]}',
                            status=422, content_type='application/json')

    request_model = _ASSET_CONFIG[asset_type]["request_model"]
    fallback_name = _ASSET_CONFIG[asset_type]["default_fallback_name"]
    successes, failures, used_filenames = [], [], set()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    tmp_path = tmp.name
    tmp.close()

    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for item in data:
                uid_label = str(item.get("uid_or_id", "unknown")) if isinstance(item, dict) else "unknown"
                try:
                    req = request_model.model_validate(item)
                except Exception as e:
                    failures.append(BatchDownloadManifestFailure(uid_or_id=uid_label, error=f"Validation error: {e}"))
                    continue

                uid_label = req.uid_or_id or str(req.seek_id or "unknown")
                result = resolve_asset_and_blobs(client, request, asset_type, req, allow_multi_blob_auto_select=True)

                if not result["success"]:
                    error_msg = result.get("error", "Unknown resolution error")
                    if result.get("multi_blob"):
                        error_msg = "Multiple content blobs; auto-select failed"
                    failures.append(BatchDownloadManifestFailure(uid_or_id=uid_label, error=error_msg))
                    continue

                blob_meta, upstream_path, accept, seek_id = (
                    result["blob_meta"], result["upstream_path"], result["accept"], result["seek_id"])

                upstream_resp = None
                try:
                    status_code, upstream_headers, upstream_resp = client.stream_content_blob(
                        request, path=upstream_path, accept=accept, params=request.query_params)
                except Exception as exc:
                    failures.append(BatchDownloadManifestFailure(uid_or_id=uid_label, error=f"Upstream error: {exc}"))
                    continue

                if status_code >= 400:
                    if upstream_resp is not None:
                        upstream_resp.close()
                    failures.append(BatchDownloadManifestFailure(uid_or_id=uid_label, error=f"Upstream HTTP {status_code}"))
                    continue

                ct = (upstream_headers.get('Content-Type') or '').lower()
                if 'text/html' in ct:
                    if upstream_resp is not None:
                        upstream_resp.close()
                    failures.append(BatchDownloadManifestFailure(uid_or_id=uid_label, error="Upstream returned HTML"))
                    continue

                try:
                    content = upstream_resp.content
                finally:
                    upstream_resp.close()

                original_filename = blob_meta.get("original_filename") or f"{fallback_name}_{seek_id}"
                filename = deduplicate_filename(original_filename, seek_id, used_filenames)
                zf.writestr(filename, content)
                successes.append(BatchDownloadManifestEntry(filename=filename, seek_id=seek_id, uid_or_id=uid_label))

            manifest = BatchDownloadManifest(
                generated_at=datetime.datetime.utcnow().isoformat() + "Z",
                total_requested=len(data), total_success=len(successes),
                total_failed=len(failures), successes=successes, failures=failures)
            zf.writestr("manifest.json", manifest.model_dump_json(indent=2))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    zip_size = os.path.getsize(tmp_path)
    zip_filename = f"{asset_type}-{datetime.datetime.utcnow().strftime('%Y-%m-%d_%H')}.zip"

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

    response = StreamingHttpResponse(_iter_and_cleanup(tmp_path), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
    response['Content-Length'] = zip_size
    return response


def check_unmatched_files(content_blobs_meta: list, files: list) -> list:
    """Return list of filenames in files that don't match any blob placeholder."""
    blob_filenames = {b.get("original_filename") for b in content_blobs_meta}
    return [f.name for f in files if f.name not in blob_filenames]


def auto_populate_content_blobs(metadata: dict, files: list) -> dict:
    """Merge uploaded files into metadata's content_blobs array.

    - If content_blobs is missing or empty, generate entries from all files.
    - If content_blobs has explicit entries, keep them and add entries for any
      files not already covered by original_filename match.
    - Returns a new dict (does not mutate the original).
    """
    import copy
    if not files:
        return metadata

    result = copy.deepcopy(metadata)
    attrs = result.get("data", {}).get("attributes", {})
    existing_blobs = attrs.get("content_blobs") or []

    covered_filenames = {b.get("original_filename") for b in existing_blobs}

    for f in files:
        if f.name not in covered_filenames:
            existing_blobs.append({
                "original_filename": f.name,
                "content_type": getattr(f, "content_type", "application/octet-stream"),
            })
            covered_filenames.add(f.name)

    attrs["content_blobs"] = existing_blobs
    result["data"]["attributes"] = attrs

    return result


def upload_content_blobs(client, request, asset_type: str, asset_id: str,
                         content_blobs_meta: list, files: list):
    """Upload file(s) to SEEK content blob endpoints.

    Matches files to content_blob placeholders by original_filename.
    Returns list of ContentBlobUploadStatus.
    """
    results = []
    file_map = {}
    for f in files:
        file_map[f.name] = f

    for blob in content_blobs_meta:
        blob_link = blob.get("link", "")
        try:
            blob_id = str(blob_link.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            results.append(ContentBlobUploadStatus(
                blob_id="unknown", original_filename=blob.get("original_filename"),
                status="failed", error="Cannot parse blob_id from link"))
            continue

        original_filename = blob.get("original_filename")
        matched_file = file_map.get(original_filename)
        if matched_file is None:
            continue

        file_data = matched_file.read()
        path = f"/{asset_type}/{asset_id}/content_blobs/{blob_id}"
        try:
            status_code, headers, resp = client.upload_content_blob(
                request, path=path, file_data=file_data,
                content_type=blob.get("content_type", "application/octet-stream"))
        except Exception as exc:
            results.append(ContentBlobUploadStatus(
                blob_id=blob_id, original_filename=original_filename,
                status="failed", error=str(exc)))
            continue

        if status_code < 300:
            results.append(ContentBlobUploadStatus(
                blob_id=blob_id, original_filename=original_filename, status="uploaded"))
        else:
            results.append(ContentBlobUploadStatus(
                blob_id=blob_id, original_filename=original_filename,
                status="failed", error=f"SEEK returned {status_code}"))

    return results
