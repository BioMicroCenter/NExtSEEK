"""POST /nextseek_api/sample-shares/, GET .../{share_id}/ and POST .../{share_id}/apply/: the studies tool's share mode.

Native and superuser only, intentionally global (a superuser shares between any two projects; the projects and the
study come from the body). Create stores a job row the share worker plans (``manage.py run_share_jobs``); read
answers from that row, with ``?verify=graph`` a read-only check of the shared samples in the graph; apply is one step
of ``studies.share_apply.apply_step`` with the caller's own SEEK credential, proved as the SEEK person bound to the
caller and never stored. The job discipline is ``nextseek_api/assay_registration/views.py``'s, the code is its own
(tool spec 16.5).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from pydantic import ValidationError
from rest_framework import viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.authentication import CsrfExemptSessionAuthentication
from nextseek_api.endpoint_descriptions import (SAMPLE_SHARE_APPLY_DESC, SAMPLE_SHARE_CREATE_DESC,
                                                SAMPLE_SHARE_DETAIL_DESC)
from nextseek_api.graph_sync import state as outbox_state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.models import (JsonApiErrorResponse, SampleShareAccepted, SampleShareApplyRequest,
                                 SampleShareRequest, SampleShareStatus, SampleShareStep)
from nextseek_api.services.users import IsDjangoSuperuser
from nextseek_api.studies import links, share_apply, share_jobs
from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.models_db import SampleShare
from nextseek_api.studies.seek import SeekCredential, SeekError, SeekRefused, SeekSession

log = logging.getLogger(__name__)

#: A missing row, or a malformed id Django's UUIDField refuses (assay_registration/views.py explains the pair).
_LOOKUP_FAILURES = (ObjectDoesNotExist, DjangoValidationError)

REQUEST_EXAMPLE = {"sample_uids": ["TIS-230324BOO-39-PUB", "TIS-230324BOO-40-PUB"], "source_project_id": 1,
                   "destination_project_id": 2558, "destination_study_id": 746}
SHARE_ID_EXAMPLE = "3f1c2a9e-8d4b-4c71-9a0e-5b6f7d8c9e01"


def _error(code: str, detail: str, status: int) -> Response:
    return Response({"errors": [{"title": code, "detail": detail}]}, status=status)


def _status_url(share_id) -> str:
    return reverse("nextseek_api:sample-shares-detail", kwargs={"share_id": str(share_id)})


class GraphUnavailable(Exception):
    """No Neo4j is configured for this app."""


def _graph_errors() -> tuple:
    from neo4j.exceptions import DriverError, Neo4jError

    return GraphUnavailable, DriverError, Neo4jError


@contextmanager
def _graph():
    """The configured Neo4j, for the apply step's preflight and the read-only check; closed at once."""
    from neo4j import GraphDatabase

    config = getattr(settings, "NEO4J_DATABASE", None) or {}
    if not config.get("URI"):
        raise GraphUnavailable("NEO4J_DATABASE names no URI")
    with GraphDatabase.driver(config["URI"], auth=config.get("AUTH")) as driver:
        yield driver, config.get("NAME") or "neo4j"


#: worst last: a row never written (an after-commit enqueue that failed) never drains, so it reads worse than pending
OUTBOX_STATES = ("done", "pending", "missing", "failed", "dead")


def _outbox_state(plan) -> str:
    """The worst state over every ``samples`` outbox row of the share's unit (one per ``SAMPLE_CHUNK`` ids, the first
    under the unit's key): done, pending, missing, failed or dead; missing when the unit has none yet."""
    keys = [k for u in plan.units for k, _part in links.outbox_rows(links.unit_key(plan.run_id, u.unit), u.sync_ids)]
    found = {r.key: r for r in GraphSyncOutbox.objects.filter(kind="samples", key__in=keys)}
    states = []
    for key in keys:
        row = found.get(key)
        if row is None:
            states.append("missing")
        elif row.done_at is not None:
            states.append("done")
        elif row.attempts >= outbox_state.MAX_ATTEMPTS:
            states.append("dead")
        else:
            states.append("failed" if row.failing_since else "pending")
    return max(states, key=OUTBOX_STATES.index) if states else "missing"


def _status(row, graph=None) -> dict:
    req = row.request or {}
    return SampleShareStatus(
        share_id=str(row.share_id), state=row.state, created_at=row.created_at.isoformat(),
        actor_login=row.actor_login, source_project_id=req.get("source_project_id", 0),
        destination_project_id=req.get("destination_project_id", 0),
        destination_study_id=req.get("destination_study_id", 0), uid_count=len(req.get("sample_uids") or []),
        run_dir=row.run_dir, plan_sha256=row.plan_sha256 or None, summary=row.summary, receipt=row.receipt,
        error=row.error, graph=graph).model_dump()


def _graph_check(row) -> dict:
    """``?verify=graph``: the share's sample and parent ids (its unit's sync ids) read in the graph, and the worst
    state of its unit's outbox rows."""
    from nextseek_api.graph_sync import writer
    from nextseek_api.studies.models import StudyMovePlan
    from nextseek_api.studies.report import PLAN_FILE

    plan = StudyMovePlan.from_file(share_apply.run_dir_of(row) / PLAN_FILE)
    ids = sorted({s for u in plan.units for s in u.sync_ids})
    with _graph() as (driver, db):
        found = writer.share_graph_check(driver, db, ids, project_id=plan.share.destination_project_id,
                                         study_id=plan.share.destination_study_id)
    return {**found, "outbox": _outbox_state(plan)}


class SampleShareViewSet(viewsets.ViewSet):
    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsDjangoSuperuser]
    lookup_field = "share_id"

    def get_authenticate_header(self, request):
        """Basic's challenge, so an anonymous caller gets 401, not 403: copied from
        ``nextseek_api/assay_registration/views.py`` ``AssayRegistrationViewSet.get_authenticate_header``, which says
        why."""
        for authenticator in self.get_authenticators():
            header = authenticator.authenticate_header(request)
            if header:
                return header
        return None

    def _row(self, share_id):
        return SampleShare.objects.get(share_id=share_id)

    @extend_schema(
        operation_id="Create Sample Share",
        description=SAMPLE_SHARE_CREATE_DESC,
        request=SampleShareRequest,
        responses={202: SampleShareAccepted, 401: JsonApiErrorResponse, 403: JsonApiErrorResponse,
                   422: JsonApiErrorResponse},
        tags=["SampleShares"],
        examples=[
            OpenApiExample("Share two samples", value=REQUEST_EXAMPLE, request_only=True),
            OpenApiExample("Accepted", value={"share_id": SHARE_ID_EXAMPLE, "state": "planning",
                                              "status_url": f"/nextseek_api/sample-shares/{SHARE_ID_EXAMPLE}/"},
                           response_only=True, status_codes=["202"]),
        ],
    )
    def create(self, request):
        try:
            body = SampleShareRequest.model_validate(request.data)
        except ValidationError as exc:
            return _error("invalid_request", str(exc)[:1000], 422)
        inp = ShareInput(**body.model_dump(), created_at=timezone.now().isoformat())
        row = share_jobs.create_share(inp, request.user)
        return Response(SampleShareAccepted(share_id=str(row.share_id), state=row.state,
                                            status_url=_status_url(row.share_id)).model_dump(), status=202)

    @extend_schema(
        operation_id="Get Sample Share",
        description=SAMPLE_SHARE_DETAIL_DESC,
        parameters=[OpenApiParameter("verify", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                                     enum=["graph"])],
        responses={200: SampleShareStatus, 401: JsonApiErrorResponse, 403: JsonApiErrorResponse,
                   404: JsonApiErrorResponse, 503: JsonApiErrorResponse},
        tags=["SampleShares"],
        examples=[OpenApiExample("A planned share", value={
            "share_id": SHARE_ID_EXAMPLE, "state": "planned", "created_at": "2026-01-01T00:00:00+00:00",
            "actor_login": "admin", "source_project_id": 1, "destination_project_id": 2558,
            "destination_study_id": 746, "uid_count": 2, "run_dir": "20260101T000000Z-share",
            "plan_sha256": "0" * 64, "summary": {"outcomes": {"shared": 2}}, "receipt": None, "error": None,
            "graph": None}, response_only=True)],
    )
    def retrieve(self, request, share_id=None):
        try:
            row = self._row(share_id)
        except _LOOKUP_FAILURES:
            return _error("not_found", "no share has this id", 404)
        graph = None
        if request.query_params.get("verify") == "graph" and row.run_dir:
            try:
                graph = _graph_check(row)
            except (OSError, ValueError, *_graph_errors()) as exc:
                log.warning("sample share %s: the graph check could not run: %s", row.share_id, type(exc).__name__)
                return _error("graph_unavailable", f"the graph check could not run ({type(exc).__name__}): the graph "
                                                   "or the share's run directory cannot be read", 503)
        return Response(_status(row, graph), status=200)

    @extend_schema(
        operation_id="Apply Sample Share",
        description=SAMPLE_SHARE_APPLY_DESC,
        request=SampleShareApplyRequest,
        responses={200: SampleShareStep, 202: SampleShareStep, 401: JsonApiErrorResponse, 403: JsonApiErrorResponse,
                   404: JsonApiErrorResponse, 409: JsonApiErrorResponse, 422: JsonApiErrorResponse,
                   502: JsonApiErrorResponse, 503: JsonApiErrorResponse},
        tags=["SampleShares"],
        examples=[
            OpenApiExample("Apply", value={"plan_sha256": "0" * 64}, request_only=True),
            OpenApiExample("One destination assay made", value={
                "share_id": SHARE_ID_EXAMPLE, "state": "applying", "clones_done": 1, "clones_remaining": 1,
                "retry_after_s": None, "status_url": f"/nextseek_api/sample-shares/{SHARE_ID_EXAMPLE}/"},
                response_only=True, status_codes=["200"]),
        ],
    )
    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, share_id=None):
        try:
            row = self._row(share_id)
        except _LOOKUP_FAILURES:
            return _error("not_found", "no share has this id", 404)
        try:
            body = SampleShareApplyRequest.model_validate(request.data)
        except ValidationError as exc:
            return _error("invalid_request", str(exc)[:1000], 422)
        credential = SeekCredential.from_request(request)
        if credential is None:
            return _error("seek_credential_missing", "apply needs your own SEEK login (Basic or the session)", 401)
        try:
            session = SeekSession(credential, write_timeout_s=share_apply.SHARE_WRITE_TIMEOUT_S).prove_for(
                request.user)
        except SeekRefused as exc:
            code = exc.code if exc.code == "seek_identity_mismatch" else "seek_refused"
            return _error(code, exc.message, 403)
        except SeekError as exc:
            return _error("seek_error", exc.message, 502)
        try:
            with _graph() as (driver, db):   # the graph is read only by the preflight, before any write
                answer = share_apply.apply_step(row, session, driver, db, plan_sha256=body.plan_sha256)
        except _graph_errors() as exc:
            log.warning("sample share %s: the graph could not be read: %s", row.share_id, type(exc).__name__)
            return _error("graph_unavailable", f"the graph could not be read ({type(exc).__name__}): call again "
                                               "later; nothing was written", 503)
        if answer.status_code >= 400:
            return _error(answer.code or "error", answer.message or answer.code or "", answer.status_code)
        return Response(SampleShareStep(share_id=str(row.share_id), state=answer.state, clones_done=answer.clones_done,
                                        clones_remaining=answer.clones_remaining, retry_after_s=answer.retry_after_s,
                                        code=answer.code, message=answer.message,
                                        status_url=_status_url(row.share_id)).model_dump(),
                        status=answer.status_code)
