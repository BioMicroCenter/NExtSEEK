"""The graph_sync hooks on the SEEK proxies and the users API (spec 5, C-17; plan task H4; CI-6).

Rails commits the row, the proxy validates what came back, and then one outbox row says what the graph owes: the
sample it wrote, the catalog a sample type moved, the maps an assay or a SOP moved, the ISA nodes, the memberships.
Nothing here calls Neo4j, and nothing here may turn a committed SEEK write into an error, so every method is pinned
three ways: the row on a 2xx, no row on a 4xx or a 5xx, and a failing enqueue that never reaches the caller.

The proxies hold their upstream client as a class attribute (`nextseek_api/CLAUDE.md`: one SEEK session shared by
every caller), so each test replaces it on its own instance rather than patching the class.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from nextseek_api.graph_sync import state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.models import UserAdminRecord
from nextseek_api.services.assays import AssayProxyViewSet
from nextseek_api.services.investigations import InvestigationProxyViewSet
from nextseek_api.services.projects import ProjectProxyViewSet
from nextseek_api.services.sample_types import SampleTypeProxyViewSet
from nextseek_api.services.samples import SampleProxyViewSet
from nextseek_api.services.sops import SopProxyViewSet
from nextseek_api.services.studies import StudyProxyViewSet
from nextseek_api.services.users import UsersViewSet

pytestmark = pytest.mark.django_db

JSON_H = {"Content-Type": "application/json"}

_EMPTY_REF = {"data": []}
_STUDY_REF = {"data": {"type": "studies", "id": "434"}}


def _rows() -> set[tuple[str, str]]:
    """Every outbox row, as (kind, key)."""
    return set(GraphSyncOutbox.objects.values_list("kind", "key"))


def _user() -> MagicMock:
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = True
    return user


def _request(method: str, body: Any = None) -> Request:
    factory = getattr(APIRequestFactory(), method)
    raw = factory("/", data=body, format="json") if body is not None else factory("/")
    request = Request(raw, parsers=[JSONParser(), FormParser(), MultiPartParser()])
    request.user = _user()
    return request


def _upstream(payload: Any, code: int = 200) -> tuple:
    return (json.dumps(payload).encode(), code, JSON_H, MagicMock())


def _broken_enqueue(monkeypatch) -> None:
    """The dmac database is unreachable: state.enqueue raises, and hooks.enqueue must swallow it."""
    def raise_it(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", raise_it)


# ---------------------------------------------------------------------------------------------------
# The seven JSON:API proxies: one table, three tests
# ---------------------------------------------------------------------------------------------------

def _sample_body():
    return {
        "data": {
            "id": "321", "type": "samples",
            "attributes": {"title": "Sample A"},
            "relationships": {
                "sample_type": {"data": {"type": "sample_types", "id": "12"}},
                "creators": _EMPTY_REF, "projects": _EMPTY_REF, "people": _EMPTY_REF,
                "assays": _EMPTY_REF, "data_files": _EMPTY_REF,
            },
            "links": {"self": "/samples/321"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }


def _sample_type_body():
    return {
        "data": {
            "id": "12", "type": "sample_types",
            "attributes": {"title": "TIS"},
            "relationships": {
                "submitter": _EMPTY_REF, "projects": _EMPTY_REF,
                "assays": _EMPTY_REF, "samples": _EMPTY_REF,
            },
            "links": {"self": "/sample_types/12"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }


def _assay_body():
    return {
        "data": {
            "id": "351", "type": "assays",
            "attributes": {"title": "Assay 1"},
            "relationships": {
                "creators": _EMPTY_REF, "submitter": _EMPTY_REF, "organisms": _EMPTY_REF,
                "people": _EMPTY_REF, "projects": _EMPTY_REF, "investigation": _STUDY_REF,
                "study": _STUDY_REF, "data_files": _EMPTY_REF, "samples": _EMPTY_REF,
                "documents": _EMPTY_REF, "models": _EMPTY_REF, "sops": _EMPTY_REF,
                "publications": _EMPTY_REF, "placeholders": _EMPTY_REF, "human_diseases": _EMPTY_REF,
            },
            "links": {"self": "/assays/351"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }


def _study_body():
    return {
        "data": {
            "id": "746", "type": "studies",
            "attributes": {"title": "Vaccine Dose Response"},
            "relationships": {"investigation": {"data": {"id": "763", "type": "investigations"}}},
            "links": {"self": "/studies/746"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }


def _investigation_body():
    return {
        "data": {
            "id": "763", "type": "investigations",
            "attributes": {"title": "Investigation 1"},
            "relationships": {"projects": _EMPTY_REF},
            "links": {"self": "/investigations/763"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }


def _project_body():
    return {
        "data": {
            "id": "2558", "type": "projects",
            "attributes": {"title": "Project 1"},
            "relationships": {"people": _EMPTY_REF},
            "links": {"self": "/projects/2558"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }


@dataclass(frozen=True)
class Proxy:
    """One proxy method: how it is called, what SEEK answers, and the rows the hook owes."""

    id: str
    viewset: type
    action: str
    client_method: str
    request_body: dict
    response_body: dict
    rows: frozenset
    method: str = "post"
    kwargs: dict = field(default_factory=dict)
    ok_code: int = 200

    def call(self, code: int | None = None, body: Any = None):
        viewset = self.viewset()
        viewset.client = MagicMock()
        answer = _upstream(self.response_body if body is None else body, code or self.ok_code)
        getattr(viewset.client, self.client_method).return_value = answer
        request = _request(self.method, self.request_body)
        return getattr(viewset, self.action)(request, **self.kwargs)


PROXIES = [
    Proxy(
        id="sample.create", viewset=SampleProxyViewSet, action="create", client_method="create_sample",
        request_body={"data": {"type": "samples", "attributes": {"title": "New"},
                               "relationships": {"sample_type": {"data": {"type": "sample_types", "id": "12"}}}}},
        response_body=_sample_body(), rows=frozenset({("samples", "sample:321")}), ok_code=201,
    ),
    Proxy(
        id="sample.partial_update", viewset=SampleProxyViewSet, action="partial_update",
        client_method="update_sample", method="patch", kwargs={"uid": "321"},
        request_body={"data": {"type": "samples", "id": "321", "attributes": {"title": "Revised"}}},
        response_body=_sample_body(), rows=frozenset({("samples", "sample:321")}),
    ),
    Proxy(
        id="sample_type.create", viewset=SampleTypeProxyViewSet, action="create",
        client_method="create_sample_type",
        request_body={"data": {"type": "sample_types", "attributes": {
            "title": "NEW",
            "sample_attributes": [{"title": "Title", "sample_attribute_type": {"id": "1"}, "required": True}]},
            "relationships": {"projects": {"data": [{"type": "projects", "id": "1"}]}}}},
        response_body=_sample_type_body(),
        rows=frozenset({("catalog", "*"), ("samples_of_type", "type:12")}), ok_code=201,
    ),
    Proxy(
        id="sample_type.partial_update", viewset=SampleTypeProxyViewSet, action="partial_update",
        client_method="update_sample_type", method="patch", kwargs={"uid": "12"},
        request_body={"data": {"id": "12", "type": "sample_types", "attributes": {"title": "Revised"}}},
        response_body=_sample_type_body(),
        rows=frozenset({("catalog", "*"), ("samples_of_type", "type:12")}),
    ),
    Proxy(
        id="assay.create", viewset=AssayProxyViewSet, action="create", client_method="create_assay",
        request_body={"data": {"type": "assays", "attributes": {
            "title": "New Assay", "assay_class": {"key": "EXP"},
            "assay_type": {"uri": "http://jermontology.org/ontology/JERMOntology#Transcriptomics"}},
            "relationships": {"study": {"data": {"type": "studies", "id": "434"}}}}},
        response_body=_assay_body(), rows=frozenset({("assay_map", "*"), ("isa", "*")}), ok_code=201,
    ),
    Proxy(
        id="assay.partial_update", viewset=AssayProxyViewSet, action="partial_update",
        client_method="update_assay", method="patch", kwargs={"uid": "351"},
        request_body={"data": {"type": "assays", "id": "351", "attributes": {"description": "Updated"}}},
        response_body=_assay_body(), rows=frozenset({("assay_map", "*"), ("isa", "*")}),
    ),
    Proxy(
        id="study.create", viewset=StudyProxyViewSet, action="create", client_method="create_study",
        request_body={"data": {"type": "studies", "attributes": {"title": "Vaccine Dose Response"},
                               "relationships": {"investigation": {"data": {"id": "763",
                                                                            "type": "investigations"}}}}},
        response_body=_study_body(), rows=frozenset({("isa", "*")}), ok_code=201,
    ),
    Proxy(
        id="study.partial_update", viewset=StudyProxyViewSet, action="partial_update",
        client_method="update_study", method="patch", kwargs={"uid": "746"},
        request_body={"data": {"type": "studies", "id": "746", "attributes": {"title": "Revised"}}},
        response_body=_study_body(), rows=frozenset({("isa", "*")}),
    ),
    Proxy(
        id="investigation.create", viewset=InvestigationProxyViewSet, action="create",
        client_method="create_investigation",
        request_body={"data": {"type": "investigations", "attributes": {"title": "New Investigation"},
                               "relationships": {"projects": {"data": [{"type": "projects", "id": "4475"}]}}}},
        response_body=_investigation_body(), rows=frozenset({("isa", "*")}), ok_code=201,
    ),
    Proxy(
        id="investigation.partial_update", viewset=InvestigationProxyViewSet, action="partial_update",
        client_method="update_investigation", method="patch", kwargs={"uid": "763"},
        request_body={"data": {"type": "investigations", "id": "763", "attributes": {"title": "Updated"}}},
        response_body=_investigation_body(), rows=frozenset({("isa", "*")}),
    ),
    Proxy(
        id="project.create", viewset=ProjectProxyViewSet, action="create", client_method="create_project",
        request_body={"data": {"type": "projects", "attributes": {"title": "New Project"}}},
        response_body=_project_body(), rows=frozenset({("isa", "*")}), ok_code=201,
    ),
    Proxy(
        id="project.partial_update", viewset=ProjectProxyViewSet, action="partial_update",
        client_method="update_project", method="patch", kwargs={"uid": "2558"},
        request_body={"data": {"type": "projects", "id": "2558", "attributes": {"title": "Updated"}}},
        response_body=_project_body(), rows=frozenset({("isa", "*")}),
    ),
]

_BY_ID = {proxy.id: proxy for proxy in PROXIES}


@pytest.mark.parametrize("proxy", PROXIES, ids=[p.id for p in PROXIES])
def test_a_2xx_enqueues_the_rows_the_element_table_names(proxy):
    response = proxy.call()
    assert response.status_code == proxy.ok_code
    assert _rows() == set(proxy.rows)


@pytest.mark.parametrize("proxy", PROXIES, ids=[p.id for p in PROXIES])
@pytest.mark.parametrize("code", [400, 404, 409, 422, 500, 502])
def test_a_4xx_or_a_5xx_enqueues_nothing(proxy, code):
    """SEEK refused the write, so the graph owes nothing."""
    proxy.call(code=code)
    assert _rows() == set()


@pytest.mark.parametrize("proxy", PROXIES, ids=[p.id for p in PROXIES])
def test_an_unreadable_response_enqueues_nothing(proxy):
    """A 2xx whose body does not validate is answered 502: the proxy never learned what SEEK wrote."""
    response = proxy.call(body={"data": {"id": "1"}})
    assert response.status_code == 502
    assert _rows() == set()


@pytest.mark.parametrize("proxy", PROXIES, ids=[p.id for p in PROXIES])
def test_a_failed_enqueue_never_reaches_the_caller(proxy, monkeypatch):
    """The write is committed in SEEK: a dead outbox must not turn it into an error."""
    _broken_enqueue(monkeypatch)
    response = proxy.call()
    assert response.status_code == proxy.ok_code
    assert _rows() == set()


def test_the_sample_hooks_key_on_the_id_seek_returned_not_the_path():
    """A patch by UID resolves to one id upstream; the row must name the sample SEEK actually wrote."""
    proxy = _BY_ID["sample.partial_update"]
    viewset = SampleProxyViewSet()
    viewset.client = MagicMock()
    viewset.client.update_sample.return_value = _upstream(proxy.response_body, 200)
    request = _request("patch", {"data": {"type": "samples", "id": "321", "attributes": {"title": "Revised"}}})
    with patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value="321"):
        response = viewset.partial_update(request, uid="NHP-220630FLY-1")
    assert response.status_code == 200
    assert _rows() == {("samples", "sample:321")}


# ---------------------------------------------------------------------------------------------------
# The sample proxy's destroy: the retire rule (spec 9, E17)
# ---------------------------------------------------------------------------------------------------

def _destroy(code: int = 200):
    viewset = SampleProxyViewSet()
    viewset.client = MagicMock()
    viewset.client.delete_sample.return_value = (b'{"status":"ok"}', code, JSON_H, MagicMock())
    return viewset.destroy(_request("delete"), uid="321")


def test_destroy_enqueues_a_retire_row():
    assert _destroy().status_code == 200
    assert _rows() == {("retire", "sample:321")}


@pytest.mark.parametrize("code", [403, 404, 422])
def test_destroy_enqueues_nothing_when_seek_refuses(code):
    _destroy(code=code)
    assert _rows() == set()


def test_destroy_survives_a_failed_enqueue(monkeypatch):
    _broken_enqueue(monkeypatch)
    assert _destroy().status_code == 200
    assert _rows() == set()


# When the proxy cannot confirm the outcome, it still enqueues the retire. That is safe because
# targeted.retire_samples reads MySQL again and leaves alone an id MySQL still holds.

@pytest.mark.parametrize("code", [500, 502, 503])
def test_destroy_enqueues_a_retire_when_seek_answers_with_a_server_error(code):
    """SEEK answered, so its work is over: whether the row went is settled, and the retire may run at once."""
    response = _destroy(code=code)
    assert response.status_code == code
    assert _rows() == {("retire", "sample:321")}
    assert GraphSyncOutbox.objects.get(kind="retire").lease_expires_at is None


def _destroy_raising(error: Exception):
    viewset = SampleProxyViewSet()
    viewset.client = MagicMock()
    viewset.client.delete_sample.side_effect = error
    return viewset.destroy(_request("delete"), uid="321")


def _assert_a_delayed_retire() -> None:
    from nextseek_api.services.samples import UNCONFIRMED_RETIRE_DELAY_S

    assert _rows() == {("retire", "sample:321")}
    r = GraphSyncOutbox.objects.get(kind="retire", key="sample:321")
    assert r.lease_expires_at is not None
    assert abs((r.lease_expires_at - r.enqueued_at).total_seconds() - UNCONFIRMED_RETIRE_DELAY_S) < 5
    # Not before SEEK has had time to finish the delete it may still be running.
    assert state.claim_next("w1", now=r.enqueued_at) is None
    assert state.claim_next("w1", now=r.lease_expires_at).key == "sample:321"


def test_destroy_answers_202_and_enqueues_a_delayed_retire_when_seek_outruns_the_timeout():
    """Measured: SEEK's own delete outruns SeekAPIClient.timeout_s, and Rails completes it after the proxy gave up."""
    import requests

    response = _destroy_raising(requests.ReadTimeout("read timed out"))
    assert response.status_code == 202
    body = json.loads(response.content)
    assert body["status"] == "unconfirmed"
    assert "did not answer" in body["detail"]
    _assert_a_delayed_retire()


def test_destroy_answers_502_and_enqueues_a_delayed_retire_when_seek_cannot_be_reached():
    import requests

    response = _destroy_raising(requests.ConnectionError("connection reset by peer"))
    assert response.status_code == 502
    error = json.loads(response.content)["errors"][0]
    assert error["title"] == "Upstream connection error"
    assert "may or may not" in error["detail"]
    _assert_a_delayed_retire()


def test_destroy_survives_a_failed_enqueue_after_a_timeout(monkeypatch):
    import requests

    _broken_enqueue(monkeypatch)
    assert _destroy_raising(requests.ReadTimeout("read timed out")).status_code == 202
    assert _rows() == set()


def test_destroy_enqueues_nothing_for_an_unresolvable_uid():
    viewset = SampleProxyViewSet()
    viewset.client = MagicMock()
    with patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value=None):
        response = viewset.destroy(_request("delete"), uid="NOT-A-SAMPLE")
    assert response.status_code == 404
    assert _rows() == set()
    viewset.client.delete_sample.assert_not_called()


# ---------------------------------------------------------------------------------------------------
# The SOP proxy: the protocol map (spec 5 E9)
# ---------------------------------------------------------------------------------------------------

_SOP_METADATA = {
    "data": {
        "type": "sops",
        "attributes": {"title": "My SOP"},
        "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
    }
}


def _sop_body(sop_id="42"):
    return {
        "data": {
            "id": sop_id, "type": "sops",
            "attributes": {"title": "Test SOP", "content_blobs": []},
            "relationships": {
                "creators": _EMPTY_REF, "submitter": _EMPTY_REF, "people": _EMPTY_REF,
                "projects": _EMPTY_REF, "investigations": _EMPTY_REF, "studies": _EMPTY_REF,
                "assays": _EMPTY_REF, "publications": _EMPTY_REF, "workflows": _EMPTY_REF,
            },
            "links": {"self": f"/sops/{sop_id}"},
            "meta": {"created": "2026-01-01", "modified": "2026-01-01", "uuid": "abc"},
        },
        "jsonapi": {"version": "1.0"},
    }


def _sop_multipart(metadata: dict) -> Request:
    """The create path reads its JSON out of a multipart ``metadata`` field."""
    raw = APIRequestFactory().post("/", {"metadata": json.dumps(metadata)}, format="multipart")
    request = Request(raw, parsers=[parser() for parser in SopProxyViewSet.parser_classes])
    request.user = _user()
    return request


def _sop_json(body: dict) -> Request:
    """The patch path validates ``request.data`` itself, so the request body is the metadata."""
    raw = APIRequestFactory().patch("/", body, format="json")
    request = Request(raw, parsers=[parser() for parser in SopProxyViewSet.parser_classes])
    request.user = _user()
    return request


def _sop_create(code: int = 201, body: Any = None):
    """The JSON-only create. Its success path raises UnboundLocalError before it can answer (spec 20: this create
    returns 500 after Rails has committed), so the hook is what must have run by then."""
    viewset = SopProxyViewSet()
    viewset.client = MagicMock()
    viewset.client.create_sop.return_value = _upstream(_sop_body() if body is None else body, code)
    try:
        return viewset.create(_sop_multipart(_SOP_METADATA))
    except UnboundLocalError:
        return None


def test_sop_create_enqueues_the_protocol_map():
    _sop_create()
    assert _rows() == {("protocol_map", "*")}


@pytest.mark.parametrize("code", [400, 404, 422, 500])
def test_sop_create_enqueues_nothing_when_seek_refuses(code):
    _sop_create(code=code)
    assert _rows() == set()


def test_sop_create_enqueues_nothing_on_an_unreadable_response():
    response = _sop_create(body={"data": {"id": "1"}})
    assert response is not None and response.status_code == 502
    assert _rows() == set()


def test_sop_create_survives_a_failed_enqueue(monkeypatch):
    _broken_enqueue(monkeypatch)
    _sop_create()
    assert _rows() == set()


def _sop_patch(code: int = 200, body: Any = None):
    viewset = SopProxyViewSet()
    viewset.client = MagicMock()
    viewset.client.update_sop.return_value = _upstream(_sop_body() if body is None else body, code)
    metadata = {"data": {"type": "sops", "id": "42", "attributes": {"title": "Patched"}}}
    with patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42"):
        return viewset.partial_update(_sop_json(metadata), uid="42")


def test_sop_patch_enqueues_the_protocol_map():
    assert _sop_patch().status_code == 200
    assert _rows() == {("protocol_map", "*")}


@pytest.mark.parametrize("code", [400, 404, 422, 500])
def test_sop_patch_enqueues_nothing_when_seek_refuses(code):
    _sop_patch(code=code)
    assert _rows() == set()


def test_sop_patch_enqueues_nothing_on_an_unreadable_response():
    assert _sop_patch(body={"data": {"id": "1"}}).status_code == 502
    assert _rows() == set()


def test_sop_patch_survives_a_failed_enqueue(monkeypatch):
    _broken_enqueue(monkeypatch)
    assert _sop_patch().status_code == 200
    assert _rows() == set()


# ---------------------------------------------------------------------------------------------------
# The users admin API: memberships (spec 5 E13)
# ---------------------------------------------------------------------------------------------------

_CREATE_BODY = {
    "login": "testuser",
    "password": "testpassword",
    "password_confirmation": "testpassword",
    "email": "testuser@example.com",
    "first_name": "Test",
    "last_name": "User",
    "project_id": 1,
    "institution_id": 1,
    "is_superuser": False,
    "activate": True,
}


def _record(**overrides) -> UserAdminRecord:
    values = dict(
        user_id=10, person_id=20, login="testuser", email="testuser@example.com",
        first_name="Test", last_name="User", active=True, django_is_active=True,
        django_is_superuser=False, project_id=1, institution_id=1,
    )
    values.update(overrides)
    return UserAdminRecord(**values)


def _user_create(*, runner_error: Exception | None = None):
    seek_user = MagicMock(id=10, person_id=20, login="testuser", activation_code=None)
    with patch("nextseek_api.services.users._sync_django_user"), \
         patch("nextseek_api.services.users._upsert_people_mirror"), \
         patch("nextseek_api.services.users.Users") as users, \
         patch("nextseek_api.services.users.run_seek_rails_runner") as runner, \
         patch("nextseek_api.services.users._build_record", return_value=_record()):
        users.objects.using.return_value.filter.return_value.exists.return_value = False
        users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        if runner_error is not None:
            runner.side_effect = runner_error
        else:
            runner.return_value = {"ok": True, "user_id": 10, "person_id": 20, "login": "testuser",
                                   "project_id": 1, "institution_id": 1}
        request = _request("post", _CREATE_BODY)
        return UsersViewSet().create(request)


def test_users_create_enqueues_a_membership_row():
    assert _user_create().status_code == 201
    assert _rows() == {("membership", "*")}


def test_users_create_enqueues_nothing_when_seek_refuses():
    from nextseek_api.services.seek_rails_runner import SeekRailsRunnerError

    response = _user_create(runner_error=SeekRailsRunnerError("login has already been taken"))
    assert response.status_code == 409
    assert _rows() == set()


def test_users_create_enqueues_nothing_on_an_invalid_body():
    response = UsersViewSet().create(_request("post", dict(_CREATE_BODY, password_confirmation="wrong")))
    assert response.status_code == 422
    assert _rows() == set()


def test_users_create_survives_a_failed_enqueue(monkeypatch):
    _broken_enqueue(monkeypatch)
    assert _user_create().status_code == 201
    assert _rows() == set()


def _user_patch(body: dict | None = None, *, uid: str = "5"):
    seek_user = MagicMock(id=5, person_id=6, login="demo", activation_code=None)
    with patch("nextseek_api.services.users._build_record", return_value=_record(user_id=5, person_id=6)), \
         patch("nextseek_api.services.users.People") as people, \
         patch("nextseek_api.services.users.DjangoUser") as django_user, \
         patch("nextseek_api.services.users.run_seek_rails_runner") as runner, \
         patch("nextseek_api.services.users.Users") as users:
        users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        people.objects.using.return_value.get.return_value = MagicMock(
            email="d@example.com", first_name="D", last_name="E")
        django_user.objects.filter.return_value.first.return_value = MagicMock()
        runner.return_value = {"ok": True, "user_id": 5, "person_id": 6, "login": "demo",
                               "project_id": 1, "institution_id": 1}
        request = _request("patch", body if body is not None else {"active": False})
        return UsersViewSet().partial_update(request, uid=uid)


def test_users_patch_enqueues_a_membership_row():
    assert _user_patch().status_code == 200
    assert _rows() == {("membership", "*")}


def test_users_patch_enqueues_nothing_for_an_unknown_user():
    assert _user_patch(uid="not-a-number").status_code == 404
    assert _rows() == set()


def test_users_patch_enqueues_nothing_on_an_invalid_body():
    assert _user_patch(body={}).status_code == 422
    assert _rows() == set()


def test_users_patch_survives_a_failed_enqueue(monkeypatch):
    _broken_enqueue(monkeypatch)
    assert _user_patch().status_code == 200
    assert _rows() == set()


# ---------------------------------------------------------------------------------------------------
# What has no graph effect (spec 5: registered in T18a as NO_GRAPH_EFFECT)
# ---------------------------------------------------------------------------------------------------

def test_a_read_enqueues_nothing():
    """Only the write methods hook: a retrieve must leave the outbox empty."""
    viewset = AssayProxyViewSet()
    viewset.client = MagicMock()
    viewset.client.get_assay.return_value = _upstream(_assay_body(), 200)
    assert viewset.retrieve(_request("get"), uid="351").status_code == 200
    assert _rows() == set()
