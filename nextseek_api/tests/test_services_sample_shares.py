"""The sample-shares endpoint (the studies tool's share mode): auth, gates, bodies, status codes, schema."""
import base64
import contextlib
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from nextseek_api.models import SampleShareRequest
from nextseek_api.services import sample_shares as view
from nextseek_api.studies import share as sh
from nextseek_api.studies import share_jobs
from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.seek import SeekRefused
from nextseek_api.studies.share_apply import StepAnswer

URL = "/nextseek_api/sample-shares/"
BODY = {"sample_uids": ["TIS-230324BOO-39-PUB"], "source_project_id": 1, "destination_project_id": 2558,
        "destination_study_id": 746}
UNKNOWN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def superuser(db):
    return User.objects.create_user("admin", password="x", is_staff=True, is_superuser=True)


@pytest.fixture
def staff_only(db):
    return User.objects.create_user("lab", password="x", is_staff=True, is_superuser=False)


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _planned(user, state="planned"):
    row = share_jobs.create_share(ShareInput(**BODY, created_at="t"), user)
    row.state, row.plan_sha256, row.run_dir = state, "a" * 64, "20260101T000000Z-share"
    row.summary = {"outcomes": {"shared": 1}}
    row.save()
    return row


@pytest.mark.django_db
@pytest.mark.parametrize("method, path", [("post", URL), ("get", URL + UNKNOWN + "/"),
                                          ("post", URL + UNKNOWN + "/apply/")])
def test_anonymous_is_401_with_a_basic_challenge(method, path):
    response = getattr(APIClient(), method)(path, {}, format="json")
    assert response.status_code == 401 and response["WWW-Authenticate"].startswith("Basic")


@pytest.mark.parametrize("method, path", [("post", URL), ("get", URL + UNKNOWN + "/"),
                                          ("post", URL + UNKNOWN + "/apply/")])
def test_a_non_superuser_is_403(staff_only, method, path):
    assert getattr(_client(staff_only), method)(path, {}, format="json").status_code == 403


@pytest.mark.parametrize("change", [{"sample_uids": []}, {"sample_uids": ["X"] * 2},
                                   {"sample_uids": [f"TIS-230324BOO-{n}" for n in range(10_001)]},
                                   {"sample_uids": ["  "]}, {"colour": "blue"}, {"destination_study_id": 0}])
def test_a_bad_body_is_422(superuser, change):
    response = _client(superuser).post(URL, {**BODY, **change}, format="json")
    assert response.status_code == 422 and response.json()["errors"][0]["title"] == "invalid_request"


def test_the_uid_limit_is_the_share_modes():
    assert SampleShareRequest.model_fields["sample_uids"].metadata[1].max_length == sh.MAX_SHARE_UIDS


def test_create_is_202_with_a_status_url_and_a_planning_row(superuser):
    response = _client(superuser).post(URL, BODY, format="json")
    body = response.json()
    assert response.status_code == 202 and body["state"] == "planning"
    assert body["status_url"] == f"/nextseek_api/sample-shares/{body['share_id']}/"
    assert share_jobs.next_claimable().request["destination_study_id"] == 746


@pytest.mark.parametrize("share_id", [UNKNOWN, "not-a-uuid"])
def test_an_unknown_or_malformed_id_is_404(superuser, share_id):
    assert _client(superuser).get(URL + share_id + "/").status_code == 404
    assert _client(superuser).post(URL + share_id + "/apply/", {"plan_sha256": "a" * 64},
                                   format="json").status_code == 404


def test_a_planned_share_reads_its_summary_and_sha(superuser):
    row = _planned(superuser)
    body = _client(superuser).get(URL + str(row.share_id) + "/").json()
    assert (body["state"], body["plan_sha256"], body["uid_count"], body["summary"]) == (
        "planned", "a" * 64, 1, {"outcomes": {"shared": 1}})
    assert body["graph"] is None


def test_verify_graph_adds_the_check(superuser):
    row = _planned(superuser)
    with patch.object(view, "_graph_check", return_value={"found": 1, "outbox": "done"}) as check:
        body = _client(superuser).get(URL + str(row.share_id) + "/?verify=graph").json()
    assert body["graph"] == {"found": 1, "outbox": "done"} and check.call_args.args[0].pk == row.pk


def test_apply_without_a_seek_credential_is_401(superuser):
    row = _planned(superuser)
    response = _client(superuser).post(URL + str(row.share_id) + "/apply/", {"plan_sha256": "a" * 64},
                                       format="json")
    assert response.status_code == 401 and response.json()["errors"][0]["title"] == "seek_credential_missing"


def _apply(user, row, *, prove=None, answer=None):
    header = "Basic " + base64.b64encode(b"admin:x").decode("ascii")
    client = _client(user)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(view.SeekSession, "prove_for", prove or (lambda self, u: self)))
        stack.enter_context(patch.object(view, "_graph", lambda: contextlib.nullcontext((None, "neo4j"))))
        step = stack.enter_context(patch.object(view.share_apply, "apply_step", return_value=answer))
        response = client.post(URL + str(row.share_id) + "/apply/", {"plan_sha256": "a" * 64}, format="json",
                               HTTP_AUTHORIZATION=header)
    return response, step


def test_apply_with_another_users_seek_login_is_403(superuser):
    row = _planned(superuser)

    def mismatch(self, user):
        raise SeekRefused("seek_identity_mismatch", "not yours")

    response, step = _apply(superuser, row, prove=mismatch)
    assert response.status_code == 403 and response.json()["errors"][0]["title"] == "seek_identity_mismatch"
    step.assert_not_called()


@pytest.mark.parametrize("answer, status, code", [
    (StepAnswer(200, "applying", 1, 1), 200, None),
    (StepAnswer(202, "applying", 0, 1, retry_after_s=10, code="clone_outcome_unknown"), 202, "clone_outcome_unknown"),
    (StepAnswer(202, "queued", 2, 0), 202, None),
    (StepAnswer(409, "planned", code="busy", message="held"), 409, "busy"),
    (StepAnswer(403, "applying", code="seek_refused", message="no"), 403, "seek_refused"),
    (StepAnswer(422, "applying", code="seek_payload_rejected", message="bad"), 422, "seek_payload_rejected"),
    (StepAnswer(502, "applying", code="seek_error", message="down"), 502, "seek_error"),
])
def test_each_step_answer_maps_to_its_status_and_body(superuser, answer, status, code):
    row = _planned(superuser)
    response, step = _apply(superuser, row, answer=answer)
    assert response.status_code == status
    body = response.json()
    if status >= 400:
        assert body["errors"][0]["title"] == code
    else:
        assert (body["clones_done"], body["clones_remaining"], body["code"]) == (
            answer.clones_done, answer.clones_remaining, code)
    assert step.call_args.kwargs["plan_sha256"] == "a" * 64


def test_a_bad_apply_body_is_422(superuser):
    row = _planned(superuser)
    response = _client(superuser).post(URL + str(row.share_id) + "/apply/", {"plan_sha256": "nope"}, format="json")
    assert response.status_code == 422


def test_the_three_paths_and_their_models_are_in_the_schema():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    paths = schema["paths"]
    assert "post" in paths["/nextseek_api/sample-shares/"]
    assert "get" in paths["/nextseek_api/sample-shares/{share_id}/"]
    assert "post" in paths["/nextseek_api/sample-shares/{share_id}/apply/"]
    components = schema["components"]["schemas"]
    for model in ("SampleShareRequest", "SampleShareApplyRequest", "SampleShareAccepted", "SampleShareStep",
                  "SampleShareStatus"):
        assert model in components, model


def test_the_graph_check_reads_every_outbox_row_of_the_unit_and_reports_the_worst(db, monkeypatch):
    from django.utils import timezone

    from nextseek_api.graph_sync import state as outbox_state
    from nextseek_api.graph_sync.models_db import GraphSyncOutbox
    from nextseek_api.studies import links
    from nextseek_api.studies.tests.conftest import U3, FakeReader, share_world

    monkeypatch.setattr(links, "SAMPLE_CHUNK", 1)          # the unit's two samples: two rows, as over 5,000 ids
    plan = sh.plan_share(ShareInput(sample_uids=[U3], source_project_id=3, destination_project_id=5,
                                    destination_study_id=40, created_at="t"), FakeReader(share_world()),
                         run_id="r", now="t")
    keys = [k for k, _part in links.outbox_rows(links.unit_key("r", 1), plan.units[0].sync_ids)]
    assert len(keys) == 2 and view._outbox_state(plan) == "missing"
    GraphSyncOutbox.objects.create(kind="samples", key=keys[0], payload=[2], done_at=timezone.now())
    assert view._outbox_state(plan) == "missing"           # its first row drained, the second never written
    GraphSyncOutbox.objects.create(kind="samples", key=keys[1], payload=[3])
    assert view._outbox_state(plan) == "pending"
    GraphSyncOutbox.objects.filter(key=keys[1]).update(failing_since=timezone.now())
    assert view._outbox_state(plan) == "failed"
    GraphSyncOutbox.objects.filter(key=keys[1]).update(attempts=outbox_state.MAX_ATTEMPTS)
    assert view._outbox_state(plan) == "dead"
    GraphSyncOutbox.objects.filter(key=keys[1]).update(done_at=timezone.now())
    assert view._outbox_state(plan) == "done"


def test_verify_graph_answers_503_in_the_envelope_when_the_graph_or_the_plan_cannot_be_read(superuser):
    from neo4j.exceptions import ServiceUnavailable

    row = _planned(superuser)                              # its run directory does not exist
    response = _client(superuser).get(URL + str(row.share_id) + "/?verify=graph")
    assert response.status_code == 503 and response.json()["errors"][0]["title"] == "graph_unavailable"

    @contextlib.contextmanager
    def down():
        raise ServiceUnavailable("no route")
        yield

    with patch.object(view, "_graph", down), patch.object(view, "_graph_check", side_effect=ServiceUnavailable("x")):
        response = _client(superuser).get(URL + str(row.share_id) + "/?verify=graph")
    assert response.status_code == 503 and response.json()["errors"][0]["title"] == "graph_unavailable"
    header = "Basic " + base64.b64encode(b"admin:x").decode("ascii")
    with patch.object(view.SeekSession, "prove_for", lambda self, u: self), patch.object(view, "_graph", down):
        response = _client(superuser).post(URL + str(row.share_id) + "/apply/", {"plan_sha256": "a" * 64},
                                           format="json", HTTP_AUTHORIZATION=header)
    assert response.status_code == 503 and response.json()["errors"][0]["title"] == "graph_unavailable"


def test_a_202_answers_the_state_the_step_left_not_a_later_one(superuser):
    row = _planned(superuser)
    share_jobs.SampleShare.objects.filter(pk=row.pk).update(state="running")   # the worker took it already
    response, _step = _apply(superuser, row, answer=StepAnswer(202, "queued", 2, 0))
    assert response.status_code == 202 and response.json()["state"] == "queued"
