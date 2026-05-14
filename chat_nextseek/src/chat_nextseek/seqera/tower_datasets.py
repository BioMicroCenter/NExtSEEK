"""Tower Datasets v2 client.

Used when chat_nextseek can't stage samplesheets to a shared filesystem
visible to the Tower compute env (e.g., chat_nextseek runs on a workstation
without the HPC's /orcd mount). The CSV is uploaded to Tower-managed object
storage; Tower handles staging it onto the compute env at launch time.

API endpoints (Seqera Platform):
- POST /workspaces/{wsId}/datasets — create or fetch dataset metadata
- POST /workspaces/{wsId}/datasets/{datasetId}/upload — multipart upload of
  a new version
- GET  /workspaces/{wsId}/datasets — list (used to find existing by name)

The upload response includes a `url` we can pass directly as the `--input`
parameter in `paramsText` at launch time.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import requests as _requests
    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover
    _requests = None
    _HAVE_REQUESTS = False

from .tower_client import TowerAPIError, TowerClient


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_dataset_name(name: str) -> str:
    """Tower dataset names must be alphanumeric + dot/underscore/dash."""
    cleaned = _NAME_SAFE_RE.sub("-", (name or "").strip())[:64]
    return cleaned or "chat-nextseek-dataset"


def find_dataset_by_name(client: TowerClient, workspace_id: str | int, name: str) -> dict[str, Any] | None:
    resp = client.request(
        "GET",
        f"/workspaces/{workspace_id}/datasets",
    )
    for ds in resp.get("datasets") or []:
        if isinstance(ds, dict) and ds.get("name") == name:
            return ds
    return None


def create_or_get_dataset(
    client: TowerClient,
    workspace_id: str | int,
    name: str,
    *,
    description: str = "",
) -> dict[str, Any]:
    """Idempotent: create a new dataset, or return the existing one with the same name.
    Returns the dataset dict (with `id`)."""
    existing = find_dataset_by_name(client, workspace_id, name)
    if existing is not None:
        return existing
    resp = client.request(
        "POST",
        f"/workspaces/{workspace_id}/datasets",
        body={"name": name, "description": description or "Created by chat_nextseek"},
    )
    ds = resp.get("dataset") or resp
    if not isinstance(ds, dict) or not ds.get("id"):
        raise TowerAPIError(f"Dataset create returned unexpected payload: {resp}")
    return ds


def upload_dataset_version(
    client: TowerClient,
    workspace_id: str | int,
    dataset_id: str,
    file_path: Path,
    *,
    media_type: str = "text/csv",
    has_header: bool = True,
) -> dict[str, Any]:
    """Upload a new dataset version (multipart). Returns the new version dict
    which includes the `url` Nextflow can read."""
    if not _HAVE_REQUESTS:
        raise RuntimeError(
            "The `requests` library is required for Tower dataset uploads. "
            "Install via `uv pip install requests`."
        )
    url = f"{client.endpoint}/workspaces/{workspace_id}/datasets/{dataset_id}/upload"
    headers = {"Authorization": f"Bearer {client.token}"}
    params = {"header": "true" if has_header else "false"}
    with file_path.open("rb") as fh:
        files = {"file": (file_path.name, fh, media_type)}
        try:
            resp = _requests.post(
                url,
                headers=headers,
                params=params,
                files=files,
                timeout=120,
            )
        except _requests.RequestException as e:
            raise TowerAPIError(f"Tower dataset upload network error: {e!r}")
    if resp.status_code >= 400:
        raise TowerAPIError(
            f"Tower dataset upload {file_path.name} → HTTP {resp.status_code}: "
            f"{resp.text[:500]}"
        )
    try:
        return resp.json()
    except ValueError:
        raise TowerAPIError(f"Tower dataset upload returned non-JSON: {resp.text[:200]}")


def upload_samplesheet_as_dataset(
    client: TowerClient,
    workspace_id: str | int,
    csv_path: Path,
    *,
    name: str,
    description: str = "",
    media_type: str = "text/csv",
) -> dict[str, Any]:
    """End-to-end: create-or-get dataset, upload CSV as a new version,
    return a dict with `dataset_id`, `version`, `url`, `name`.
    """
    safe_name = safe_dataset_name(name)
    ds = create_or_get_dataset(client, workspace_id, safe_name, description=description)
    upload_resp = upload_dataset_version(
        client,
        workspace_id,
        ds["id"],
        csv_path,
        media_type=media_type,
    )
    version = upload_resp.get("version") or upload_resp
    if isinstance(version, dict):
        version_url = version.get("url") or upload_resp.get("url")
        version_num = version.get("version") or version.get("id")
    else:
        version_url = upload_resp.get("url")
        version_num = None
    if not version_url:
        raise TowerAPIError(
            f"Tower upload response missing `url`: {upload_resp}"
        )
    return {
        "dataset_id": ds["id"],
        "name": safe_name,
        "version": version_num,
        "url": version_url,
    }
