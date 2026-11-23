"""GET /nextseek_api/definitions/export/ (nextseek_api/services/definitions_export.py)."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User as DjangoUser
from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.graph_sync import sources
from nextseek_api.graph_sync.models_db import SampleTypeConnectionRow
from nextseek_api.services import definitions_export
from nextseek_api.services.definitions_export import DefinitionsExportViewSet, build_export
from seek.models import Sample_types_context

URL = "/nextseek_api/definitions/export/"
ATTRS = [{"sample_type": "BAC", "key": "7:UID", "title": "UID", "pos": 0, "required": True}]
META = [{"schema_version": "1.3", "catalog_hash": "b13b", "synced_at": "2026-11-23T15:46:31.795Z"}]


def _graph(statements):
    assert set(statements) == {"meta", "attributes"}
    return {"meta": META, "attributes": ATTRS}


@pytest.fixture
def catalog(db, monkeypatch):
    cols = ", ".join(f"{f.column} {f.db_type(connection)}" for f in Sample_types_context._meta.concrete_fields
                     if not f.primary_key)
    with connection.cursor() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS sample_types_context (id INTEGER PRIMARY KEY, {cols})")
        cur.execute("CREATE TABLE IF NOT EXISTS assay_context (id INTEGER PRIMARY KEY, assay_name TEXT, "
                    "Children_Sample_Types TEXT, internal_assay_id INTEGER)")
        cur.execute("CREATE TABLE IF NOT EXISTS internal_assays (id INTEGER PRIMARY KEY, internal_assay_title TEXT)")
        cur.execute("INSERT INTO assay_context VALUES (3, 'DNA Extraction', 'DNA', 5)")
        cur.execute("INSERT INTO internal_assays VALUES (5, 'DNA Extraction'), (6, 'Library Prep')")
    Sample_types_context.objects.create(sample_type="TIS", clade="Processed")
    Sample_types_context.objects.create(sample_type="BAC", sampletype_id=7, clade="Source", name="Bacteria Sample")
    SampleTypeConnectionRow.objects.create(parent_type="BAC", assay="DNA Extraction", child_type="DNA",
                                           designed=True, observed=True)
    SampleTypeConnectionRow.objects.create(parent_type="TIS", assay="Bacterial Extraction", child_type="BAC",
                                           designed=True, observed=False)
    monkeypatch.setattr(sources, "sample_types", lambda: [{"id": 7, "title": "BAC"}, {"id": 9, "title": "A.ATAC"},
                                                          {"id": 8, "title": "TIS"}])


def test_the_export_carries_every_piece_in_the_old_bundles_shape(catalog):
    out = build_export(graph_reader=_graph)

    assert [r["sample_type"] for r in out["sample_types"]] == ["BAC", "TIS"]
    assert "id" not in out["sample_types"][0] and "id" not in out["assays"][0]
    assert out["assays"][0]["assay_name"] == "DNA Extraction"
    assert out["connections"] == [{"assay": "DNA Extraction", "parent_type": "BAC", "child_type": "DNA"}]
    assert out["licensed_connections"][1] == {"parent": "TIS", "assay": "Bacterial Extraction", "child": "BAC",
                                              "designed": True, "observed": False,
                                              "parent_clade": "Processed", "child_clade": "Source"}
    assert out["attributes"] == ATTRS and out["graph_meta"] == META[0]
    assert out["needs_definition"] == {"sample_types": ["A.ATAC"], "assays": ["Library Prep"]}


def _get(user=None):
    request = APIRequestFactory().get(URL)
    if user is not None:
        force_authenticate(request, user=user)
    return DefinitionsExportViewSet.as_view({"get": "export"})(request)


def test_anonymous_gets_401():
    assert _get().status_code == 401


def test_any_logged_in_user_gets_the_export(monkeypatch):
    monkeypatch.setattr(definitions_export, "build_export", lambda: {"sample_types": []})
    response = _get(DjangoUser(username="lab", is_staff=False, is_superuser=False))
    assert response.status_code == 200 and response.data == {"sample_types": []}


def test_a_graph_that_cannot_be_read_is_a_502(monkeypatch):
    def down():
        raise RuntimeError("neo4j went away")

    monkeypatch.setattr(definitions_export, "build_export", down)
    response = _get(DjangoUser(username="lab"))
    assert response.status_code == 502
    assert response.data["errors"][0]["detail"] == "neo4j went away"


def test_the_schema_documents_the_route_and_its_models():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    op = schema["paths"][URL]["get"]
    assert op["operationId"] == "Export Definitions"
    assert op["responses"]["200"]["content"]["application/json"]["examples"]
    assert {"DefinitionsExportResponse", "DefinitionsLicensedConnection"} <= set(schema["components"]["schemas"])
