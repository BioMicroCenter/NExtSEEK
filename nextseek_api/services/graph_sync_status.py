"""GET /nextseek_api/admin/graph-sync/status/: what state the graph sync is in (the spec's section 13).

Native, read-only and superuser. It reads the two dmac tables and nothing else: no Neo4j connection, no SEEK call
and no MySQL outside ``graph_sync_outbox`` and ``graph_sync_run``, so it answers while the graph itself is down and
says so, which is the condition an operator most often needs it for.

Six parts, all from ``graph_sync/state.py``:

* ``runs``: the latest run of each kind, whatever its status (``state.last_runs``);
* ``freshness``: whether the weekly full sync, the nightly reconcile and the outbox are within their thresholds
  (``state.freshness``), each reported as ``ok``, ``stale`` or, before the first run, ``never``;
* ``outbox``: the open rows by kind, and the oldest one still waiting (``state.outbox_summary``);
* ``drift``: the result the latest drift run recorded, read from that run's own row rather than by a fresh check.
* ``failing``: the outbox rows that have failed since they last succeeded and that no worker is retrying, oldest
  failure first, at most ``state.FAILING_ROWS_SHOWN`` with counts over all of them (``state.failing_rows``);
* ``failed_runs``: the full, reconcile, catalog and drift kinds whose latest run failed (``state.failed_runs``).

Errors in those two parts are ``state.error_excerpt``: one line, URLs and addresses replaced, 240 characters at most.

``schema_version`` is the writer's, so a caller can tell a graph the current writer built from one an older version
left. Comparing it with the last full sync's own recorded version is what the smoke suite's parity check keys on
(``ci/smoke/test_graph_sync_status.py``).

A database error becomes 503 in the JSON:API envelope: production runs a v1.0 graph without migration 0021, so the
tables are genuinely absent there and a 500 would read as a defect in this endpoint.
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.db import DatabaseError
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.authentication import CsrfExemptSessionAuthentication
from nextseek_api.endpoint_descriptions import GRAPH_SYNC_STATUS_DESC
from nextseek_api.graph_sync import state, writer
from nextseek_api.models import GraphSyncStatusResponse, JsonApiErrorResponse
from nextseek_api.services.users import IsDjangoSuperuser
from nextseek_graph import schema

log = logging.getLogger(__name__)

UNAVAILABLE_TITLE = "Graph sync state unavailable"
# Fixed prose, never the driver's message: that names schemas, hosts and statements, and this body is written to
# operator terminals and CI logs.
UNAVAILABLE_DETAIL = (
    "The graph_sync_outbox and graph_sync_run tables could not be read. An instance that has not applied migration "
    "0021 does not have them yet."
)


def build_status(*, now: datetime | None = None) -> dict:
    """The status body. Reads the two dmac tables and raises ``DatabaseError`` when it cannot.

    One clock for every part, so an age in the outbox and an age in the freshness block are measured from the same
    instant rather than from two moments a query apart.
    """
    now = now or timezone.now()
    runs = state.last_runs()
    payload = {
        "generated_at": now.isoformat(),
        "schema_version": writer.SCHEMA_VERSION,
        "runs": runs,
        "freshness": state.freshness(now=now),
        "outbox": state.outbox_summary(now=now),
        # The drift run's own recorded result. Running a check here would make a read-only status endpoint open a
        # Neo4j session on every call.
        "drift": (runs.get("drift") or {}).get("drift"),
        # Rows failing past their retry and the latest run of a kind that failed: what the CI health checks fail on
        # (nextseek_api/graph_sync/health.py). failing_since, not enqueued_at, ages a row: a re-enqueue moves the latter.
        "failing": state.failing_rows(now=now),
        "failed_runs": state.failed_runs(runs, now=now),
    }
    return GraphSyncStatusResponse.model_validate(payload).model_dump()


class GraphSyncStatusViewSet(viewsets.ViewSet):
    """Read-only, superuser-only view of the graph sync's own state.

    No list route: the ViewSet defines no ``list`` method, so the router publishes only the ``status`` action and
    the API root does not advertise this prefix.
    """

    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    # IsAuthenticated first, so an anonymous caller gets 401 rather than 403. IsDjangoSuperuser, never DRF's
    # IsAdminUser: dmac/views.py sets is_staff on every SEEK user at login, so an is_staff gate enforces nothing.
    permission_classes = [IsAuthenticated, IsDjangoSuperuser]

    def get_authenticate_header(self, request):
        """Advertise a challenge so an unauthenticated request is 401, not 403.

        DRF asks only ``authenticators[0]``, and ``CsrfExemptSessionAuthentication`` has no challenge to give.
        Session auth stays first, which is what decides who authenticates. Same override as
        ``GraphSearchViewSet.get_authenticate_header``.
        """
        for authenticator in self.get_authenticators():
            header = authenticator.authenticate_header(request)
            if header:
                return header
        return None

    @extend_schema(
        operation_id="Admin: Graph Sync Status",
        description=GRAPH_SYNC_STATUS_DESC,
        responses={
            200: GraphSyncStatusResponse,
            503: JsonApiErrorResponse,
        },
        tags=["admin"],
        examples=[
            OpenApiExample(
                name="A synced instance with one sample waiting",
                value={
                    "generated_at": "2026-09-15T02:30:00+00:00",
                    "schema_version": schema.SCHEMA_VERSION,
                    "runs": {
                        "full": {
                            "id": 412,
                            "kind": "full",
                            "status": "ok",
                            "started_at": "2026-09-13T03:00:00+00:00",
                            "finished_at": "2026-09-13T03:41:22+00:00",
                            "watermark_from": None,
                            "watermark_to": "1308453",
                            "counts": {"trigger": "loop", "schema_version": schema.SCHEMA_VERSION, "samples": 1084754},
                            "drift": None,
                        },
                    },
                    "freshness": {
                        "full": {
                            "status": "ok",
                            "satisfied_by": "full",
                            "last_ok_started_at": "2026-09-13T03:00:00+00:00",
                            "last_ok_finished_at": "2026-09-13T03:41:22+00:00",
                            "age_s": 170_400.0,
                            "threshold_s": 691_200,
                        },
                        "reconcile": {
                            "status": "ok",
                            "satisfied_by": "reconcile",
                            "last_ok_started_at": "2026-09-15T02:00:00+00:00",
                            "last_ok_finished_at": "2026-09-15T02:04:10+00:00",
                            "age_s": 1800.0,
                            "threshold_s": 93_600,
                        },
                        "outbox": {
                            "status": "ok",
                            "oldest_enqueued_at": "2026-09-15T02:29:31+00:00",
                            "age_s": 29.0,
                            "threshold_s": 3600,
                        },
                    },
                    "outbox": {
                        "pending": {"samples": 1},
                        "dead": {},
                        "claimed": {},
                        "oldest_pending": {
                            "kind": "samples",
                            "key": "sample:1525",
                            "enqueued_at": "2026-09-15T02:29:31+00:00",
                            "age_s": 29.0,
                        },
                        "max_attempts": 8,
                    },
                    "drift": None,
                    "failing": {"rows": [], "total": 0, "overdue": 0, "limit": 20},
                    "failed_runs": [],
                },
                response_only=True,
            ),
            OpenApiExample(
                name="A drain failing on a lost database connection",
                value={
                    "generated_at": "2026-09-29T19:00:00+00:00",
                    "schema_version": writer.SCHEMA_VERSION,
                    "runs": {
                        "catalog": {
                            "id": 7,
                            "kind": "catalog",
                            "status": "failed",
                            "started_at": "2026-09-29T17:00:00+00:00",
                            "finished_at": "2026-09-29T17:00:01+00:00",
                            "watermark_from": None,
                            "watermark_to": None,
                            "counts": {"trigger": "loop", "status": "failed",
                                       "error": "OperationalError: (2006, 'Server has gone away')"},
                            "drift": None,
                        },
                    },
                    "freshness": {
                        "full": {
                            "status": "ok",
                            "satisfied_by": "full",
                            "last_ok_started_at": "2026-09-27T03:00:00+00:00",
                            "last_ok_finished_at": "2026-09-27T03:10:00+00:00",
                            "age_s": 230_400.0,
                            "threshold_s": 691_200,
                        },
                        "reconcile": {
                            "status": "ok",
                            "satisfied_by": "reconcile",
                            "last_ok_started_at": "2026-09-29T02:00:00+00:00",
                            "last_ok_finished_at": "2026-09-29T02:01:00+00:00",
                            "age_s": 61_200.0,
                            "threshold_s": 93_600,
                        },
                        "outbox": {
                            "status": "stale",
                            "oldest_enqueued_at": "2026-09-29T16:50:00+00:00",
                            "age_s": 7800.0,
                            "threshold_s": 3600,
                        },
                    },
                    "outbox": {
                        "pending": {"catalog": 1, "samples_of_type": 1},
                        "dead": {},
                        "claimed": {},
                        "oldest_pending": {
                            "kind": "samples_of_type",
                            "key": "type:3",
                            "enqueued_at": "2026-09-29T16:50:00+00:00",
                            "age_s": 7800.0,
                        },
                        "max_attempts": 8,
                    },
                    "drift": None,
                    "failing": {
                        "rows": [
                            {
                                "kind": "catalog",
                                "key": "*",
                                "attempts": 0,
                                "dead": False,
                                "failing_since": "2026-09-29T17:00:01+00:00",
                                "age_s": 7199.0,
                                "threshold_s": 5400,
                                "overdue": True,
                                "next_retry_at": "2026-09-29T19:00:02+00:00",
                                "error": "OperationalError: (2006, 'Server has gone away')",
                            },
                            {
                                "kind": "samples_of_type",
                                "key": "type:3",
                                "attempts": 1,
                                "dead": False,
                                "failing_since": "2026-09-29T17:05:00+00:00",
                                "age_s": 6900.0,
                                "threshold_s": 5400,
                                "overdue": True,
                                "next_retry_at": "2026-09-29T19:05:00+00:00",
                                "error": "OperationalError: (2006, 'Server has gone away')",
                            },
                        ],
                        "total": 2,
                        "overdue": 2,
                        "limit": 20,
                    },
                    "failed_runs": [
                        {
                            "id": 7,
                            "kind": "catalog",
                            "status": "failed",
                            "trigger": "loop",
                            "finished_at": "2026-09-29T17:00:01+00:00",
                            "age_s": 7199.0,
                            "threshold_s": 5400,
                            "overdue": True,
                            "error": "OperationalError: (2006, 'Server has gone away')",
                        },
                    ],
                },
                response_only=True,
            ),
            OpenApiExample(
                name="An instance without migration 0021",
                value={"errors": [{"title": UNAVAILABLE_TITLE, "detail": UNAVAILABLE_DETAIL}]},
                response_only=True,
                status_codes=["503"],
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="status")
    def status(self, request):
        try:
            return Response(build_status(), status=http_status.HTTP_200_OK)
        except DatabaseError as exc:
            log.warning("graph sync status: the state tables could not be read: %s", exc)
            return Response(
                {"errors": [{"title": UNAVAILABLE_TITLE, "detail": UNAVAILABLE_DETAIL}]},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )
