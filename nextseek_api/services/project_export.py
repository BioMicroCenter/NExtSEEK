"""Superuser-only bulk export of every sample in a SEEK project.

Port of the standalone ``pull_all_db.py`` script (NExtSEEK-DEV/pull_all_db).
Three parts of that script are deliberately NOT carried over:

* the SSH tunnel to fairdata.mit.edu — the container reaches ``seek_production``
  directly through ``connections["seek"]``;
* the ``.env`` / ``load_dotenv`` credential path — credentials already live in
  ``dmac.settings.DATABASES``, and a second source could silently point
  somewhere else (both schemas are named ``seek_production``);
* the four-stage filesystem round-trip (TSV -> raw xlsx -> parsed xlsx ->
  cleaned xlsx), which accounted for most of the script's runtime. The workbook
  is built in memory and streamed.

Two behavioural fixes relative to the script:

* sample type comes from the ``sample_types`` FK, not a regex over the uuid
  prefix. The script's ``str.startswith`` match made the ``AB`` sheet absorb
  every ``ABP-*`` row; ``AB`` and ``ABP`` are distinct rows in ``sample_types``.
* records are keyed by ``samples.id`` as well as ``uuid``. ``samples.uuid``
  carries a non-unique index and real collisions exist.
"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Any

from django.conf import settings
from django.db import connections
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, TokenAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.assistant.excel_export import generate_table_xlsx
from nextseek_api.models import ProjectExportRequest, ProjectExportResponse
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication, _error_response

logger = logging.getLogger(__name__)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Sample type used when ``samples.sample_type_id`` does not resolve.
UNKNOWN_SAMPLE_TYPE = "UNKNOWN"

#: Columns always present, always kept, and always leading. ``uuid`` is not
#: unique, so ``id`` is the only true key.
KEY_COLUMNS = ("id", "uuid")

#: Excel forbids these in a sheet name, and caps the name at 31 characters.
_ILLEGAL_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


# ---------------------------------------------------------------------------
# Extraction (pure — no HTTP, no filesystem)
# ---------------------------------------------------------------------------

def _rows(sql: str, params: list | None = None) -> list[dict]:
    """Run a parameterized query against the SEEK schema, return dict rows."""
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def resolve_project(project_id: int) -> dict | None:
    """Return ``{id, title}`` for a SEEK project, or None if it does not exist."""
    found = _rows("SELECT id, title FROM projects WHERE id = %s", [project_id])
    return found[0] if found else None


def pull_project_samples(project_id: int) -> list[dict]:
    """Every sample in a project, with its sample type resolved through the FK.

    Ported from pull_all_db.py's ``build_query``, with the ``sample_types`` join
    added and the schema left unqualified — ``connections["seek"]`` is already
    connected to the SEEK database, so no schema name is interpolated into SQL.
    """
    return _rows(
        """
        SELECT s.id, s.uuid, st.title AS sample_type, s.json_metadata
        FROM projects_samples ps
        JOIN samples s ON ps.sample_id = s.id
        LEFT JOIN sample_types st ON st.id = s.sample_type_id
        WHERE ps.project_id = %s
        ORDER BY st.title, s.id
        """,
        [project_id],
    )


def _parse_metadata(raw: Any) -> dict | None:
    """Parse a ``json_metadata`` blob, or None if it is not a usable object."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _sheet_labels(sample_types: list[str]) -> dict[str, str]:
    """Map sample types to unique, Excel-legal sheet names (<= 31 chars)."""
    labels: dict[str, str] = {}
    used: set[str] = set()
    for sample_type in sample_types:
        base = _ILLEGAL_SHEET_CHARS.sub("_", sample_type)[:31] or UNKNOWN_SAMPLE_TYPE
        label, suffix = base, 2
        while label in used:
            # Truncate the base, not the disambiguator, to stay within 31 chars.
            tail = f"_{suffix}"
            label = f"{base[:31 - len(tail)]}{tail}"
            suffix += 1
        used.add(label)
        labels[sample_type] = label
    return labels


def build_sheet_tables(rows: list[dict]) -> tuple[list[dict], int]:
    """Group sample rows by sample type into ``generate_table_xlsx`` tables.

    Returns ``(tables, unparseable_count)``. Each table is
    ``{"label", "sample_type", "columns", "data"}``; columns are in first-seen
    order after the key columns, and columns that are empty for every row in
    the group are dropped (the script's ``clean_excel_sheets`` stage).
    """
    groups: dict[str, list[dict]] = {}
    unparseable = 0

    for row in rows:
        metadata = _parse_metadata(row.get("json_metadata"))
        if metadata is None:
            unparseable += 1
            metadata = {}

        record = {"id": row["id"], "uuid": row["uuid"]}
        for key, value in metadata.items():
            # Never let a metadata key shadow the keys we control.
            record[f"metadata_{key}" if key in KEY_COLUMNS else key] = value

        groups.setdefault(row.get("sample_type") or UNKNOWN_SAMPLE_TYPE, []).append(record)

    labels = _sheet_labels(sorted(groups))
    tables = []
    for sample_type in sorted(groups):
        records = groups[sample_type]

        columns = list(KEY_COLUMNS)
        seen = set(KEY_COLUMNS)
        for record in records:
            for key in record:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)

        kept = [
            column for column in columns
            if column in KEY_COLUMNS
            or any(not _is_empty(record.get(column)) for record in records)
        ]
        tables.append({
            "label": labels[sample_type],
            "sample_type": sample_type,
            "columns": kept,
            "data": records,
        })
    return tables, unparseable


def _source() -> dict[str, str]:
    """Which database answered — echoed so a caller can tell dev from prod."""
    db = settings.DATABASES[settings.SEEK_DATABASE]
    return {"db_host": str(db.get("HOST") or ""), "db_name": str(db.get("NAME") or "")}


def export_project(project_id: int, output_format: str):
    """Shared body of the GET and POST routes. Returns a DRF/Django response."""
    project = resolve_project(project_id)
    if project is None:
        source = _source()
        return _error_response(
            "Not found",
            f"No SEEK project with id={project_id} in "
            f"{source['db_name']}@{source['db_host']}.",
            status.HTTP_404_NOT_FOUND,
        )

    rows = pull_project_samples(project_id)
    tables, unparseable = build_sheet_tables(rows)
    logger.info(
        "project_export project_id=%s format=%s samples=%s types=%s unparseable=%s",
        project_id, output_format, len(rows), len(tables), unparseable,
    )

    if output_format == "json":
        payload = ProjectExportResponse(
            project_id=project["id"],
            project_title=project["title"] or "",
            source=_source(),
            total_samples=len(rows),
            total_sample_types=len(tables),
            unparseable_metadata=unparseable,
            data=[
                {
                    "sample_type": table["sample_type"],
                    "n_samples": len(table["data"]),
                    "samples": table["data"],
                }
                for table in tables
            ],
        )
        return Response(payload.model_dump(mode="json"), status=status.HTTP_200_OK)

    xlsx_bytes = generate_table_xlsx(tables)
    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", project["title"] or "").strip("-")[:40]
    filename = f"project-{project['id']}-{safe_title or 'export'}.xlsx"
    return FileResponse(
        io.BytesIO(xlsx_bytes),
        content_type=XLSX_CONTENT_TYPE,
        as_attachment=True,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# ProjectExportViewSet
# ---------------------------------------------------------------------------

_XLSX_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.BINARY,
    description="XLSX workbook, one sheet per sample type.",
)


class ProjectExportViewSet(viewsets.ViewSet):
    """Bulk-export every sample in a SEEK project (superuser-only)."""

    authentication_classes = [
        TokenAuthentication,
        CsrfExemptSessionAuthentication,
        BasicAuthentication,
    ]
    # IsAuthenticated first so anonymous callers get 401 with a WWW-Authenticate
    # challenge rather than a bare 403. IsSuperUser, not DRF's IsAdminUser —
    # see nextseek_api/permissions.py for why is_staff is meaningless here.
    permission_classes = [IsAuthenticated, IsSuperUser]

    @extend_schema(
        operation_id="Admin: Project Export (POST)",
        tags=["admin"],
        request=ProjectExportRequest,
        responses={
            (200, "application/json"): ProjectExportResponse,
            (200, XLSX_CONTENT_TYPE): _XLSX_RESPONSE,
        },
        examples=[
            OpenApiExample("JSON (default)", value={"project_id": 1}, request_only=True),
            OpenApiExample(
                "XLSX download",
                value={"project_id": 1, "output_format": "xlsx"},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="run")
    def run(self, request):
        try:
            payload = ProjectExportRequest.model_validate(request.data or {})
        except ValidationError as exc:
            return Response(
                {"errors": [{"title": "Invalid request", "detail": exc.errors()}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return export_project(payload.project_id, payload.output_format)

    @extend_schema(
        operation_id="Admin: Project Export (GET)",
        tags=["admin"],
        parameters=[
            OpenApiParameter(
                "output_format",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                enum=["json", "xlsx"],
                description="Output format (default: json).",
            ),
        ],
        responses={
            (200, "application/json"): ProjectExportResponse,
            (200, XLSX_CONTENT_TYPE): _XLSX_RESPONSE,
        },
    )
    def retrieve(self, request, pk=None):
        """GET /admin/project-export/{project_id}/?output_format=xlsx

        Browser-friendly twin of ``run`` — an admin can click a link.
        """
        try:
            payload = ProjectExportRequest.model_validate({
                "project_id": pk,
                "output_format": request.query_params.get("output_format", "json"),
            })
        except ValidationError as exc:
            return Response(
                {"errors": [{"title": "Invalid request", "detail": exc.errors()}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return export_project(payload.project_id, payload.output_format)
