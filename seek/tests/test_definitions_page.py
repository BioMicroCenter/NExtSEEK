"""``/seek/admin/definitions/`` (seek/views/definitions.py) and the three old pages that became its tabs."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from nextseek_api.graph_sync import sources
from nextseek_api.graph_sync.models_db import SampleTypeConnectionRow
from nextseek_api.tests.test_services_definitions_admin import SEEK_ATTRS, SEEK_TYPES, make_tables
from seek.models import Sample_types_context


def _user(sup):
    return SimpleNamespace(is_authenticated=True, is_superuser=sup, is_staff=True, username="u")


def _req(path, sup=True):
    req = RequestFactory().get(path)
    req.user = _user(sup)
    req.COOKIES = {}
    return req


@pytest.fixture
def catalog(db, monkeypatch):
    """BAC with hand text and tiers, a few connections (one seen in data but allowed by no assay), a hand-typed
    child nothing backs, an assay map title spelled differently from its definition."""
    from django.db import connection
    make_tables()
    Sample_types_context.objects.create(
        sample_type="BAC", sampletype_id=7, clade="Source", name="Bacteria Sample",
        description="A microbial strain used in an experiment.", tags="bacteria, pathogen, MTB",
        required_metadata="UID", standard_metadata="Species", possible_metadata_fields="Notes",
        child_sampletypes="DNA, RNA", parent_sampletypes="TIS")
    Sample_types_context.objects.create(sample_type="TIS", sampletype_id=8, clade="Source", name="Tissue")
    Sample_types_context.objects.create(sample_type="DNA", sampletype_id=11, clade="Processed", name="DNA")
    with connection.cursor() as cur:
        cur.execute("INSERT INTO assay_context (id, assay_name, Description, Required_Parent_Sample_Types, "
                    "Children_Sample_Types, internal_assay_id) VALUES "
                    "(3, 'DNA Extraction', 'DNA isolated from a sample.', 'BAC or CEL', 'DNA', 5), "
                    "(4, 'Bacterial Extraction', NULL, 'TIS', 'BAC', NULL), "
                    "(5, 'Fc Receptor Binding Assay', NULL, NULL, NULL, NULL)")
        cur.execute("INSERT INTO internal_assays VALUES (5, 'DNA Extraction'), (6, 'Library Prep'), "
                    "(7, 'FC Receptor Binding Assay')")
    for p, a, c, d, o in [("BAC", "DNA Extraction", "DNA", True, True), ("TIS", "Bacterial Extraction", "BAC", True,
                                                                         True),
                          ("BAC", "Library Prep", "D.BSRA", False, True)]:
        SampleTypeConnectionRow.objects.create(parent_type=p, assay=a, child_type=c, designed=d, observed=o)
    monkeypatch.setattr(sources, "sample_types", lambda: SEEK_TYPES + [{"id": 13, "title": "RNA"}])
    monkeypatch.setattr(sources, "sample_attributes", lambda: SEEK_ATTRS)
    monkeypatch.setattr(sources, "type_clades", lambda: {})


def render_page(path="/seek/admin/definitions/?type=BAC", sup=True):
    from seek.views.definitions import adminDefinitions
    with patch("seek.decorators._login", return_value={"status": True}):
        return adminDefinitions(_req(path, sup))


def test_a_superuser_sees_the_page_with_the_selected_type(catalog):
    resp = render_page()
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "<h1 class=\"ns-page-title\">Definitions</h1>" in html
    for tab in ("pane-sample-types", "pane-assays", "pane-attention", "pane-attributes", "pane-clades",
                "pane-assay-map"):
        assert f'id="{tab}"' in html
    assert 'value="Bacteria Sample"' in html and "bacteria, pathogen, MTB" in html
    assert 'data-field="Species"' in html and "SEEK required" in html      # UID is SEEK-required: no select
    assert "Library Prep <span" in html and "<li>BAC → D.BSRA</li>" in html   # seen, not designed, no definition
    assert "child_sampletypes: RNA" in html                                  # hand-typed, nothing backs it
    assert "RNA · no definition or data backs it" in html
    assert 'data-create="sample-type" data-key="A.ATAC"' in html
    assert 'data-create="assay" data-key="Library Prep"' in html
    assert 'Assay map "FC Receptor Binding Assay" vs definition <a href="?assay=5#assays">' in html


def test_an_assay_is_selected_by_row_id(catalog):
    html = render_page("/seek/admin/definitions/?assay=3").content.decode()
    assert 'data-save="assay" data-key="3"' in html and 'value="BAC or CEL"' in html
    assert "BAC → DNA" in html


def test_a_non_superuser_is_refused(catalog):
    resp = render_page(sup=False)
    body = resp.content.decode()
    assert "Bacteria Sample" not in body and "pane-sample-types" not in body
    assert '"status": 0' in body


def test_a_visitor_is_sent_to_sign_in():
    from seek.views.definitions import adminDefinitions
    with patch("seek.decorators._login", return_value={"status": False}):
        resp = adminDefinitions(_req("/seek/admin/definitions/"))
    assert resp.status_code == 302 and resp.url == "/login/?next=/seek/admin/definitions/"


@pytest.mark.parametrize("view, path, tab", [
    ("seek.views.admin.adminClades", "/seek/admin/clades/", "#clades"),
    ("seek.views.admin.internalAssays", "/seek/admin/internal_assays/", "#assay-map"),
    ("seek.views.samples.sampleAttributes", "/seek/samples/attributes/", "#attributes"),
])
def test_the_old_pages_send_a_superuser_to_their_tab(view, path, tab):
    import importlib
    module, name = view.rsplit(".", 1)
    with patch("seek.decorators._login", return_value={"status": True}):
        resp = getattr(importlib.import_module(module), name)(_req(path))
    assert resp.status_code == 302 and resp.url == "/seek/admin/definitions/" + tab


def test_framed_old_pages_render_and_allow_a_same_origin_frame():
    import seek.views.admin as admin
    import seek.views.samples as samples
    with patch("seek.decorators._login", return_value={"status": True}), \
            patch.object(admin, "DBtable_clades"), patch.object(admin, "DBtable_stc") as stc, \
            patch.object(admin, "render", return_value=HttpResponse("clades")):
        stc.return_value.getAllWithTitles.return_value = []
        resp = admin.adminClades(_req("/seek/admin/clades/?embed=1"))
    assert resp.status_code == 200 and resp.headers["X-Frame-Options"] == "SAMEORIGIN"

    with patch("seek.decorators._login", return_value={"status": True}), \
            patch.object(samples, "DBtable_sampletype", MagicMock()), \
            patch.object(samples, "DBtable_attributetype", MagicMock()), \
            patch.object(samples, "render", return_value=HttpResponse("attrs")):
        lab = samples.sampleAttributes(_req("/seek/samples/attributes/", sup=False))
    assert lab.status_code == 200          # a lab user keeps the read-only page: Definitions is superuser only


def test_the_route_is_registered():
    from django.urls import reverse
    assert reverse("adminDefinitions") == "/seek/admin/definitions/"



def test_with_the_switch_off_the_page_says_so_and_the_tier_card_shows_new_and_stale_titles(catalog, monkeypatch):
    monkeypatch.delenv("NEXTSEEK_GRAPH_SYNC_CONTEXT", raising=False)
    monkeypatch.setattr(sources, "sample_attributes", lambda: [a for a in SEEK_ATTRS if a["title"] != "Notes"] + [
        {"sample_type_id": 7, "title": "Barcode", "required": False}])
    html = render_page().content.decode()
    assert "NEXTSEEK_GRAPH_SYNC_CONTEXT=write" in html
    assert 'data-field="Barcode" data-new' in html and ">new<" in html
    assert "not in SEEK" in html and 'type="hidden" name="tier" data-field="Notes" value="possible"' in html
    monkeypatch.setenv("NEXTSEEK_GRAPH_SYNC_CONTEXT", "write")
    assert "NEXTSEEK_GRAPH_SYNC_CONTEXT=write" not in render_page().content.decode()


def test_the_save_script_reports_drf_detail_and_the_http_status(catalog):
    """A CSRF 403 answers {"detail": ...}, not JSON:API errors; a proxy error page is not JSON at all."""
    html = render_page().content.decode()
    assert "res.j.detail" in html and "'Not saved (HTTP ' + res.code + ')'" in html
    assert "r.json().catch(" in html
