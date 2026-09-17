"""Superuser review surface for the reingest attribute approval queue.

Gated by ``IsSuperUser``, deliberately not an ``is_staff``-based check: the SEEK
login sets ``is_staff`` on every user (``dmac/views.py``), which would make an
``IsAdminUser``-style gate equivalent to ``IsAuthenticated``. See
``nextseek_api/permissions.py`` for the predicate.

``approve`` is the ONLY place in the system that turns a queued proposal into a
rule ``NessieAI.ns.reingest.proposals.approved_rules()`` will later hand to the
mapper. A ``needs_definition`` row means the target attribute did NOT exist on
its sample type when the row was queued; nothing stops that attribute still
being undefined at approval time, so ``approve`` re-checks
``proposals.attribute_exists`` right before writing the ruling. Reingest never
invents a sample attribute, and this check is what makes that true for
approved rows specifically, since the CI contract test for reingest map files
covers only what is committed to the repo, never database rows. See
``NessieAI/ns/reingest/proposals.py`` for the full policy this queue follows.

``attribute_exists`` deliberately raises (rather than returning ``False``) when
the sample-type catalog itself is unreachable, so that an infrastructure
outage is never mistaken for "this attribute genuinely does not exist". That
exception is let through here as a 503, not caught and turned into the 409
refusal a genuine gap gets -- conflating the two would tell a superuser the
attribute is undefined when the true story is that the database could not be
reached to check.
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.assistant.models_db import ReingestAttributeProposal
from nextseek_api.authentication import CsrfExemptSessionAuthentication
from nextseek_api.helpers import StandardResultsSetPagination
from nextseek_api.permissions import IsSuperUser
from NessieAI.ns.reingest.proposals import attribute_exists


def _error_response(title: str, status: int, detail: str | None = None) -> Response:
    """The house JSON:API-shaped error envelope -- see nextseek_api/models.py:JsonApiErrorResponse."""
    body: dict = {"errors": [{"title": title}]}
    if detail:
        body["errors"][0]["detail"] = detail
    return Response(body, status=status)


class ReingestAttributeProposalSerializer(serializers.ModelSerializer):
    # The list is not restricted to pending rows, so a superuser auditing the
    # queue sees terminal ones too -- and "approved" is useless without "by
    # whom". StringRelatedField renders the username rather than a user id:
    # the audit question is who ruled, not which primary key they are.
    proposed_by = serializers.StringRelatedField()
    reviewed_by = serializers.StringRelatedField()

    class Meta:
        model = ReingestAttributeProposal
        fields = [
            "id", "pipeline", "raw_key", "proposed_target", "proposed_attribute",
            "datatype", "example_value", "source_file", "rationale", "status",
            "times_proposed", "first_seen_run", "last_seen_run",
            "manifest_digest", "proposed_by", "reviewed_by", "reviewed_at",
            "created_at",
        ]
        read_only_fields = fields


class ReingestProposalViewSet(viewsets.ReadOnlyModelViewSet):
    """List the queue; approve or reject one row.

    Superuser-only and intentionally global: this queue is not project-scoped
    data, it is an operator worklist, the same posture as the other
    superuser-only admin surfaces (``docs/endpoint-authorization-register.md``).
    """

    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    # IsAuthenticated first so an anonymous caller gets 401 with a
    # WWW-Authenticate challenge rather than a bare 403 -- same reasoning as
    # nextseek_api/services/project_export.py.
    permission_classes = [IsAuthenticated, IsSuperUser]
    serializer_class = ReingestAttributeProposalSerializer
    pagination_class = StandardResultsSetPagination
    queryset = ReingestAttributeProposal.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        pipeline = self.request.query_params.get("pipeline")
        if pipeline:
            queryset = queryset.filter(pipeline=pipeline)
        return queryset

    def _rule(self, request, status, row):
        row.status = status
        row.reviewed_by = request.user
        row.reviewed_at = timezone.now()
        row.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        row = self.get_object()
        try:
            defined = attribute_exists(row.proposed_target, row.proposed_attribute)
        except RuntimeError as exc:
            # Outage, not a schema gap -- see the module docstring. Surfaced as
            # a server error (503) rather than the refusal below, so a
            # superuser is never told an attribute is undefined when the
            # truth is that the catalog could not be reached to check.
            return _error_response(
                "Sample type catalog unreachable", 503,
                detail=(
                    f"Could not verify whether {row.proposed_attribute!r} is "
                    f"defined on {row.proposed_target!r}: {exc}. Try again once "
                    "the catalog is reachable; this proposal's ruling was not "
                    "changed."
                ),
            )
        if not defined:
            return _error_response(
                "Attribute not defined", 409,
                detail=(
                    f"{row.proposed_attribute!r} is not defined on sample type "
                    f"{row.proposed_target!r}. Define the attribute first "
                    "(native Attribute API), then approve this proposal -- "
                    "reingest never invents a sample attribute."
                ),
            )
        return self._rule(request, ReingestAttributeProposal.STATUS_APPROVED, row)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        # No existence check: rejecting an attribute that does not exist is
        # always a legitimate ruling, and a human's ruling here is never
        # reopened by a later automated sighting (NessieAI/ns/reingest/proposals.py).
        row = self.get_object()
        return self._rule(request, ReingestAttributeProposal.STATUS_REJECTED, row)
