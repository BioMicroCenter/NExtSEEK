"""POST /nextseek_api/samples/graph_search/: sample search answered from the Neo4j sample graph.

A thin HTTP layer over ``nextseek_api/graph_search/`` (its README has the rules). The request is advanced_search's
body plus an optional ``extensions`` block; the response is advanced_search's envelope. In order, the view:

1. refuses an unauthenticated caller (401);
2. validates the body with ``GraphSearchRequest`` (422, advanced_search's error envelope);
3. refuses a search with nothing to search on (no term, no sample type that resolves, no ``extensions.where`` and no
   ``extensions.query``) with advanced_search's 422, before any lookup;
4. resolves the caller's scope from MySQL (403 when the caller maps to no SEEK person);
5. answers ``total: 0`` without touching the graph when a non-superuser belongs to no project;
6. runs the page and count statements through ``graph_search.service.search`` (READ transactions, 60 s timeout);
7. hydrates the page's rows from MySQL and validates the envelope with ``SampleAdvancedSearchResult``.

Unlike advanced_search, no SEEK password is needed (scope is read from MySQL), and paging happens in the database: a
page past the end returns empty ``rows`` with the real ``total``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from pydantic import ValidationError
from rest_framework import viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.permissions import IsAuthenticated

from nextseek_api.authentication import CsrfExemptSessionAuthentication
from nextseek_api.endpoint_descriptions import GRAPH_SEARCH_DESC
from nextseek_api.graph_search import hydrate as gs_hydrate
from nextseek_api.graph_search import service as gs_service
from nextseek_api.graph_search.query import GraphSearchInvalid, split_terms
from nextseek_api.graph_search.scope import ScopeUnavailable, resolve_scope
from nextseek_api.helpers import StandardResultsSetPagination
from nextseek_api.models import GraphSearchRequest, JsonApiErrorResponse, SampleAdvancedSearchResult

log = logging.getLogger(__name__)

NOTHING_TO_SEARCH = (
    "Give a filter_searchText, a sampletype that exists on this instance, extensions.where or extensions.query. "
    "A search with none of them would read every sample."
)

_TRUE = ("1", "true", "yes")

# One driver per process and configuration: a driver owns a connection pool, so opening one per request would pay
# for a new connection (and, with neo4j://, a routing table) on every search.
_DRIVERS: dict = {}
_DRIVERS_LOCK = threading.Lock()


def _neo4j():
    """The process's Neo4j driver and database name, from ``settings.NEO4J_DATABASE``."""
    config = settings.NEO4J_DATABASE
    uri, auth = config["URI"], tuple(config["AUTH"])
    with _DRIVERS_LOCK:
        driver = _DRIVERS.get((uri, auth))
        if driver is None:
            driver = GraphDatabase.driver(uri, auth=auth)
            _DRIVERS[(uri, auth)] = driver
    return driver, config.get("NAME") or "neo4j"


def _response(payload, status: int) -> HttpResponse:
    return HttpResponse(json.dumps(payload, default=str).encode(), status=status, content_type="application/json")


def _error(status: int, title: str, detail: Optional[str] = None) -> HttpResponse:
    error = {"title": title}
    if detail:
        error["detail"] = detail
    return _response({"errors": [error]}, status)


def _describe(exc: ValidationError) -> str:
    """What the model rejected, one clause per error: the field path and pydantic's message (never the input)."""
    parts = []
    for error in exc.errors(include_url=False):
        where = ".".join(str(p) for p in error.get("loc") or ()) or "body"
        parts.append(f"{where}: {error.get('msg')}")
    return "; ".join(parts)


def _page(params) -> Optional[int]:
    """The 1-based ``page``, or None when it is not a positive integer."""
    try:
        value = int(str(params.get("page", 1)).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _page_size(params) -> int:
    """``page_size`` as advanced_search's paginator reads it: default 100, invalid or non-positive falls back to the
    default, anything above 1,000 is capped (DRF ``PageNumberPagination.get_page_size`` with
    ``StandardResultsSetPagination``)."""
    paginator = StandardResultsSetPagination
    try:
        value = int(params.get(paginator.page_size_query_param))
    except (TypeError, ValueError):
        return paginator.page_size
    if value <= 0:
        return paginator.page_size
    return min(value, paginator.max_page_size)


def _is_timeout(exc: Neo4jError) -> bool:
    return "TransactionTimedOut" in (getattr(exc, "code", None) or "")


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


_EXAMPLE_ROW = {
    "id": 1525,
    "title": "TIS-230324BOO-39-PUB",
    "uuid": "TIS-230324BOO-39-PUB",
    "sample_type_id": 26,
    "sample_type": "TIS",
    "contributor_id": 145,
    "first_name": "Demo",
    "created_at": "2023-03-24 14:02:11",
    "json_metadata": {"UID": "TIS-230324BOO-39-PUB", "Organ": "Lung"},
    "assays": "Tissue RNA-seq",
    "attributeValue": "",
}


class GraphSearchViewSet(viewsets.ViewSet):
    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get_authenticate_header(self, request):
        """Advertise BasicAuthentication's challenge so an anonymous caller gets 401, not 403.

        DRF asks only ``authenticators[0]`` for a challenge, and SessionAuthentication has none, so DRF would coerce
        NotAuthenticated to 403, the code this endpoint uses for "no SEEK person". Session auth stays first, which
        is what decides who authenticates. Same override as AssayRegistrationViewSet.get_authenticate_header.
        """
        for authenticator in self.get_authenticators():
            header = authenticator.authenticate_header(request)
            if header:
                return header
        return None

    @extend_schema(
        operation_id="Graph Search Samples",
        description=GRAPH_SEARCH_DESC,
        parameters=[
            OpenApiParameter(name="page", type=int, location=OpenApiParameter.QUERY,
                             description="1-based page number (default 1); a page past the end returns empty rows"),
            OpenApiParameter(name="page_size", type=int, location=OpenApiParameter.QUERY,
                             description="Items per page (default 100, max 1000)"),
            OpenApiParameter(name="debug_meta", type=bool, location=OpenApiParameter.QUERY,
                             description="Append {\"debug\": {cypher_ms, count_ms, hydrate_ms, total_ms}} to footer"),
        ],
        request=GraphSearchRequest,
        responses={
            200: SampleAdvancedSearchResult,
            403: JsonApiErrorResponse,
            422: JsonApiErrorResponse,
            502: JsonApiErrorResponse,
            504: JsonApiErrorResponse,
        },
        tags=["Samples"],
        examples=[
            OpenApiExample(
                name="Sample type and a term (advanced_search's body)",
                description="Any advanced_search body works unchanged and matches the same samples.",
                value={
                    "sampletype": "TIS",
                    "filter_searchText": "lung",
                    "attribute": "Organ",
                    "filter_matchType": "PARTIAL",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Paired attribute conditions on one sample type",
                description=(
                    "where items are ANDed on one sample type, exact and case-sensitive. Values are cast by the "
                    "attribute's type, so a range sees only the values stored as numbers."
                ),
                value={
                    "filter_searchText": "",
                    "extensions": {
                        "where": [
                            {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"},
                            {"sample_type": "TIS", "attribute": "CellCount", "op": ">=", "value": 10000000},
                        ]
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                name="A lineage condition",
                description="TIS samples with a D.SEQ sample within 4 DERIVED_FROM hops below them.",
                value={
                    "sampletype": "TIS",
                    "filter_searchText": "",
                    "extensions": {"lineage": {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 4}},
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Associated with a sample type anywhere in the lineage tree",
                description=(
                    "The Sample Search page's Associated with: lung samples with a D.SEQ sample among their ancestors "
                    "or descendants, the whole tree (12 hops). A non-superuser's related sample, and every sample "
                    "between, must be in one of their projects."
                ),
                value={
                    "filter_searchText": "",
                    "extensions": {"query": "lung",
                                   "lineage": {"direction": "either", "sample_type": "D.SEQ", "max_hops": 12}},
                },
                request_only=True,
            ),
            OpenApiExample(
                name="The Sample Search page's query text",
                description=(
                    "extensions.query reads the Advanced box's text as advanced_search did: upper-case AND, OR and "
                    "NOT, parentheses to group, term[TYPE] limiting a term to a sample type. OR is never on one level "
                    "with AND or NOT; a text graph_search cannot read is a 422 that says why."
                ),
                value={
                    "filter_searchText": "",
                    "filter_matchType": "PARTIAL",
                    "extensions": {"query": "(lung[TIS] OR liver[TIS]) NOT granuloma"},
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Keywords across every sample type (OR)",
                value={
                    "filter_searchText": ["granuloma", "lung"],
                    "searchText_logic": "OR",
                    "filter_matchType": "PARTIAL",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="One page of results",
                value={
                    "total": 1,
                    "rows": [_EXAMPLE_ROW],
                    "footer": [],
                    "sampleTypes": ["TIS"],
                    "noSampleTypes": 1,
                    "msg": "okay",
                    "status": 1,
                },
                response_only=True,
            ),
        ],
    )
    def create(self, request):
        started = time.perf_counter()
        if not getattr(getattr(request, "user", None), "is_authenticated", False):
            return HttpResponse(b'{"detail":"Authentication required"}', status=401,
                                content_type="application/json")

        try:
            req = GraphSearchRequest.model_validate(request.data)
        except ValidationError as exc:
            return _error(422, "Invalid request", _describe(exc))
        except Exception:
            return _error(422, "Invalid request")

        params = getattr(request, "query_params", None) or request.GET
        page = _page(params)
        if page is None:
            return _error(422, "Invalid request", "page must be a positive integer")
        page_size = _page_size(params)
        debug = str(params.get("debug_meta", "0")).lower() in _TRUE

        try:
            filters = gs_service.db_filters(req)
        except Exception:
            return _error(422, "Invalid request")

        # Nothing to search on: advanced_search's refusal, before any lookup. extensions.where alone is enough,
        # because it names a sample type and the builder scans that type's label; so is extensions.query, the
        # Sample Search page's query text.
        has_where = bool(req.extensions is not None and req.extensions.where)
        has_query = bool(req.extensions is not None and (req.extensions.query or "").strip())
        if (not split_terms(req.filter_searchText) and not filters.get("sampletype_ids") and not has_where
                and not has_query):
            return _error(422, "Invalid request", NOTHING_TO_SEARCH)

        try:
            scope = resolve_scope(request.user)
        except ScopeUnavailable as exc:
            return _error(403, str(exc))
        except Exception:
            log.exception("graph_search: could not resolve the caller's project scope")
            return _error(502, "Invalid upstream response")

        timings = {"cypher_ms": 0.0, "count_ms": 0.0, "hydrate_ms": 0.0}
        if not scope.is_admin and not scope.project_ids:
            # A member of no project sees nothing; no statement is run.
            result, rows = {"total": 0, "ids": [], "sample_types": []}, []
        else:
            try:
                driver, db = _neo4j()
                result = gs_service.search(req, scope, page, page_size, driver=driver, db=db, filters=filters)
            except GraphSearchInvalid as exc:
                return _error(422, "Invalid request", str(exc))
            except Neo4jError as exc:
                if _is_timeout(exc):
                    log.warning("graph_search: a statement passed %s s", gs_service.TIMEOUT_SECONDS)
                    return _error(504, "Graph query timed out",
                                  f"The search took longer than {gs_service.TIMEOUT_SECONDS} seconds.")
                log.warning("graph_search: Neo4j refused the search: %s", getattr(exc, "code", None))
                return _error(502, "Invalid upstream response")
            except Exception:
                log.exception("graph_search: the graph search failed")
                return _error(502, "Invalid upstream response")
            timings.update(result["timings"])

            start = time.perf_counter()
            try:
                rows = gs_hydrate.hydrate(result["ids"])
            except Exception:
                log.exception("graph_search: hydrating the page from MySQL failed")
                return _error(502, "Invalid upstream response")
            timings["hydrate_ms"] = _ms(start)

        sample_types = list(result["sample_types"])
        data = {
            "total": int(result["total"]),
            "rows": rows,
            "footer": [],
            "sampleTypes": sample_types,
            "noSampleTypes": len(sample_types),
            "msg": "okay",
            "status": 1,
        }
        if debug:
            data["footer"].append({"debug": {**timings, "total_ms": _ms(started)}})

        try:
            SampleAdvancedSearchResult.model_validate(data)
        except ValidationError as exc:
            log.warning("graph_search: the envelope failed validation: %s", _describe(exc))
            return _error(502, "Invalid upstream response")
        return _response(data, 200)
