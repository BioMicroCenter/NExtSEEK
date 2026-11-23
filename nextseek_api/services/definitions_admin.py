"""The Definitions admin page's writes and its "Needs attention" list (``/seek/admin/definitions/``).

Superuser only, one row at a time, hand columns only:

- ``PATCH /nextseek_api/definitions/sample_types/{code}/``: a sample type's name, description, Tags and metadata tiers.
- ``POST /nextseek_api/definitions/sample_types/``: a ``sample_types_context`` row for a SEEK type that has none.
- ``PATCH /nextseek_api/definitions/assays/{id}/``: an assay's description, Tags, other names and its designed
  connections (required parents, optional parents, children, in the ``A or B, C`` grammar).
- ``POST /nextseek_api/definitions/assays/``: an ``assay_context`` row for an internal assay that has none.

Rules this module keeps:

- **Never a derived column.** ``sampletype_id``, the context table's ``clade``, the metadata membership, the four
  connection lists and ``internal_assay_id`` belong to graph_sync's context step (``context_derived``). A tier edit
  may only move titles the row already lists between required, standard and possible; a title SEEK marks required
  stays required.
- **Codes are SEEK titles on this box.** Every code a designed column names must be a SEEK sample type title.
- **Assays are keyed by row id**, not name: ``assay_context`` carries the same name on more than one row.
- **After the commit, queue the syncs.** ``context`` always; ``catalog`` when a column the SampleType node reads
  changed (name, description, Tags); ``assay_map`` after any assay edit, because the Assay nodes and their catalog
  edges are built from ``assay_context``.
- **The page reads no graph.** ``attention`` works from ``sample_type_connections`` and the dmac and SEEK tables.
"""
from __future__ import annotations

from collections import defaultdict

from django.conf import settings
from django.db import connections, transaction
from drf_spectacular.utils import OpenApiExample, extend_schema
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.authentication import SeekSessionAuthentication
from nextseek_api.endpoint_descriptions import (
    DEFINITIONS_ASSAY_CREATE_DESC,
    DEFINITIONS_ASSAY_UPDATE_DESC,
    DEFINITIONS_SAMPLE_TYPE_CREATE_DESC,
    DEFINITIONS_SAMPLE_TYPE_UPDATE_DESC,
)
from nextseek_api.graph_sync import context_derived, hooks, sources
from nextseek_api.models import (
    DefinitionAssayCreateRequest,
    DefinitionAssayUpdateRequest,
    DefinitionRowResponse,
    DefinitionSampleTypeCreateRequest,
    DefinitionSampleTypeUpdateRequest,
    JsonApiErrorResponse,
)
from nextseek_api.services.context_catalog import parse_list
from nextseek_api.services.users import IsDjangoSuperuser

# Request field -> table column. Mixed case as the tables spell them; MySQL matches column names in any case.
SAMPLE_TYPE_TEXT = {"name": "name", "description": "description", "tags": "Tags"}
TIER_COLUMNS = {"required": "required_metadata", "standard": "standard_metadata",
                "possible": "possible_metadata_fields"}
ASSAY_TEXT = {"description": "Description", "tags": "Tags", "alternative_names": "Alternative_Assay_Names",
              "required_parents": "Required_Parent_Sample_Types",
              "optional_parents": "Optional_Parent_Sample_Types", "children": "Children_Sample_Types"}
ASSAY_CODE_FIELDS = ("required_parents", "optional_parents", "children")


class Invalid(Exception):
    """A request that names something this box does not have; answered 422."""


def _cursor():
    return connections[settings.NEXTSEEK_DATABASE].cursor()


def _one(sql: str, params) -> dict | None:
    with _cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None


_ST_COLS = ", ".join(["id", "sample_type"] + list(SAMPLE_TYPE_TEXT.values()) + list(TIER_COLUMNS.values()))


def sample_type_row(code: str) -> dict | None:
    """The row for a code. MySQL may match it in another case or with trailing spaces, so callers read the row's
    own ``sample_type`` and address it by ``id`` from here on."""
    return _one(f"SELECT {_ST_COLS} FROM sample_types_context WHERE sample_type = %s", [code])


def _sample_type_by_id(row_id: int) -> dict | None:
    return _one(f"SELECT {_ST_COLS} FROM sample_types_context WHERE id = %s", [row_id])


def seek_attribute_titles(code: str) -> tuple[list[str], set[str]]:
    """The type's SEEK attribute titles, stripped, in SEEK's order, and the required ones. Joined by the exact
    title, lowest id first, as the context step joins."""
    ids = [t["id"] for t in sources.sample_types() if t["title"] == code]
    attrs = [a for a in sources.sample_attributes() if ids and a["sample_type_id"] == min(ids)]
    titles = list(dict.fromkeys(a["title"].strip() for a in attrs if a["title"].strip()))
    return titles, {a["title"].strip() for a in attrs if a["required"]}


def assay_row(row_id: int) -> dict | None:
    cols = ", ".join(f"{c} AS {f}" for f, c in ASSAY_TEXT.items())
    return _one(f"SELECT id, assay_name, {cols} FROM assay_context WHERE id = %s", [row_id])


def _bad_codes(raw, titles: set[str]) -> list[str]:
    """The codes a designed column names that are not SEEK sample type titles here."""
    return [c for c in context_derived._codes(raw) if c not in titles]


def _check_tiers(row: dict, tiers: dict[str, list[str]], seek_required: set[str]) -> None:
    """A tier edit only moves the titles the row already lists; SEEK-required titles stay required."""
    stored = {t for col in TIER_COLUMNS.values() for t in parse_list(row.get(col))}
    given = [t for tier in TIER_COLUMNS for t in tiers.get(tier, [])]
    if len(given) != len(set(given)):
        raise Invalid("a field is listed in more than one tier")
    if set(given) != stored:
        added, dropped = sorted(set(given) - stored), sorted(stored - set(given))
        raise Invalid(f"tiers move fields, they do not add or remove them (added {added}, dropped {dropped}); "
                      "which fields exist is SEEK's")
    demoted = sorted((seek_required & stored) - set(tiers.get("required", [])))
    if demoted:
        raise Invalid(f"SEEK marks {demoted} required; those stay in the required tier")


def _cell(values: list[str]):
    return ", ".join(values) if values else None


def update_sample_type(code: str, body: DefinitionSampleTypeUpdateRequest) -> tuple[dict, bool]:
    """Write the hand columns; return the row and whether a column the SampleType node reads changed."""
    row = sample_type_row(code)
    if row is None:
        raise LookupError(code)
    cells = {SAMPLE_TYPE_TEXT[f]: (getattr(body, f).strip() or None) for f in SAMPLE_TYPE_TEXT
             if getattr(body, f) is not None}
    if body.tiers is not None:
        tiers = body.tiers.model_dump()
        _, seek_required = seek_attribute_titles(row["sample_type"])
        _check_tiers(row, tiers, seek_required)
        cells.update({col: _cell(tiers.get(tier, [])) for tier, col in TIER_COLUMNS.items()})
    node_changed = any(row.get(col) != cells[col] for col in SAMPLE_TYPE_TEXT.values() if col in cells)
    _write_sample_type(row["id"], cells)
    return _sample_type_by_id(row["id"]), node_changed


def _write_sample_type(row_id: int, cells: dict) -> None:
    sets = ", ".join(f"{col} = %s" for col in cells)
    with transaction.atomic(using=settings.NEXTSEEK_DATABASE), _cursor() as cur:
        cur.execute(f"UPDATE sample_types_context SET {sets} WHERE id = %s", [*cells.values(), row_id])


def _column(sql: str) -> list[str]:
    with _cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall() if r[0] is not None]


def _existing(title: str, names: list[str], fix: str) -> None:
    """Refuse a title a row already holds, or holds but for case or spacing. Compared in Python: MySQL's _ci
    collation ignores case and trailing spaces, so a SQL ``=`` would call them the same title."""
    if title in names:
        raise FileExistsError(f"{title!r} already has a definition")
    near = [n for n in names if _norm(n) == _norm(title)]
    if near:
        raise FileExistsError(f"{title!r} differs only in case or spacing from the definition {near[0]!r}; {fix}")


def create_sample_type(code: str) -> dict:
    if code not in {t["title"] for t in sources.sample_types()}:
        raise Invalid(f"{code!r} is not a SEEK sample type title on this box")
    _existing(code, _column("SELECT sample_type FROM sample_types_context"), "rename the type in SEEK")
    with transaction.atomic(using=settings.NEXTSEEK_DATABASE), _cursor() as cur:
        cur.execute("INSERT INTO sample_types_context (sample_type) VALUES (%s)", [code])
    return sample_type_row(code)


def update_assay(row_id: int, body: DefinitionAssayUpdateRequest) -> dict:
    if assay_row(row_id) is None:
        raise LookupError(row_id)
    given = {f: getattr(body, f) for f in ASSAY_TEXT if getattr(body, f) is not None}
    titles = {t["title"] for t in sources.sample_types()}
    bad = {f: _bad_codes(given[f], titles) for f in ASSAY_CODE_FIELDS if f in given}
    bad = {f: codes for f, codes in bad.items() if codes}
    if bad:
        raise Invalid(f"not SEEK sample type titles on this box: {bad}")
    cells = {ASSAY_TEXT[f]: (v.strip() or None) for f, v in given.items()}
    _write_assay(row_id, cells)
    return assay_row(row_id)


def _write_assay(row_id: int, cells: dict) -> None:
    sets = ", ".join(f"{col} = %s" for col in cells)
    with transaction.atomic(using=settings.NEXTSEEK_DATABASE), _cursor() as cur:
        cur.execute(f"UPDATE assay_context SET {sets} WHERE id = %s", [*cells.values(), row_id])


def create_assay(title: str) -> dict:
    if title not in _column("SELECT internal_assay_title FROM internal_assays"):
        raise Invalid(f"{title!r} is not an internal assay on this box")
    _existing(title, _column("SELECT assay_name FROM assay_context"), "rename it on the Assay map tab")
    with transaction.atomic(using=settings.NEXTSEEK_DATABASE), _cursor() as cur:
        cur.execute("INSERT INTO assay_context (assay_name) VALUES (%s)", [title])
    with _cursor() as cur:
        cur.execute("SELECT id, assay_name FROM assay_context ORDER BY id DESC")
        return assay_row(next(i for i, n in cur.fetchall() if n == title))


# --- the "Needs attention" list ----------------------------------------------------------------------------------

def _norm(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def attention(*, context_rows, assay_rows, seek_types, seek_attributes, type_clades, internal_assays,
              connection_rows) -> dict:
    """What the page lists as needing a person. Pure: plain rows in.

    ``connection_rows`` are ``sample_type_connections`` rows (parent, assay, child, designed, observed).
    """
    undesigned: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for c in connection_rows:
        if c["observed"] and not c["designed"]:
            undesigned[c["assay"]].append((c["parent"], c["child"]))

    observed = [(c["parent"], c["assay"], c["child"]) for c in connection_rows if c["observed"]]
    result = context_derived.derive(context_rows=context_rows, assay_rows=assay_rows, seek_types=seek_types,
                                    seek_attributes=seek_attributes, type_clades=type_clades,
                                    internal_assays=internal_assays, observed=observed)
    by_code = {r["sample_type"]: r for r in context_rows}
    would_drop = []
    for code, cells in sorted(result.sample_types.items()):
        for col in context_derived.CONNECTION_COLUMNS:
            if col not in cells:
                continue
            read = context_derived._codes if col in context_derived.CODE_COLUMNS else parse_list
            new = set(read(cells[col]))
            for entry in read(by_code[code].get(col)):
                if entry not in new:
                    would_drop.append({"sample_type": code, "column": col, "entry": entry})

    names = {a["assay_name"] for a in assay_rows if a.get("assay_name")}
    by_norm = defaultdict(set)
    for n in names:
        by_norm[_norm(n)].add(n)
    spelling = sorted({(r["internal_assay_title"], n) for r in internal_assays
                       for n in by_norm.get(_norm(r["internal_assay_title"]), ())
                       if n != r["internal_assay_title"]})

    needs = context_derived.needs_definition([t["title"] for t in seek_types], context_rows, internal_assays,
                                             assay_rows)
    # A title that differs from a definition only by case or spacing gets no Create (it would answer 409): it is
    # listed under spelling, to be renamed.
    clash = {a for a, _ in spelling}
    needs["assays"] = [t for t in needs["assays"] if t not in clash]
    return {
        "undesigned": {a: sorted(set(p)) for a, p in sorted(undesigned.items())},
        "needs_definition": needs,
        "would_drop": would_drop,
        "spelling": [{"assay_map": a, "definition": d} for a, d in spelling],
    }


# --- the endpoints -----------------------------------------------------------------------------------------------

def _error(code: int, title: str, detail: str) -> Response:
    return Response({"errors": [{"title": title, "detail": detail}]}, status=code)


def _parse(model, data):
    try:
        return model.model_validate(data), None
    except ValidationError as exc:
        return None, _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request", str(exc))


class _SuperuserViewSet(viewsets.ViewSet):
    authentication_classes = [SeekSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsDjangoSuperuser]
    lookup_value_regex = r"[^/]+"   # sample type codes carry dots (D.BSRA)

    def get_authenticate_header(self, request):
        # The session authenticator has no challenge, so an anonymous call would read 403; Basic's makes it a 401.
        return BasicAuthentication().authenticate_header(request)


_ST_ROW = {"sample_type": "BAC", "name": "Bacteria Sample", "description": "A microbial strain used in an experiment.",
           "Tags": "bacteria, pathogen", "required_metadata": "UID, Name", "standard_metadata": "Species",
           "possible_metadata_fields": "Notes"}
_ASSAY_ROW = {"id": 3, "assay_name": "DNA Extraction", "description": "DNA isolated from a sample.", "tags": "dna",
              "alternative_names": None, "required_parents": "BAC or CEL", "optional_parents": None,
              "children": "DNA"}


class DefinitionSampleTypesViewSet(_SuperuserViewSet):
    """Hand text and tiers of one ``sample_types_context`` row (module docstring)."""

    @extend_schema(
        operation_id="Update Sample Type Definition",
        description=DEFINITIONS_SAMPLE_TYPE_UPDATE_DESC,
        request=DefinitionSampleTypeUpdateRequest,
        responses={200: DefinitionRowResponse, 404: JsonApiErrorResponse, 422: JsonApiErrorResponse},
        tags=["SampleTypes"],
        examples=[
            OpenApiExample("Move Species to standard", request_only=True,
                           value={"description": "A microbial strain used in an experiment.",
                                  "tiers": {"required": ["UID", "Name"], "standard": ["Species"],
                                            "possible": ["Notes"]}}),
            OpenApiExample("Saved", response_only=True, value={"row": _ST_ROW, "enqueued": ["context", "catalog"]}),
        ],
    )
    def partial_update(self, request, pk=None):
        body, bad = _parse(DefinitionSampleTypeUpdateRequest, request.data)
        if bad:
            return bad
        try:
            row, node_changed = update_sample_type(pk, body)
        except LookupError:
            return _error(status.HTTP_404_NOT_FOUND, "No definition", f"no sample_types_context row for {pk!r}")
        except Invalid as exc:
            return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request", str(exc))
        enqueued = ["context"]
        hooks.enqueue("context", "*")
        if node_changed:
            hooks.enqueue("catalog", "*")
            enqueued.append("catalog")
        return Response({"row": row, "enqueued": enqueued})

    @extend_schema(
        operation_id="Create Sample Type Definition",
        description=DEFINITIONS_SAMPLE_TYPE_CREATE_DESC,
        request=DefinitionSampleTypeCreateRequest,
        responses={201: DefinitionRowResponse, 409: JsonApiErrorResponse, 422: JsonApiErrorResponse},
        tags=["SampleTypes"],
        examples=[
            OpenApiExample("Create A.ATAC", request_only=True, value={"code": "A.ATAC"}),
            OpenApiExample("Created", response_only=True, status_codes=["201"],
                           value={"row": {"sample_type": "A.ATAC", "name": None, "description": None},
                                  "enqueued": ["context"]}),
        ],
    )
    def create(self, request):
        body, bad = _parse(DefinitionSampleTypeCreateRequest, request.data)
        if bad:
            return bad
        try:
            row = create_sample_type(body.code)
        except Invalid as exc:
            return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request", str(exc))
        except FileExistsError as exc:
            return _error(status.HTTP_409_CONFLICT, "Already defined", str(exc))
        hooks.enqueue("context", "*")
        return Response({"row": row, "enqueued": ["context"]}, status=status.HTTP_201_CREATED)


class DefinitionAssaysViewSet(_SuperuserViewSet):
    """Hand text and designed connections of one ``assay_context`` row, by row id (module docstring)."""

    @extend_schema(
        operation_id="Update Assay Definition",
        description=DEFINITIONS_ASSAY_UPDATE_DESC,
        request=DefinitionAssayUpdateRequest,
        responses={200: DefinitionRowResponse, 404: JsonApiErrorResponse, 422: JsonApiErrorResponse},
        tags=["Assays"],
        examples=[
            OpenApiExample("Allow CEL as a parent", request_only=True,
                           value={"required_parents": "BAC or CEL", "children": "DNA"}),
            OpenApiExample("Saved", response_only=True, value={"row": _ASSAY_ROW,
                                                               "enqueued": ["context", "assay_map"]}),
        ],
    )
    def partial_update(self, request, pk=None):
        if not str(pk).isdigit():
            return _error(status.HTTP_404_NOT_FOUND, "No definition", "assay definitions are addressed by row id")
        body, bad = _parse(DefinitionAssayUpdateRequest, request.data)
        if bad:
            return bad
        try:
            row = update_assay(int(pk), body)
        except LookupError:
            return _error(status.HTTP_404_NOT_FOUND, "No definition", f"no assay_context row {pk}")
        except Invalid as exc:
            return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request", str(exc))
        hooks.enqueue("context", "*")
        hooks.enqueue("assay_map", "*")
        return Response({"row": row, "enqueued": ["context", "assay_map"]})

    @extend_schema(
        operation_id="Create Assay Definition",
        description=DEFINITIONS_ASSAY_CREATE_DESC,
        request=DefinitionAssayCreateRequest,
        responses={201: DefinitionRowResponse, 409: JsonApiErrorResponse, 422: JsonApiErrorResponse},
        tags=["Assays"],
        examples=[
            OpenApiExample("Create Library Prep", request_only=True, value={"internal_assay_title": "Library Prep"}),
            OpenApiExample("Created", response_only=True, status_codes=["201"],
                           value={"row": {"id": 151, "assay_name": "Library Prep"},
                                  "enqueued": ["context", "assay_map"]}),
        ],
    )
    def create(self, request):
        body, bad = _parse(DefinitionAssayCreateRequest, request.data)
        if bad:
            return bad
        try:
            row = create_assay(body.internal_assay_title)
        except Invalid as exc:
            return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request", str(exc))
        except FileExistsError as exc:
            return _error(status.HTTP_409_CONFLICT, "Already defined", str(exc))
        hooks.enqueue("context", "*")
        hooks.enqueue("assay_map", "*")
        return Response({"row": row, "enqueued": ["context", "assay_map"]}, status=status.HTTP_201_CREATED)
