from rest_framework.pagination import PageNumberPagination
import time
import logging
import base64
from typing import Any, Dict, Optional, Tuple, List

import requests
from django.conf import settings

from seek.seekdb import SeekDB


log = logging.getLogger(__name__)

JSONAPI_ACCEPT = 'application/vnd.api+json'


def basic_auth_header(basic_tuple: Optional[Tuple[str, str]]) -> Dict[str, str]:
    """Build a UTF-8-safe HTTP Basic ``Authorization`` header.

    Never pass credentials to requests as ``auth=(user, password)``: requests
    encodes them with Latin-1, so a password containing any character outside
    that range (e.g. a non-ASCII letter) raises UnicodeEncodeError before the
    request is even sent. RFC 7617 leaves the credential charset to the server
    and SEEK reads UTF-8, so encode the header ourselves.

    Returns an empty dict for a falsy tuple, or for a tuple carrying a None
    username or password, so callers can merge it unconditionally. The None
    check matters for the seek/ call sites: SeekDB(server, None, None) builds
    a SeekAPI with no credentials (seek/seekdb.py:31), and stringifying that
    would send a literal ``None:None`` as the credential pair. An empty-string
    password is still a credential and is encoded normally.
    """
    if not basic_tuple:
        return {}
    if basic_tuple[0] is None or basic_tuple[1] is None:
        return {}
    raw = f"{basic_tuple[0]}:{basic_tuple[1]}"
    return {
        'Authorization': 'Basic ' + base64.b64encode(raw.encode('utf-8')).decode('ascii')
    }


def get_basic_auth(request) -> Optional[Tuple[str, str]]:
    try:
        auth_header = request.META.get('HTTP_AUTHORIZATION') or ''
        if not auth_header.lower().startswith('basic '):
            return None
        encoded_credentials = auth_header.split(' ', 1)[1]
        decoded = base64.b64decode(encoded_credentials).decode("utf-8")
        # Only split on the first colon to allow colons in password
        username, password = decoded.split(':', 1)
        return username, password
    except Exception:
        return None


def get_auth(request) -> Optional[Tuple[str, str]]:
    """Return (username, password) for the current SEEK user session, or None if unauthenticated.

    Note: This does NOT consider Token auth or direct Basic headers. Use resolve_seek_auth
    when you need to consider multiple upstream auth sources.
    """
    seekdb = SeekDB(None, None, None)
    user = seekdb.getSeekLogin(request, False)
    if not user or not user.get('status'):
        return None
    return user['username'], user['password']


def get_oauth_auth(request) -> Optional[str]:
    """Return the logged-in user's stored SEEK OAuth access token, or None.

    Distinct from ``get_token_auth`` below in both provenance and wire format,
    and conflating the two is the easy mistake here. ``get_token_auth`` reads a
    credential the *caller* presented on this request and it is sent on as
    ``Authorization: Token ...``. This one is a credential NExtSEEK holds on the
    user's behalf (issue #16, sub-project 1), and Doorkeeper requires it be
    presented as ``Bearer``.

    Returns None -- never raises -- for every ordinary "no token available"
    case: the feature flag is off, the request is anonymous, or the user has no
    usable token row. resolve_seek_auth runs on every proxied request, so a SEEK
    outage mid-refresh has to degrade to "no credential" and fall through to the
    caller's existing 401 path rather than 500 the request.
    """
    if not getattr(settings, "SEEK_OAUTH_ENABLED", False):
        return None
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        # Imported here, not at module scope: seek.oauth.service imports the
        # models, and this module is already inside the seekdb <-> seekapi
        # import knot (see SeekAPI.authHeaders, which imports back into here).
        from seek.oauth.service import get_valid_access_token

        return get_valid_access_token(user)
    except Exception:
        log.warning(
            "seek_oauth: could not resolve a stored access token for user_id=%s; "
            "falling through to the remaining auth sources.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return None


def get_token_auth(request) -> Optional[str]:
    try:
        # Prefer SEEK-specific header if you decide to introduce it in the future:
        h = request.META.get('HTTP_X_SEEK_AUTHORIZATION') or ''
        if not h:
            h = request.META.get('HTTP_AUTHORIZATION') or ''
        low = h.lower()
        if low.startswith('token ') or low.startswith('bearer '):
            val = h.split(' ', 1)[1].strip()
            # Remove optional surrounding quotes from Swagger inputs
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1].strip()
            return val
    except Exception:
        pass
    return None


def resolve_seek_auth(request, order: Optional[List[str]] = None) -> Tuple[Optional[Tuple[str, str]], Optional[Dict[str, str]]]:
    """
    Determine how to authenticate to SEEK for this incoming request.

    Returns a tuple: (basic_tuple, extra_headers)
    - basic_tuple: (username, password) when using Basic auth (session or header)
    - extra_headers: {'Authorization': 'Token <token>'} when using Token auth

    order controls which sources to try first. Allowed entries:
      - "BASIC": try HTTP Basic Authorization header
      - "SESSION": try session-derived Basic (SeekDB)
      - "OAUTH": try the logged-in user's stored SEEK OAuth token (#16)
      - "TOKEN": try HTTP Token Authorization header

    Default order is ["BASIC", "SESSION", "OAUTH", "TOKEN"].

    OAUTH sits after the two Basic sources deliberately. An explicitly presented
    credential -- a Basic header, or a session established by password login --
    keeps winning, so flag-off behaviour is provably identical to before this
    source existed: get_oauth_auth returns None when SEEK_OAUTH_ENABLED is off,
    and the loop falls through exactly as it used to. Sub-project 5 promotes
    OAUTH to the front, when the entries ahead of it are being deleted anyway.

    Note that OAUTH and TOKEN emit *different* schemes, and that is not an
    inconsistency to tidy up: TOKEN forwards a credential the caller presented
    and has always been sent on as ``Token``, while OAUTH is an OAuth2 access
    token, which Doorkeeper only accepts as ``Bearer``.
    """
    order = order or ["BASIC", "SESSION", "OAUTH", "TOKEN"]

    for method in order:
        if method == "BASIC":
            basic = get_basic_auth(request)
            if basic and basic[0] and basic[1]:
                return basic, {}
        elif method == "SESSION":
            sess = get_auth(request)
            if sess:
                return sess, {}
        elif method == "OAUTH":
            oauth_token = get_oauth_auth(request)
            if oauth_token:
                return None, { 'Authorization': f'Bearer {oauth_token}' }
        elif method == "TOKEN":
            token = get_token_auth(request)
            if token:
                return None, { 'Authorization': f'Token {token}' }

    return None, None


class SeekAPIClient:
    """Minimal SEEK API client using requests.Session and JSON:API headers."""

    def __init__(self) -> None:
        self.base_url: str = settings.SEEK_URL.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'Accept': JSONAPI_ACCEPT})
        self.timeout_s: int = 20

    def _request(self, method: str, path: str, request, params: Optional[Dict[str, Any]] = None,
                 json: Optional[Dict[str, Any]] = None):
        # Try BASIC -> SESSION -> TOKEN by default for proxy endpoints
        basic_tuple, extra_headers = resolve_seek_auth(request)
        if not basic_tuple and not extra_headers:
            # Synthesize a 401 response signature for the caller
            return None, 401, {'Content-Type': JSONAPI_ACCEPT}, None

        url = f"{self.base_url}{path}"
        t0 = time.time()
        # Build per-request headers to avoid mutating session-wide headers with Authorization
        headers = {'Accept': JSONAPI_ACCEPT}
        if extra_headers:
            headers.update(extra_headers)
        # Encode Basic auth header manually to preserve UTF-8 (requests uses
        # Latin-1, which corrupts passwords with special characters).
        headers.update(basic_auth_header(basic_tuple))
        resp = self.session.request(
            method=method.upper(),
            url=url,
            headers=headers,
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

    def get_current_person(self, request):
        return self._request('GET', f'/people/current', request)

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

    # ---- Studies endpoints ----

    def list_studies(self, request, params: Optional[Dict[str, Any]] = None):
        return self._request('GET', '/studies', request, params=params)

    def get_study(self, request, study_id: str):
        return self._request('GET', f'/studies/{study_id}', request)

    def create_study(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/studies', request, json=payload)

    def update_study(self, request, study_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/studies/{study_id}', request, json=payload)

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

    # ---- Samples endpoints ----

    def get_sample(self, request, sample_id: str):
        return self._request('GET', f'/samples/{sample_id}', request)

    def create_sample(self, request, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('POST', '/samples', request, json=payload)

    def update_sample(self, request, sample_id: str, payload: Dict[str, Any]):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('PATCH', f'/samples/{sample_id}', request, json=payload)

    def delete_sample(self, request, sample_id: str):
        self.session.headers.update({'Content-Type': JSONAPI_ACCEPT})
        return self._request('DELETE', f'/samples/{sample_id}', request)

    # ---- Streaming content blob download ----

    def stream_content_blob(self, request, path: str, accept: str = '*/*',
                            params: Optional[Dict[str, Any]] = None):
        """Stream-download content from SEEK.

        Uses per-request headers (does not mutate session-wide headers),
        ``stream=True`` to avoid buffering, and a longer read timeout suitable
        for large file transfers.

        Returns ``(status_code, headers_dict, requests.Response)`` on success,
        or ``(status_code, headers_dict, None)`` when authentication is missing.
        The caller is responsible for closing the response after consuming the stream.
        """
        basic_tuple, extra_headers = resolve_seek_auth(request)
        if not basic_tuple and not extra_headers:
            return 401, {'Content-Type': JSONAPI_ACCEPT}, None

        url = f"{self.base_url}{path}"
        headers = {'Accept': accept}
        if extra_headers:
            headers.update(extra_headers)
        headers.update(basic_auth_header(basic_tuple))

        t0 = time.time()
        resp = self.session.request(
            method='GET',
            url=url,
            headers=headers,
            params=params,
            stream=True,
            timeout=(10, 300),
        )
        dt_ms = (time.time() - t0) * 1000.0
        log.info("seek_proxy_stream path=%s status=%d latency_ms=%.1f",
                 path, resp.status_code, dt_ms)

        return resp.status_code, dict(resp.headers), resp

    # ---- Upload content blob ----

    def upload_content_blob(self, request, path: str, file_data: bytes,
                            content_type: str):
        """PUT binary content to a SEEK content blob endpoint.

        path: e.g. /sops/42/content_blobs/99
        Returns: (status_code, headers_dict, response_or_None)
        """
        basic_tuple, extra_headers = resolve_seek_auth(request)
        if not basic_tuple and not extra_headers:
            return 401, {'Content-Type': JSONAPI_ACCEPT}, None

        url = f"{self.base_url}{path}"
        headers = {'Content-Type': 'application/octet-stream'}
        if extra_headers:
            headers.update(extra_headers)
        headers.update(basic_auth_header(basic_tuple))

        t0 = time.time()
        resp = self.session.request(
            method='PUT',
            url=url,
            headers=headers,
            data=file_data,
            timeout=(10, 300),
        )
        dt_ms = (time.time() - t0) * 1000.0
        log.info("seek_upload path=%s status=%d latency_ms=%.1f bytes=%d",
                 path, resp.status_code, dt_ms, len(file_data))

        return resp.status_code, dict(resp.headers), resp

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


def paginate_rows_in_envelope(request, envelope, rows_key='rows', paginator_cls=StandardResultsSetPagination):
    rows = envelope.get(rows_key)
    if not isinstance(rows, list):
        return envelope
    paginator = paginator_cls()
    page = paginator.paginate_queryset(rows, request)
    envelope[rows_key] = page
    if 'total' not in envelope:
        envelope['total'] = len(rows)
    return envelope

def resolve_sampletype_to_seek_id(s: str) -> Optional[str]:
    """Resolve a sample type display title to a SEEK numeric id string.
    - If input is numeric, return as-is.
    - Else, use DBtable_sampletype("DEFAULT").getSampleTypeID(title).
    """
    val = str(s)
    if val.isdigit():
        return val
    try:
        from seek.dbtable_sampletype import DBtable_sampletype  # local import for optional dependency
        rid = DBtable_sampletype("DEFAULT").getSampleTypeID(val)
        if isinstance(rid, int) and rid > 0:
            return str(rid)
        return None
    except Exception:
        return None

