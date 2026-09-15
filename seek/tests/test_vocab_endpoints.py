"""Endpoint contract for the association workbench.

These assert the shape the shared JS relies on, and the gating that keeps the
admin surface superuser-only.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("wb-admin", "wb@example.invalid", "pw-not-real")


@pytest.fixture
def plain_user(db):
    return User.objects.create_user("wb-plain", "wp@example.invalid", "pw-not-real")


def test_suggestions_route_resolves():
    assert reverse("internalAssaySuggestions") == "/seek/admin/internal_assays/suggestions"


@pytest.mark.django_db
def test_suggestions_rejects_non_superuser(plain_user, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(plain_user)
    resp = client.get("/seek/admin/internal_assays/suggestions")
    assert json.loads(resp.content)["status"] == 0


@pytest.mark.django_db
def test_suggestions_returns_envelope(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(
        views.DBtable_internalassays,
        "getAll",
        lambda self: [{"id": 74, "internal_assay_title": "Tissue Collection"}],
    )
    monkeypatch.setattr(
        views.DBtable_assaysinternalassays,
        "getAllWithTitles",
        lambda self: [
            {"assay_id": 1, "assay_title": "Tissue Collection - Metadata",
             "internal_assay_id": None, "internal_assay_title": None},
            {"assay_id": 2, "assay_title": "Tissue Collection - Metadata",
             "internal_assay_id": 74, "internal_assay_title": "Tissue Collection"},
        ],
    )
    client = Client()
    client.force_login(superuser)
    body = json.loads(client.get("/seek/admin/internal_assays/suggestions").content)

    assert body["status"] == 1
    # Only the unmapped row is offered a suggestion.
    assert set(body["suggestions"]) == {"1"}
    assert body["suggestions"]["1"][0]["tier"] == "exact"
    assert body["suggestions"]["1"][0]["vocabulary_id"] == 74


@pytest.mark.django_db
def test_suggestions_rejects_signed_out_user(plain_user, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(
        views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 0, "err": "Not signed in"}
    )
    client = Client()
    client.force_login(plain_user)
    resp = client.get("/seek/admin/internal_assays/suggestions")
    body = json.loads(resp.content)

    assert resp.status_code == 200
    assert body["status"] == 0
    assert body["suggestions"] == {}


@pytest.mark.django_db
def test_suggestions_degrades_on_data_layer_error(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})

    def boom(self):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(views.DBtable_internalassays, "getAll", boom)
    client = Client()
    client.force_login(superuser)
    resp = client.get("/seek/admin/internal_assays/suggestions")
    body = json.loads(resp.content)

    # The page must not 500 — it degrades to an empty suggestions envelope.
    assert resp.status_code == 200
    assert body["status"] == 0
    assert body["suggestions"] == {}


ASSOC_URL = "/seek/internal_assays/assayAssociation/save"


@pytest.mark.django_db
def test_association_save_rejects_get(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    assert client.get(ASSOC_URL).status_code == 405


@pytest.mark.django_db
def test_association_save_reports_updated_count(superuser, monkeypatch):
    import seek.views.admin as views

    seen = []
    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(
        views.DBtable_assaysinternalassays, "update",
        lambda self, a, i: seen.append((a, i)),
    )
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 1, "internal_assay_id": 74},
                                     {"assay_id": 2, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["status"] == 1
    assert body["updated"] == 2
    assert seen == [(1, 74), (2, 74)]


@pytest.mark.django_db
def test_association_save_collects_per_row_errors(superuser, monkeypatch):
    """A row removed by an intervening Sync must not 500 the batch."""
    import seek.views.admin as views

    def _boom(self, assay_id, internal_assay_id):
        raise views.Assays_internal_assays.DoesNotExist("gone")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_assaysinternalassays, "update", _boom)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 999, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["status"] == 0
    assert body["updated"] == 0
    assert "999" in json.dumps(body["errors"])


@pytest.mark.django_db
def test_association_save_commits_the_rows_that_worked(superuser, monkeypatch):
    """Partial success: one stale row must not undo its 38 healthy siblings."""
    import seek.views.admin as views

    def _one_bad(self, assay_id, internal_assay_id):
        if assay_id == 999:
            raise views.Assays_internal_assays.DoesNotExist("gone")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_assaysinternalassays, "update", _one_bad)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 1, "internal_assay_id": 74},
                                     {"assay_id": 999, "internal_assay_id": 74},
                                     {"assay_id": 2, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["status"] == 1
    assert body["updated"] == 2
    assert len(body["errors"]) == 1


@pytest.mark.django_db
def test_a_real_value_error_is_reported_with_its_type(superuser, monkeypatch):
    """No sentinel-exception control flow: a genuine ValueError is not a rollback."""
    import seek.views.admin as views

    def _raises(self, assay_id, internal_assay_id):
        raise ValueError("bad internal_assay_id")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_assaysinternalassays, "update", _raises)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 1, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["updated"] == 0
    assert "ValueError" in json.dumps(body["errors"])


SAVE_URL = "/seek/internal_assays/save"
DELETE_URL = "/seek/internal_assays/delete"


@pytest.mark.django_db
def test_internal_assay_save_rejects_get(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    assert client.get(SAVE_URL).status_code == 405


@pytest.mark.django_db
def test_internal_assay_save_reports_missing_title_and_commits_sibling(superuser, monkeypatch):
    """One bad row must not stop a valid sibling from committing."""
    import seek.views.admin as views

    created = []
    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(
        views.DBtable_internalassays, "new",
        lambda self, internal_assay_title: created.append(internal_assay_title),
    )
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        SAVE_URL,
        data=json.dumps({"records": [{"internal_assay_title": ""},
                                     {"internal_assay_title": "Tissue Collection"}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 1
    assert body["updated"] == 1
    assert len(body["errors"]) == 1
    assert "missing internal_assay_title" in json.dumps(body["errors"])
    assert created == ["Tissue Collection"]


@pytest.mark.django_db
def test_internal_assay_delete_rejects_get(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    assert client.get(DELETE_URL).status_code == 405


@pytest.mark.django_db
def test_internal_assay_delete_reports_missing_id(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        DELETE_URL,
        data=json.dumps({"records": [{}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 0
    assert body["updated"] == 0
    assert "missing id" in json.dumps(body["errors"])


@pytest.mark.django_db
def test_internal_assay_delete_reports_does_not_exist(superuser, monkeypatch):
    """The row is already gone (e.g. deleted from another tab) — reported, not a 500."""
    import seek.views.admin as views

    def _boom(self, internal_assay_id):
        raise views.Internal_assays.DoesNotExist("gone")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_internalassays, "delete", _boom)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        DELETE_URL,
        data=json.dumps({"records": [{"id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 0
    assert body["updated"] == 0
    assert "no longer exists" in json.dumps(body["errors"])


@pytest.mark.django_db
def test_association_save_rejects_non_dict_json_body(superuser, monkeypatch):
    """A syntactically valid but non-object JSON body must not 500."""
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    resp = client.post(ASSOC_URL, data=json.dumps([1, 2, 3]), content_type="application/json")
    body = json.loads(resp.content)

    assert resp.status_code == 200
    assert body["status"] == 0


@pytest.mark.django_db
def test_association_save_rejects_empty_records(superuser, monkeypatch):
    """Nothing was written, so an empty batch is not reported as a success."""
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": []}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 0
    assert body["updated"] == 0
    assert body["errors"] == []


CLADE_ASSOC_URL = "/seek/clade/sampleTypes/save/"


@pytest.mark.django_db
def test_clade_sample_types_save_rejects_get(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    assert client.get(CLADE_ASSOC_URL).status_code == 405


@pytest.mark.django_db
def test_clade_sample_types_save_returns_same_envelope(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    # admin.py aliases the class: `from dmac.dbtable_sampletypesclades import
    # DBtable_sample_types_clades as DBtable_stc` (seek/views/admin.py).
    monkeypatch.setattr(views.DBtable_stc, "update", lambda self, s, c: None)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        CLADE_ASSOC_URL,
        data=json.dumps({"records": [{"sample_type_id": 3, "clade_id": 9}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert set(body) == {"status", "msg", "updated", "errors"}
    assert body["status"] == 1 and body["updated"] == 1


CLADE_SAVE_URL = "/seek/clade/save/"


@pytest.mark.django_db
def test_clade_save_defaults_missing_order_to_zero(superuser, monkeypatch):
    """DBtable_clades.new/update both do int(order), so a missing or empty
    order would raise TypeError rather than saving a null unless the view
    defaults it first."""
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    captured = {}

    def fake_new(self, title, color, order):
        captured["order"] = order

    monkeypatch.setattr(views.DBtable_clades, "new", fake_new)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        CLADE_SAVE_URL,
        data=json.dumps({"records": [{"title": "Glioma", "color": "#fff", "order": ""}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 1 and body["updated"] == 1
    assert captured["order"] == 0


CLADE_DELETE_URL = "/seek/clade/delete/"


@pytest.mark.django_db
def test_delete_message_reads_as_a_deletion(superuser, monkeypatch):
    """_wb_batch is shared with the save endpoints; without a verb of its own a
    successful delete reported 'Saved 1 clade deletion(s).'"""
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_clades, "delete", lambda self, clade_id: None)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        CLADE_DELETE_URL,
        data=json.dumps({"records": [{"id": 3}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 1 and body["updated"] == 1
    assert body["msg"] == "Deleted 1 clade(s)."


@pytest.mark.django_db
def test_partial_delete_message_reads_as_a_deletion(superuser, monkeypatch):
    """Partial-success copy takes the same verb, and the semantics are
    unchanged: status 1 because one record was written."""
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_internalassays, "delete", lambda self, internal_assay_id: None)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        DELETE_URL,
        data=json.dumps({"records": [{"id": 74}, {}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["status"] == 1 and body["updated"] == 1
    assert body["msg"] == "Deleted 1 internal assay(s); 1 failed."


@pytest.mark.django_db
def test_save_message_still_reads_as_a_save(superuser, monkeypatch):
    import seek.views.admin as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(
        views.DBtable_internalassays, "new",
        lambda self, internal_assay_title: None,
    )
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        SAVE_URL,
        data=json.dumps({"records": [{"internal_assay_title": "Tissue Collection"}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)

    assert body["msg"] == "Saved 1 internal assay(s)."
