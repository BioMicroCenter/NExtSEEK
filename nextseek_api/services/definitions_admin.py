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
- **Look-alikes are checked.** ``similar_types`` (JSON text, a list of ``{code, differs}``) names SEEK sample type
  titles on a sample type row and assay names (``assay_context`` or ``internal_assays``) on an assay row, never the
  row itself. ``when_to_use`` is plain text. Neither is read by the SampleType node, so neither queues ``catalog``.
- **After the commit, queue the syncs.** ``context`` always; ``catalog`` when a column the SampleType node reads
  changed (name, description, Tags); ``assay_map`` after any assay edit, because the Assay nodes and their catalog
  edges are built from ``assay_context``.
- **The page reads no graph.** ``attention`` works from ``sample_type_connections`` and the dmac and SEEK tables.
"""
from __future__ import annotations

import json
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, connections, transaction
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
# Hand columns the Django model does not carry (migration 0036 adds them); read and written with raw SQL.
GUIDE_COLUMNS = ("when_to_use", "similar_types")
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


_ST_COLS = ", ".join(["id", "sample_type"] + list(SAMPLE_TYPE_TEXT.values()) + list(TIER_COLUMNS.values())
                     + list(GUIDE_COLUMNS))


def similar_list(raw) -> list | None:
    """The stored JSON text of ``similar_types`` as a list; None when empty or not a list."""
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) and value else None


def similar_text(raw) -> str:
    """``similar_types`` as the page's textarea holds it: one ``code | differs`` per line."""
    return "\n".join(f"{e.get('code', '')} | {e.get('differs', '')}" for e in similar_list(raw) or [])


def _shown(row: dict | None) -> dict | None:
    if row is not None:
        row["similar_types"] = similar_list(row.get("similar_types"))
    return row


def _guide_cells(body, own: str, valid: set[str]) -> dict:
    """The when_to_use / similar_types cells a request sets. Every code must be in ``valid`` and none the row's own."""
    cells = {}
    if body.when_to_use is not None:
        cells["when_to_use"] = body.when_to_use.strip() or None
    if body.similar_types is not None:
        codes = [e.code for e in body.similar_types]
        if any(_norm(c) == _norm(own) for c in codes):
            raise Invalid(f"similar_types may not name the row itself ({own!r})")
        unknown = sorted({c for c in codes if c not in valid})
        if unknown:
            raise Invalid(f"similar_types names {unknown}, which this box does not have")
        cells["similar_types"] = (json.dumps([e.model_dump() for e in body.similar_types], ensure_ascii=False)
                                  if codes else None)
    return cells


def sample_type_row(code: str) -> dict | None:
    """The row for a code. MySQL may match it in another case or with trailing spaces, so callers read the row's
    own ``sample_type`` and address it by ``id`` from here on."""
    return _shown(_one(f"SELECT {_ST_COLS} FROM sample_types_context WHERE sample_type = %s", [code]))


def _sample_type_by_id(row_id: int) -> dict | None:
    return _shown(_one(f"SELECT {_ST_COLS} FROM sample_types_context WHERE id = %s", [row_id]))


def seek_attribute_titles(code: str) -> tuple[list[str], set[str]]:
    """The type's SEEK attribute titles, stripped, in SEEK's order, and the required ones. Joined by the exact
    title, lowest id first, as the context step joins."""
    ids = [t["id"] for t in sources.sample_types() if t["title"] == code]
    attrs = [a for a in sources.sample_attributes() if ids and a["sample_type_id"] == min(ids)]
    titles = list(dict.fromkeys(a["title"].strip() for a in attrs if a["title"].strip()))
    return titles, {a["title"].strip() for a in attrs if a["required"]}


def assay_row(row_id: int) -> dict | None:
    cols = ", ".join(f"{c} AS {f}" for f, c in ASSAY_TEXT.items())
    return _shown(_one(f"SELECT id, assay_name, {cols}, {', '.join(GUIDE_COLUMNS)} FROM assay_context "
                       "WHERE id = %s", [row_id]))


def _bad_codes(raw, titles: set[str]) -> list[str]:
    """The codes a designed column names that are not SEEK sample type titles here."""
    return [c for c in context_derived._codes(raw) if c not in titles]


def stored_tiers(row: dict) -> dict[str, str]:
    """Each title the row lists, to its tier."""
    return {t: tier for tier, col in TIER_COLUMNS.items() for t in parse_list(row.get(col))}


def _check_tiers(row: dict, tiers: dict[str, list[str]], seek_titles: list[str], seek_required: set[str]) -> None:
    """Which fields exist is SEEK's. A tier edit keeps every title the row lists, may place a SEEK title of the type
    that no tier holds yet (the context step does that only once its switch is on), never moves a title SEEK no
    longer has (the step drops those), and keeps SEEK-required titles required."""
    stored = stored_tiers(row)
    given = {t: tier for tier in TIER_COLUMNS for t in tiers.get(tier, [])}
    if len(given) != sum(len(tiers.get(tier, [])) for tier in TIER_COLUMNS):
        raise Invalid("a field is listed in more than one tier")
    dropped = sorted(set(stored) - set(given))
    if dropped:
        raise Invalid(f"tiers move fields, they do not remove them (dropped {dropped}); which fields exist is SEEK's")
    alien = sorted(set(given) - set(stored) - set(seek_titles))
    if alien:
        raise Invalid(f"{alien} is not a SEEK attribute of this type; which fields exist is SEEK's")
    stale = sorted(t for t in stored if seek_titles and t not in seek_titles and given[t] != stored[t])
    if stale:
        raise Invalid(f"{stale} is not in SEEK any more; it stays where it is until the context step drops it")
    demoted = sorted((seek_required & set(given)) - set(tiers.get("required", [])))
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
        seek_titles, seek_required = seek_attribute_titles(row["sample_type"])
        _check_tiers(row, tiers, seek_titles, seek_required)
        cells.update({col: _cell(tiers.get(tier, [])) for tier, col in TIER_COLUMNS.items()})
    cells.update(_guide_cells(body, row["sample_type"], {t["title"] for t in sources.sample_types()}))
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
    current = assay_row(row_id)
    if current is None:
        raise LookupError(row_id)
    given = {f: getattr(body, f) for f in ASSAY_TEXT if getattr(body, f) is not None}
    titles = {t["title"] for t in sources.sample_types()}
    bad = {f: _bad_codes(given[f], titles) for f in ASSAY_CODE_FIELDS if f in given}
    bad = {f: codes for f, codes in bad.items() if codes}
    if bad:
        raise Invalid(f"not SEEK sample type titles on this box: {bad}")
    cells = {ASSAY_TEXT[f]: (v.strip() or None) for f, v in given.items()}
    names = (set(_column("SELECT assay_name FROM assay_context"))
             | set(_column("SELECT internal_assay_title FROM internal_assays"))) if body.similar_types else set()
    cells.update(_guide_cells(body, current["assay_name"], names))
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
    # derive's unknown_codes holds the codes the assay rows and the data name; a row's own hand-typed code is checked
    # against SEEK's titles the same way.
    titles = {t["title"] for t in seek_types}
    unknown = set(result.report.get("unknown_codes", ()))
    would_drop = []
    for code, cells in sorted(result.sample_types.items()):
        old = by_code[code]
        for col in context_derived.CONNECTION_COLUMNS:
            if col not in cells:
                continue
            read = context_derived._codes if col in context_derived.CODE_COLUMNS else parse_list
            new = set(read(cells[col]))
            for entry in read(old.get(col)):
                if entry not in new:
                    why = ("type not on this box" if col in context_derived.CODE_COLUMNS
                           and (entry in unknown or entry not in titles)
                           else "no definition or data backs it")
                    would_drop.append({"sample_type": code, "column": col, "entry": entry, "reason": why})
        # A title moving between tiers (SEEK made it required) is not a removal; one leaving every tier is.
        meta = context_derived.METADATA_COLUMNS
        kept = {t for col in meta for t in parse_list(cells[col] if col in cells else old.get(col))}
        for col in meta:
            for entry in parse_list(old.get(col)):
                if entry not in kept:
                    would_drop.append({"sample_type": code, "column": col, "entry": entry,
                                       "reason": "SEEK no longer has this field"})

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
           "possible_metadata_fields": "Notes", "when_to_use": "Use this when the sample is a cultured strain.",
           "similar_types": [{"code": "TIS", "differs": "a bulk tissue piece, not a strain"}]}
_ASSAY_ROW = {"id": 3, "assay_name": "DNA Extraction", "description": "DNA isolated from a sample.", "tags": "dna",
              "alternative_names": None, "required_parents": "BAC or CEL", "optional_parents": None,
              "children": "DNA", "when_to_use": "Use this when DNA is pulled from cells.",
              "similar_types": [{"code": "Library Prep", "differs": "makes libraries from DNA this one extracts"}]}


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
            OpenApiExample("Say when to use it", request_only=True,
                           value={"when_to_use": "Use this when the sample is a cultured strain; use TIS instead "
                                                 "when it is a piece of tissue.",
                                  "similar_types": [{"code": "TIS", "differs": "a bulk tissue piece, not a strain"}]}),
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
                                  "enqueued": ["context", "catalog"]}),
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
        except IntegrityError:     # another request created it between the check and the insert
            return _error(status.HTTP_409_CONFLICT, "Already defined", "another request created it just now")
        hooks.enqueue("context", "*")
        hooks.enqueue("catalog", "*")   # the new row flips SampleType.has_context
        return Response({"row": row, "enqueued": ["context", "catalog"]}, status=status.HTTP_201_CREATED)


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
            OpenApiExample("Say when to use it", request_only=True,
                           value={"when_to_use": "Use this when DNA is pulled from cells; use Library Prep instead "
                                                 "when the DNA is already extracted.",
                                  "similar_types": [{"code": "Library Prep",
                                                     "differs": "starts from extracted DNA"}]}),
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
        except IntegrityError:     # another request created it between the check and the insert
            return _error(status.HTTP_409_CONFLICT, "Already defined", "another request created it just now")
        hooks.enqueue("context", "*")
        hooks.enqueue("assay_map", "*")
        return Response({"row": row, "enqueued": ["context", "assay_map"]}, status=status.HTTP_201_CREATED)
