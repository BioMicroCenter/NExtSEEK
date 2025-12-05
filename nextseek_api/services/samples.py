from typing import Optional, Any

import json
import logging
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from pydantic import ValidationError
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient, resolve_sampletype_to_seek_id
from nextseek_api.helpers import paginate_rows_in_envelope
from nextseek_api.models import (
    SampleSingleResponse,
    SampleCreateRequest,
    SampleUpdateRequest,
    SampleAdvancedSearchRequest,
    SampleAdvancedSearchResult,
)

log = logging.getLogger(__name__)

try:
    from seek.dbtable_sample import DBtable_sample
except Exception:  # pragma: no cover - optional resolver
    DBtable_sample = None  # type: ignore


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment to a SEEK sample id.
    - If numeric, use as-is.
    - Else: attempt to resolve via DBtable_sample().getSampleID(uid) when available.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        if DBtable_sample is None:
            return None
        dbs = DBtable_sample()
        sid = dbs.getSampleID(s)
        sid = int(sid) if sid is not None else 0
        return str(sid) if sid > 0 else None
    except Exception:
        return None


class SampleProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        description="Fetch a single Sample by SEEK id or NExtSEEK UID. Returns sample metadata including title, UUID, created/updated timestamps, and IDs for related entities (sample_type, creators, projects, assays, data_files). Examples: 'Show me the details of sample NHP-220630FLY-2-PUB'; 'What assays are linked to TIS-230324BOO-39-PUB?'",
        operation_id="Fetch a Sample",
        parameters=[
            OpenApiParameter(
                name='uid',
                type=str,
                location=OpenApiParameter.PATH,
                description='SEEK id (numeric) or Sample UUID (string)'
            ),
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
        ],
        responses={200: SampleSingleResponse},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Get Sample",
                value={
                    "data": {
                        "id": "321",
                        "type": "samples",
                        "attributes": {"title": "A Sample"},
                        "relationships": {
                            "sample_type": {"data": {"type": "sample_types", "id": "12"}},
                            "creators": {"data": []},
                            "projects": {"data": []},
                            "people": {"data": []},
                            "assays": {"data": []},
                            "data_files": {"data": []}
                        },
                        "links": {"self": "/samples/321"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Sample not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_sample(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleSingleResponse.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_proxy.validation_error action=retrieve errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_proxy.validation_exception action=retrieve error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create a Sample",
        description="Create a new Sample in SEEK. Requires sample type relationship and attributes. Returns the created sample with its assigned ID, UUID, and metadata. Examples: 'Register a new tissue sample for the water study'; 'Add a new blood draw sample for a monkey taken during this visit (PAV-220630FLY-3)'",
        parameters=[
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
        ],
        request=SampleCreateRequest,
        responses={201: SampleSingleResponse, 200: SampleSingleResponse},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Create Sample",
                value={
                    "data": {
                        "type": "samples",
                        "attributes": {"title": "A Sample"},
                        "relationships": {"sample_type": {"data": {"type": "sample_types", "id": "12"}}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            # Best-effort normalization happens inside model using optional db_resolver if supplied
            payload = SampleCreateRequest.model_validate(request.data).to_seek_payload(db_resolver=None)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_sample(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = json.loads(body or b"{}")
            SampleSingleResponse.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_proxy.validation_error action=create errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_proxy.validation_exception action=create error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update a Sample",
        description="Update an existing Sample by SEEK id or NExtSEEK UID. Accepts partial updates via attribute_map. Returns the updated sample with all current metadata. Examples: 'Update NHP-220630FLY-1-PUB scientist to John Doe'; 'Change the study name for sample TIS-230324BOO-39-PUB to John Doe's study'",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or Sample UUID (string)'),
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
        ],
        request=SampleUpdateRequest,
        responses={200: SampleSingleResponse},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Patch Sample",
                value={
                    "data": {
                        "type": "samples",
                        "id": "321",
                        "attributes": {
                            "attribute_map": {
                            "title": "Revised title"
                            }
                        }
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = SampleUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload(db_resolver=None)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        path_id = str(uid) if uid is not None else None
        body_id: Optional[str] = payload.get('data', {}).get('id')

        seek_id: Optional[str] = None
        if path_id and path_id.isdigit():
            seek_id = path_id
            if body_id is not None and str(body_id) != str(seek_id):
                return HttpResponse(b'{"errors":[{"title":"Payload id does not match path id"}]}', status=422, content_type='application/json')
            if body_id is None:
                payload['data']['id'] = str(seek_id)
        else:
            if body_id and str(body_id).isdigit():
                seek_id = str(body_id)
            elif path_id:
                resolved = _resolve_uid_to_seek_id(path_id)
                if resolved:
                    seek_id = resolved
                    if body_id is not None and str(body_id) != str(seek_id):
                        return HttpResponse(b'{"errors":[{"title":"Payload id does not match resolved uid"}]}', status=422, content_type='application/json')
                    payload['data']['id'] = str(seek_id)

        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Sample not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_sample(request, str(seek_id), payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleSingleResponse.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_proxy.validation_error action=partial_update errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_proxy.validation_exception action=partial_update error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # Optional delete endpoint, behind feature flag if needed by caller
    @extend_schema(
        operation_id="Delete a Sample",
        description="Delete a Sample by SEEK id or NExtSEEK UID. Permanently removes the sample from SEEK. Returns confirmation on success. Examples: 'Remove sample NHP-220630FLY-1-PUB from the system'; 'Delete all records for sample 321'",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or Sample UUID (string)')
        ],
        responses={200: None},
        tags=['Samples'],
    )
    def destroy(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Sample not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.delete_sample(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
        # Pass through upstream response on success; SEEK returns okResponse JSON
        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


class SampleAdvancedSearchViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Advanced Search Samples",
        description="Search samples by type, attribute filters, and text matching. Supports multiple sample types, attribute-based filtering, and partial/exact match modes. Returns paginated list of matching samples with full metadata. Can also retrieve all samples of a given type by leaving filter fields empty. Examples: 'Group all monkeys by sex and necropsy date'; 'Find all mice from water study that were treated with NDMA'; 'Show me all samples associated with T cell depletion'; 'Create a barplot comparing the fraction of tumor values for all DFCI3 patient biopsies'; 'How many monkeys have both CT scan data and sequencing data?'",
        parameters=[
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
            OpenApiParameter(
                name='page',
                type=int,
                location=OpenApiParameter.QUERY,
                description='1-based page number'
            ),
            OpenApiParameter(
                name='page_size',
                type=int,
                location=OpenApiParameter.QUERY,
                description='Items per page (default 100, max 1000)'
            ),
        ],
        request=SampleAdvancedSearchRequest,
        responses={200: SampleAdvancedSearchResult},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Numeric sampletype",
                value={
                    "sampletype": "2",
                    "filter_searchText": "lung",
                    "attribute": "Organ",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Multiple sampletypes and single attribute",
                value={
                    "sampletype": ["TIS", "CEL"],
                    "filter_searchText": "lung",
                    "attribute": "Name",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Attribute list with AND logic",
                value={
                    "sampletype": ["TIS"],
                    "filter_searchText": "lung",
                    "attribute": ["Organ", "Name"],
                    "attribute_logic": "AND",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Search Text Only",
                value={
                    "filter_searchText": "CD8 Depletion",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Multiple search texts AND (no attributes, no sample types)",
                value={
                    "filter_searchText": ["lung", "granuloma"],
                    "searchText_logic": "AND",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Multiple search texts OR (no attributes)",
                value={
                    "filter_searchText": ["kidney", "Liver"],
                    "searchText_logic": "OR",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Multiple search texts OR with single attribute",
                value={
                    "filter_searchText": ["lung", "liver"],
                    "searchText_logic": "OR",
                    "attribute": "Organ",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Multiple search texts AND with multiple attributes (attribute OR)",
                value={
                    "filter_searchText": [
                        "granuloma",
                        "lung"
                    ],
                    "searchText_logic": "AND",
                    "attribute": [
                        "Organ",
                        "Type"
                    ],
                    "attribute_logic": "OR",
                    "filter_matchType": "PARTIAL"
                    }
            ),
        ],
    )
    def create(self, request):
        # Parse and validate request
        try:
            req = SampleAdvancedSearchRequest.model_validate(request.data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # No pre-resolution 404: unresolvable sample types are dropped silently in to_db_filters

        # Build resolver closure and DB filters
        try:
            def resolver(title: str) -> Optional[str]:
                return resolve_sampletype_to_seek_id(title)

            filters = req.to_db_filters(sampletype_resolver=resolver)
        except ValueError as ve:
            # Distinguish resolution failure (404) vs generic validation (422)
            if "SampleType not found" in str(ve):
                return HttpResponse(b'{"errors":[{"title":"SampleType not found"}]}', status=404, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # Execute DB search
        try:
            searchType = "Advanced"
            user_seek = getattr(request, "user", None)
            if DBtable_sample is None:
                raise RuntimeError("DBtable_sample unavailable")
            # Normalize multiple search texts into a PubMed-style boolean expression for upstream
            try:
                raw_st = req.filter_searchText
                if isinstance(raw_st, list):
                    terms = [str(t or '').strip() for t in raw_st if str(t or '').strip()]
                    if len(terms) >= 2:
                        st_logic = str(filters.get('searchText_logic') or 'OR').upper()
                        op = ' AND ' if st_logic == 'AND' else ' OR '
                        filters['filter_searchText'] = op.join(f'({t})' for t in terms)
                    elif len(terms) == 1:
                        # Single term: pass through unchanged (no parentheses)
                        filters['filter_searchText'] = terms[0]
                    else:
                        filters['filter_searchText'] = ''
                # If it's already a string, leave it as-is
            except Exception:
                # Best-effort normalization; fall back to existing value
                pass
            report = DBtable_sample().searchAdvanced(user_seek, filters, searchType)
        except Exception as e:
            log.warning("samples_advanced.exec_exception action=create error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        # Validate output schema
        try:
            data = json.loads(report or "{}")
            SampleAdvancedSearchResult.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_advanced.validation_error action=create errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_advanced.validation_exception action=create error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        # Debug flag to emit computed ids/counts in footer without breaking schema
        debug_meta_enabled = str(request.GET.get('debug_meta', '0')).lower() in ('1','true','yes')
        debug_meta = {}

        # Attribute list filtering (key presence AND value match), supports AND/OR across attributes and search terms
        if isinstance(data, dict) and isinstance(data.get('rows'), list):
            debug_meta['pre_attribute_rows'] = len(data.get('rows') or [])
            # Normalize attribute keys
            attr_list = filters.get('attribute_list', []) or []
            if isinstance(attr_list, list):
                attr_keys = [str(a).strip().lower() for a in attr_list if isinstance(a, str) and str(a).strip()]
            else:
                attr_keys = []
            attr_logic = (filters.get('attribute_logic') or ("OR" if len(attr_keys) > 1 else None))
            match_type = str(filters.get('filter_matchType', 'PARTIAL') or 'PARTIAL').upper()

            # Normalize search terms from the original request (not the combined string)
            try:
                raw_st_orig = req.filter_searchText
            except Exception:
                raw_st_orig = None
            if isinstance(raw_st_orig, list):
                search_terms = [str(x or '').strip().lower() for x in raw_st_orig if str(x or '').strip()]
            else:
                s = str(raw_st_orig or '').strip().lower()
                search_terms = [s] if s else []
            search_logic = str(filters.get('searchText_logic') or ('OR' if len(search_terms) > 1 else None) or 'OR').upper()

            debug_meta['attr_keys'] = attr_keys
            debug_meta['attr_logic'] = attr_logic
            debug_meta['filter_matchType'] = match_type
            debug_meta['searchText_logic'] = search_logic
            debug_meta['filter_searchText'] = search_terms if len(search_terms) > 1 else (search_terms[0] if search_terms else '')

            if attr_keys and search_terms:
                rows = data.get('rows', [])
                filtered_rows = []

                def match_one(val, term: str) -> bool:
                    try:
                        sval = '' if val is None else str(val)
                    except Exception:
                        sval = ''
                    sval_l = sval.lower()
                    if match_type == 'EXACT':
                        return sval_l.strip() == term
                    # PARTIAL or unknown defaults to substring
                    return term in sval_l

                def attrs_match_for_term(term: str, kv: dict) -> bool:
                    if not attr_keys:
                        return True
                    if attr_logic == 'AND':
                        for a in attr_keys:
                            if a not in kv or not match_one(kv[a], term):
                                return False
                        return True
                    elif attr_logic == 'OR':
                        for a in attr_keys:
                            if a in kv and match_one(kv[a], term):
                                return True
                        return False
                    else:
                        # Single attribute default
                        a = attr_keys[0]
                        return a in kv and match_one(kv[a], term)

                for r in rows:
                    try:
                        meta = r.get('json_metadata')
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = None
                        if not isinstance(meta, dict):
                            continue
                        # Build case-insensitive key->value map (first occurrence wins)
                        kv = {}
                        for k, v in meta.items():
                            try:
                                kl = str(k).strip().lower()
                            except Exception:
                                continue
                            if kl not in kv:
                                kv[kl] = v

                        if not search_terms:
                            ok = True
                        elif search_logic == 'AND':
                            ok = all(attrs_match_for_term(t, kv) for t in search_terms)
                        else:
                            ok = any(attrs_match_for_term(t, kv) for t in search_terms)

                        if ok:
                                filtered_rows.append(r)
                    except Exception:
                        continue

                data['rows'] = filtered_rows
                data['total'] = len(filtered_rows)
                debug_meta['post_attribute_rows'] = len(filtered_rows)

        # Enforce sample type filter client-side to compensate for upstream Advanced behavior
        # Sample type filtering (OR across list)
        st_ids = filters.get('sampletype_ids', []) or []
        if isinstance(st_ids, list):
            st_ids_norm = set()
            for x in st_ids:
                try:
                    st_ids_norm.add(int(str(x)))
                except Exception:
                    continue
        else:
            st_ids_norm = set()

        try:
            stid = int(str(filters.get('sampletype_id', 0)))
        except Exception:
            stid = 0

        debug_meta['sampletype_ids'] = sorted(list(st_ids_norm))
        debug_meta['sampletype_id'] = stid

        # Prefer explicit list when present; else fallback to singleton id
        if isinstance(data, dict) and isinstance(data.get('rows'), list):
            if st_ids_norm:
                rows = data.get('rows', [])
                debug_meta['pre_sampletype_rows'] = len(rows)
                filtered_rows = []
                for r in rows:
                    try:
                        r_sid = int(str(r.get('sample_type_id')))
                        if r_sid in st_ids_norm:
                            filtered_rows.append(r)
                    except Exception:
                        continue
                data['rows'] = filtered_rows
                data['total'] = len(filtered_rows)
                debug_meta['post_sampletype_rows'] = len(filtered_rows)
            elif stid > 0:
                rows = data.get('rows', [])
                debug_meta['pre_sampletype_rows'] = len(rows)
                filtered_rows = []
                for r in rows:
                    try:
                        r_sid = int(str(r.get('sample_type_id')))
                        if r_sid == stid:
                            filtered_rows.append(r)
                    except Exception:
                        continue
                data['rows'] = filtered_rows
                data['total'] = len(filtered_rows)
                debug_meta['post_sampletype_rows'] = len(filtered_rows)

        # Attach debug info in footer to avoid schema validation issues
        if debug_meta_enabled:
            try:
                if not isinstance(data.get('footer'), list):
                    data['footer'] = []
                data['footer'].append({'debug': debug_meta})
            except Exception:
                pass
            # Keep sampleTypes/noSampleTypes consistent with current rows (do not alter rows in debug mode)
            rows = data.get('rows', [])
            titles = []
            seen = set()
            for r in rows:
                t = r.get('sample_type')
                if t and t not in seen:
                    seen.add(t)
                    titles.append(t)
            if titles:
                data['sampleTypes'] = titles
                data['noSampleTypes'] = len(titles)
            else:
                # Ensure fields are coherent even if upstream provided them
                if 'sampleTypes' in data:
                    data['sampleTypes'] = []
                if 'noSampleTypes' in data:
                    data['noSampleTypes'] = 0

        # Paginate rows within the envelope while preserving schema
        try:
            data = paginate_rows_in_envelope(request, data)
        except Exception:
            # Fallback to unpaginated envelope on pagination error
            pass

        # Always return the mutated dict to ensure pagination is applied
        return HttpResponse(json.dumps(data).encode(), status=200, content_type='application/json')

