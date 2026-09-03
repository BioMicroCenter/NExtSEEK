"""The Download Templates tool as an API.

The picker page at /seek/templates has served this since it was rebuilt; this
module is its headless twin. Both read `template_catalog.build_catalog()`, so
neither can drift from the other.

Native, not a SEEK proxy: everything comes from MySQL through
`template_catalog.py` and `sample_workbook.py`. Neo4j is never on the request
path -- the derived rules are read from the materialised
`dmac.sample_type_requirements` table, which is allowed to be empty.
"""

from __future__ import annotations

from dataclasses import asdict

from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.endpoint_descriptions import (
    TEMPLATE_CATALOG_DESC,
    TEMPLATE_GENERATE_DESC,
)
from nextseek_api.models import TemplateCatalogResponse, TemplateGenerateRequest
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
from nextseek_api.services.sample_workbook import render_template_workbook
from nextseek_api.services.template_catalog import build_catalog, select_entries

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class TemplatesViewSet(viewsets.ViewSet):
    """Blank upload templates: what can be generated, and generating it."""

    # BasicAuthentication FIRST, deliberately. DRF answers an unauthenticated
    # request with 401 only when the first authenticator returns a
    # WWW-Authenticate challenge from authenticate_header(); SessionAuthentication
    # returns None there, which turns the same request into a bare 403. Basic
    # first yields the 401 an API caller expects, and costs session auth nothing:
    # BasicAuthentication returns None when there is no Basic header, so the
    # session authenticator still runs.
    #
    # No TokenAuthentication: token auth does not work in this project.
    authentication_classes = [BasicAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        """Read is open to any authenticated user; generating is superuser-only.

        Reading matches seek.views.assets.templatesList, which requires login but
        deliberately not project membership: these are schema definitions, not
        sample data, so there is nothing project-scoped to expose.

        IsSuperUser, never DRF's IsAdminUser -- dmac/views.py sets is_staff=1 on
        every SEEK user at login, so IsAdminUser is IsAuthenticated under a
        misleading name.
        """
        if self.action == "generate":
            return [IsAuthenticated(), IsSuperUser()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="Templates: Catalog (GET)",
        description=TEMPLATE_CATALOG_DESC,
        tags=["templates"],
        responses={200: TemplateCatalogResponse},
        examples=[
            OpenApiExample(
                "Catalog",
                value={
                    "groups": [
                        {
                            "key": "",
                            "label": "Experimental types",
                            "entries": [
                                {"code": "DNA", "sample_type_id": 42, "name": "DNA",
                                 "description": "Extracted DNA.", "group": ""},
                                {"code": "TIS", "sample_type_id": 2, "name": "Tissue",
                                 "description": "A tissue sample.", "group": ""},
                            ],
                        },
                        {
                            "key": "D.",
                            "label": "Data types",
                            "entries": [
                                {"code": "D.SEQ", "sample_type_id": 11,
                                 "name": "Sequencing Data", "description": "Reads.",
                                 "group": "D."},
                            ],
                        },
                    ],
                    "children": {"TIS": ["DNA"]},
                    "requires": {
                        "D.SEQ": {"add": ["DNA"],
                                  "assays": ["Whole Genome Sequencing"]}
                    },
                    "companions": {"NHP": {"add": ["PAV"], "assays": []}},
                    "max_suggestions": 12,
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="catalog")
    def catalog(self, request):
        payload = build_catalog()
        response = TemplateCatalogResponse(
            groups=[
                {
                    "key": group["key"],
                    "label": group["label"],
                    # build_catalog hands back SampleTypeEntry dataclasses so the
                    # page template and the workbook writer can read attributes
                    # off them; only the API needs them flattened.
                    "entries": [asdict(entry) for entry in group["entries"]],
                }
                for group in payload["groups"]
            ],
            children=payload["children"],
            requires=payload["requires"],
            companions=payload["companions"],
            max_suggestions=payload["max_suggestions"],
        )
        return Response(response.model_dump(mode="json"), status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="Templates: Generate (POST)",
        description=TEMPLATE_GENERATE_DESC,
        tags=["templates"],
        request=TemplateGenerateRequest,
        responses={
            (200, XLSX_CONTENT_TYPE): OpenApiResponse(
                response=OpenApiTypes.BINARY,
                description="Blank template workbook: a README, one headers-only "
                            "sheet per requested type in request order, a "
                            "controlled-vocabulary sheet, and a hidden _NEXTSEEK "
                            "manifest.",
            ),
        },
        examples=[
            OpenApiExample(
                "Three experimental types",
                value={"codes": ["PAT", "TIS", "DNA"]},
                request_only=True,
            ),
            OpenApiExample(
                "One data type",
                value={"codes": ["D.SEQ"]},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        try:
            payload = TemplateGenerateRequest.model_validate(request.data or {})
        except ValidationError as exc:
            return Response(
                {"errors": [{"title": "Invalid request", "detail": exc.errors()}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        chosen, unknown = select_entries(payload.codes)
        if unknown:
            # seek.views.assets.templatesDownload drops these silently, so that a
            # stale bookmark still produces the types it names. An API caller gets
            # told instead: a workbook quietly missing a sheet is worse than a 422.
            # That is the ONLY thing the two callers still decide separately;
            # select_entries owns the rest so they cannot drift.
            return Response(
                {"errors": [{
                    "title": "Unknown sample type code",
                    "detail": "not in the catalog: " + ", ".join(unknown),
                }]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        buffer, filename = render_template_workbook(chosen)
        return FileResponse(
            buffer,
            content_type=XLSX_CONTENT_TYPE,
            as_attachment=True,
            filename=filename,
        )
