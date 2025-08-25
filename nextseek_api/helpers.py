import time
import logging
from typing import Any, Dict, Optional, Tuple

import requests
from django.conf import settings

from seek.seekdb import SeekDB


log = logging.getLogger(__name__)

JSONAPI_ACCEPT = 'application/vnd.api+json'


def get_auth(request) -> Optional[Tuple[str, str]]:
    """Return (username, password) for the current SEEK user, or None if unauthenticated."""
    seekdb = SeekDB(None, None, None)
    user = seekdb.getSeekLogin(request, False)
    if not user or not user.get('status'):
        return None
    return user['username'], user['password']


class SeekAPIClient:
    """Minimal SEEK API client using requests.Session and JSON:API headers."""

    def __init__(self) -> None:
        self.base_url: str = settings.SEEK_URL.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'Accept': JSONAPI_ACCEPT})
        self.timeout_s: int = 20

    def _request(self, method: str, path: str, request, params: Optional[Dict[str, Any]] = None,
                 json: Optional[Dict[str, Any]] = None):
        auth = get_auth(request)
        if not auth:
            # Synthesize a 401 response signature for the caller
            return None, 401, {'Content-Type': JSONAPI_ACCEPT}, None

        url = f"{self.base_url}{path}"
        t0 = time.time()
        resp = self.session.request(
            method=method.upper(),
            url=url,
            auth=auth,
            params=params,
            json=json,
            timeout=self.timeout_s,
        )
        dt_ms = (time.time() - t0) * 1000.0

        # Redact payload sizes rather than payloads; don't log secrets
        try:
            size = len(resp.content) if resp.content is not None else 0
        except Exception:
            size = -1
        log.info("seek_proxy method=%s path=%s status=%d latency_ms=%.1f resp_bytes=%d",
                 method.upper(), path, resp.status_code, dt_ms, size)

        return resp.content, resp.status_code, dict(resp.headers), resp

    # ---- SOP endpoints ----

    def list_sops(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/sops', request, params=params)

    def get_sop(self, request, sop_id: str):
        return self._request('GET', f'/sops/{sop_id}', request)

    def create_sop(self, request, payload: Dict[str, Any]):
        # Ensure JSON:API content-type for writes
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/sops', request, json=payload)

    def update_sop(self, request, sop_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/sops/{sop_id}', request, json=payload)


    # ---- DataFiles endpoints ----

    def list_data_files(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/data_files', request, params=params)

    def get_data_file(self, request, data_file_id: str, version: int):
        params = {'version': int(version)}
        return self._request('GET', f'/data_files/{data_file_id}', request, params=params)

    def create_data_file(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/data_files', request, json=payload)

    def update_data_file(self, request, data_file_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/data_files/{data_file_id}', request, json=payload)

    # ---- Projects endpoints ----

    def list_projects(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/projects', request, params=params)

    def get_project(self, request, project_id: str):
        return self._request('GET', f'/projects/{project_id}', request)

    def create_project(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/projects', request, json=payload)

    def update_project(self, request, project_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/projects/{project_id}', request, json=payload)

    # ---- People endpoints ----

    def list_people(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/people', request, params=params)

    def get_person(self, request, person_id: str):
        return self._request('GET', f'/people/{person_id}', request)

    def create_person(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/people', request, json=payload)

    def update_person(self, request, person_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/people/{person_id}', request, json=payload)

    # ---- Investigations endpoints ----

    def list_investigations(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/investigations', request, params=params)

    def get_investigation(self, request, investigation_id: str):
        return self._request('GET', f'/investigations/{investigation_id}', request)

    def create_investigation(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/investigations', request, json=payload)

    def update_investigation(self, request, investigation_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/investigations/{investigation_id}', request, json=payload)

    # ---- Assays endpoints ----

    def list_assays(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/assays', request, params=params)

    def get_assay(self, request, assay_id: str):
        return self._request('GET', f'/assays/{assay_id}', request)

    def create_assay(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/assays', request, json=payload)

    def update_assay(self, request, assay_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/assays/{assay_id}', request, json=payload)

    # ---- SampleTypes endpoints ----

    def list_sample_types(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/sample_types', request, params=params)

    def get_sample_type(self, request, sample_type_id: str):
        return self._request('GET', f'/sample_types/{sample_type_id}', request)

    def create_sample_type(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/sample_types', request, json=payload)

    def update_sample_type(self, request, sample_type_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/sample_types/{sample_type_id}', request, json=payload)

