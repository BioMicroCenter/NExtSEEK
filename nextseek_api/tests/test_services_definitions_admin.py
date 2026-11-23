"""The Definitions admin page's writes and its Needs attention list (nextseek_api/services/definitions_admin.py)."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User as DjangoUser
from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.graph_sync import hooks, sources
from nextseek_api.services import definitions_admin as da
from seek.models import Sample_types_context

ASSAY_COLS = ("assay_name", "internal_assay_id", "Description", "Tags", "Alternative_Assay_Names",
              "Required_Parent_Sample_Types", "Optional_Parent_Sample_Types", "Children_Sample_Types")
SEEK_TYPES = [{"id": 7, "title": "BAC"}, {"id": 8, "title": "TIS"}, {"id": 9, "title": "D.BSRA"},
              {"id": 10, "title": "A.ATAC"}, {"id": 11, "title": "DNA"}, {"id": 12, "title": "CEL"}]
SEEK_ATTRS = [{"sample_type_id": 7, "title": "UID", "required": True},
              {"sample_type_id": 7, "title": "Species", "required": False},
              {"sample_type_id": 7, "title": "Notes", "required": False}]


def make_tables():
    """The two context tables and internal_assays as the stacks spell them (assay_context has no model)."""
    cols = ", ".join(f"{f.column} {f.db_type(connection)}" for f in Sample_types_context._meta.concrete_fields
                     if not f.primary_key)
    with connection.cursor() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS sample_types_context (id INTEGER PRIMARY KEY, {cols})")
        cur.execute("CREATE TABLE IF NOT EXISTS assay_context (id INTEGER PRIMARY KEY, "
                    + ", ".join(f"{c} TEXT" for c in ASSAY_COLS) + ")")
        cur.execute("CREATE TABLE IF NOT EXISTS internal_assays (id INTEGER PRIMARY KEY, internal_assay_title TEXT)")


@pytest.fixture
def tables(db, monkeypatch):
    make_tables()
    Sample_types_context.objects.create(sample_type="BAC", sampletype_id=7, name="Bacteria Sample",
                                        description="old", tags="bacteria", required_metadata="UID",
                                        standard_metadata="Species", possible_metadata_fields="Notes")
    Sample_types_context.objects.create(sample_type="D.BSRA", name="Bisulfite reads")
    with connection.cursor() as cur:
        cur.execute("INSERT INTO assay_context (id, assay_name, Required_Parent_Sample_Types, Children_Sample_Types) "
                    "VALUES (3, 'DNA Extraction', 'BAC', 'DNA')")
        cur.execute("INSERT INTO internal_assays VALUES (5, 'DNA Extraction'), (6, 'Library Prep')")
    monkeypatch.setattr(sources, "sample_types", lambda: SEEK_TYPES)
    monkeypatch.setattr(sources, "sample_attributes", lambda: SEEK_ATTRS)
    queued = []
    monkeypatch.setattr(hooks, "enqueue", lambda kind, key, *a, **k: queued.append((kind, key)) or True)
    return queued


SUPER = DjangoUser(username="admin", is_superuser=True)
LAB = DjangoUser(username="lab", is_superuser=False, is_staff=True)


def _call(viewset, action, method, path, body=None, user=SUPER, **kw):
    request = getattr(APIRequestFactory(), method)(path, body, format="json")
    if user is not None:
        force_authenticate(request, user=user)
    return viewset.as_view({method: action})(request, **kw)


def _patch_type(code, body, user=SUPER):
    return _call(da.DefinitionSampleTypesViewSet, "partial_update", "patch",
                 f"/nextseek_api/definitions/sample_types/{code}/", body, user, pk=code)


def _patch_assay(row_id, body, user=SUPER):
    return _call(da.DefinitionAssaysViewSet, "partial_update", "patch",
                 f"/nextseek_api/definitions/assays/{row_id}/", body, user, pk=str(row_id))


def test_anonymous_gets_401_and_a_non_superuser_403(tables):
    assert _patch_type("BAC", {"name": "x"}, user=None).status_code == 401
    assert _patch_type("BAC", {"name": "x"}, user=LAB).status_code == 403
    assert _patch_assay(3, {"children": "DNA"}, user=LAB).status_code == 403
    assert tables == []


def test_hand_text_saves_and_queues_context_and_catalog(tables):
    r = _patch_type("BAC", {"description": "new", "tags": "bacteria, MTB"})
    assert r.status_code == 200
    assert r.data["row"]["description"] == "new" and r.data["row"]["Tags"] == "bacteria, MTB"
    assert tables == [("context", "*"), ("catalog", "*")]


def test_a_dotted_code_resolves(tables):
    from django.urls import resolve
    assert resolve("/nextseek_api/definitions/sample_types/D.BSRA/").kwargs["pk"] == "D.BSRA"
    assert _patch_type("D.BSRA", {"name": "Bisulfite reads"}).status_code == 200
    assert tables == [("context", "*")]          # nothing the SampleType node reads changed


def test_tiers_move_fields_and_queue_only_context(tables):
    r = _patch_type("BAC", {"tiers": {"required": ["UID"], "standard": ["Notes"], "possible": ["Species"]}})
    assert r.status_code == 200
    assert (r.data["row"]["standard_metadata"], r.data["row"]["possible_metadata_fields"]) == ("Notes", "Species")
    assert tables == [("context", "*")]


@pytest.mark.parametrize("tiers, why", [
    ({"required": ["UID", "Color"], "standard": ["Species"], "possible": ["Notes"]}, "not a SEEK attribute"),
    ({"required": ["UID"], "standard": ["Species"]}, "dropped"),
    ({"required": [], "standard": ["UID", "Species"], "possible": ["Notes"]}, "required tier"),
    ({"required": ["UID"], "standard": ["Species", "UID"], "possible": ["Notes"]}, "more than one tier"),
])
def test_tiers_never_change_which_fields_exist(tables, tiers, why):
    r = _patch_type("BAC", {"tiers": tiers})
    assert r.status_code == 422 and why in r.data["errors"][0]["detail"]
    assert tables == []


def test_bad_bodies_are_422_and_unknown_codes_404(tables):
    assert _patch_type("BAC", {}).status_code == 422
    assert _patch_type("BAC", {"clade": "Source"}).status_code == 422      # a derived column is not accepted
    assert _patch_type("NOPE", {"name": "x"}).status_code == 404


def test_assay_connections_save_in_the_alternation_grammar(tables):
    r = _patch_assay(3, {"required_parents": "BAC or CEL", "children": "DNA", "description": "DNA out"})
    assert r.status_code == 200
    assert r.data["row"]["required_parents"] == "BAC or CEL" and r.data["row"]["description"] == "DNA out"
    assert tables == [("context", "*"), ("assay_map", "*")]


def test_an_assay_code_that_is_not_a_seek_title_is_422(tables):
    r = _patch_assay(3, {"required_parents": "BAC or MOUSE"})
    assert r.status_code == 422 and "MOUSE" in r.data["errors"][0]["detail"]
    assert _patch_assay(99, {"children": "DNA"}).status_code == 404
    assert tables == []


def test_create_makes_a_bare_row_for_a_seek_type_or_internal_assay(tables):
    def post(viewset, path, body, user=SUPER):
        return _call(viewset, "create", "post", path, body, user)

    st = "/nextseek_api/definitions/sample_types/"
    r = post(da.DefinitionSampleTypesViewSet, st, {"code": "A.ATAC"})
    assert r.status_code == 201 and r.data["row"]["sample_type"] == "A.ATAC" and r.data["row"]["name"] is None
    assert post(da.DefinitionSampleTypesViewSet, st, {"code": "A.ATAC"}).status_code == 409
    assert post(da.DefinitionSampleTypesViewSet, st, {"code": "MOUSE"}).status_code == 422
    assert post(da.DefinitionSampleTypesViewSet, st, {"code": "TIS"}, user=LAB).status_code == 403

    a = "/nextseek_api/definitions/assays/"
    r = post(da.DefinitionAssaysViewSet, a, {"internal_assay_title": "Library Prep"})
    assert r.status_code == 201 and r.data["row"]["assay_name"] == "Library Prep"
    assert post(da.DefinitionAssaysViewSet, a, {"internal_assay_title": "Library Prep"}).status_code == 409
    assert post(da.DefinitionAssaysViewSet, a, {"internal_assay_title": "Nothing"}).status_code == 422
    # a new sample type row flips SampleType.has_context, so catalog too
    assert tables == [("context", "*"), ("catalog", "*"), ("context", "*"), ("assay_map", "*")]


def test_attention_lists_what_a_person_must_fix():
    context_rows = [{"id": 1, "sample_type": "BAC", "sampletype_id": 7, "child_sampletypes": "DNA, RNA"},
                    {"id": 2, "sample_type": "DNA", "sampletype_id": 11}]
    assay_rows = [{"id": 3, "assay_name": "DNA Extraction", "Required_Parent_Sample_Types": "BAC",
                   "Children_Sample_Types": "DNA", "internal_assay_id": 5},
                  {"id": 4, "assay_name": "Fc Receptor Binding Assay", "internal_assay_id": None}]
    internal = [{"id": 5, "internal_assay_title": "DNA Extraction"}, {"id": 6, "internal_assay_title": "Library Prep"},
                {"id": 7, "internal_assay_title": "FC Receptor  Binding Assay"}]
    connections = [{"parent": "BAC", "assay": "DNA Extraction", "child": "DNA", "designed": True, "observed": True},
                   {"parent": "BAC", "assay": "Library Prep", "child": "D.BSRA", "designed": False,
                    "observed": True}]
    out = da.attention(context_rows=context_rows, assay_rows=assay_rows, seek_types=SEEK_TYPES + [
        {"id": 13, "title": "RNA"}], seek_attributes=[], type_clades={}, internal_assays=internal,
        connection_rows=connections)
    assert out["undesigned"] == {"Library Prep": [("BAC", "D.BSRA")]}
    assert out["needs_definition"]["sample_types"] == ["A.ATAC", "CEL", "D.BSRA", "RNA", "TIS"]
    assert "Library Prep" in out["needs_definition"]["assays"]
    assert {"sample_type": "BAC", "column": "child_sampletypes", "entry": "RNA"} in out["would_drop"]
    assert out["spelling"] == [{"assay_map": "FC Receptor  Binding Assay", "definition": "Fc Receptor Binding Assay"}]


def test_the_four_operations_and_their_models_are_in_the_openapi_document():
    from drf_spectacular.generators import SchemaGenerator
    schema = SchemaGenerator().get_schema(request=None, public=True)
    paths = schema["paths"]
    st = next(p for p in paths if p.endswith("/definitions/sample_types/{id}/"))
    assay = next(p for p in paths if p.endswith("/definitions/assays/{id}/"))
    assert "patch" in paths[st] and "patch" in paths[assay]
    assert "post" in paths[st.rsplit("{id}/", 1)[0]] and "post" in paths[assay.rsplit("{id}/", 1)[0]]
    for model in ("PatchedDefinitionSampleTypeUpdateRequest", "PatchedDefinitionAssayUpdateRequest",
                  "DefinitionSampleTypeCreateRequest", "DefinitionAssayCreateRequest", "DefinitionRowResponse"):
        assert model in schema["components"]["schemas"]


def _post(viewset, path, body, user=SUPER):
    return _call(viewset, "create", "post", path, body, user)


def test_create_compares_titles_exactly_and_names_a_spelling_clash(tables):
    """MySQL's _ci collation ignores case and trailing spaces, so titles are compared in Python, as sources.py does."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO internal_assays VALUES (27, 'FC Receptor Binding Assay')")
        cur.execute("INSERT INTO assay_context (id, assay_name) VALUES (9, 'Fc Receptor Binding Assay')")
    a = "/nextseek_api/definitions/assays/"
    clash = _post(da.DefinitionAssaysViewSet, a, {"internal_assay_title": "FC Receptor Binding Assay"})
    assert clash.status_code == 409 and "Assay map" in clash.data["errors"][0]["detail"]
    assert _post(da.DefinitionAssaysViewSet, a, {"internal_assay_title": "library prep "}).status_code == 422
    st = "/nextseek_api/definitions/sample_types/"
    assert _post(da.DefinitionSampleTypesViewSet, st, {"code": "bac"}).status_code == 422
    assert tables == []


def test_attention_offers_no_create_for_a_title_that_differs_only_in_spelling():
    out = da.attention(context_rows=[], assay_rows=[{"id": 9, "assay_name": "Fc Receptor Binding Assay"}],
                       seek_types=[], seek_attributes=[], type_clades={}, connection_rows=[],
                       internal_assays=[{"id": 27, "internal_assay_title": "FC Receptor Binding Assay"}])
    assert out["needs_definition"]["assays"] == []
    assert out["spelling"] == [{"assay_map": "FC Receptor Binding Assay", "definition": "Fc Receptor Binding Assay"}]


def test_a_case_folded_url_code_still_keeps_seek_required_titles_required(tables, monkeypatch):
    """MySQL finds BAC for /sample_types/bac/; the SEEK ids must come from the row's own code, not the URL's."""
    real = da.sample_type_row
    monkeypatch.setattr(da, "sample_type_row", lambda code: real("BAC" if code == "bac" else code))
    monkeypatch.setattr(sources, "sample_attributes", lambda: [{**a, "title": a["title"] + " "} for a in SEEK_ATTRS])
    r = _patch_type("bac", {"tiers": {"required": [], "standard": ["UID", "Species"], "possible": ["Notes"]}})
    assert r.status_code == 422 and "required tier" in r.data["errors"][0]["detail"]
    ok = _patch_type("bac", {"name": "Bacteria"})
    assert ok.status_code == 200 and ok.data["row"]["sample_type"] == "BAC" and ok.data["row"]["name"] == "Bacteria"



def test_with_the_switch_off_a_new_seek_attribute_can_be_placed_and_a_stale_title_cannot_move(tables, monkeypatch):
    """The context step places a new SEEK attribute and drops a title SEEK no longer has only once the switch is on;
    until then the tier card offers the new one and pins the stale one."""
    monkeypatch.setattr(sources, "sample_attributes", lambda: SEEK_ATTRS + [
        {"sample_type_id": 7, "title": "Barcode ", "required": False}])
    with connection.cursor() as cur:
        cur.execute("UPDATE sample_types_context SET standard_metadata = 'Species, OldField' WHERE sample_type = 'BAC'")
    keep = {"required": ["UID"], "standard": ["Species", "OldField", "Barcode"], "possible": ["Notes"]}
    r = _patch_type("BAC", {"tiers": keep})
    assert r.status_code == 200 and r.data["row"]["standard_metadata"] == "Species, OldField, Barcode"
    moved = {"required": ["UID"], "standard": ["Species", "Barcode"], "possible": ["Notes", "OldField"]}
    r = _patch_type("BAC", {"tiers": moved})
    assert r.status_code == 422 and "not in SEEK" in r.data["errors"][0]["detail"]


def test_a_create_that_loses_a_race_is_409(tables, monkeypatch):
    """Two Creates pass the check together; the database's unique key refuses the second, which answers 409."""
    with connection.cursor() as cur:
        cur.execute("CREATE UNIQUE INDEX st_code ON sample_types_context (sample_type)")
        cur.execute("CREATE UNIQUE INDEX ac_name ON assay_context (assay_name)")
    monkeypatch.setattr(da, "_existing", lambda *a: None)       # the other request's row landed after the check
    r = _post(da.DefinitionSampleTypesViewSet, "/nextseek_api/definitions/sample_types/", {"code": "BAC"})
    assert r.status_code == 409
    r = _post(da.DefinitionAssaysViewSet, "/nextseek_api/definitions/assays/",
              {"internal_assay_title": "DNA Extraction"})
    assert r.status_code == 409
    assert tables == []
