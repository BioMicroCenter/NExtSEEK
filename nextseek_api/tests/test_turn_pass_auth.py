"""TurnPassAuthentication over HTTP: what a pass reaches, what it is refused, and how (piece 1)."""
import logging
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from nextseek_api.assistant import turn_pass
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.tests.turn_pass_support import make_turn, make_user, pass_header

pytestmark = pytest.mark.django_db

A = "/nextseek_api/assistant"


class _Config:
    API_USER = "service"
    API_PASS = "service-pw"


@pytest.fixture(autouse=True)
def _host(monkeypatch):
    """No SEEK, no graph, no model: participation passes, the scope is empty, the chat config is a stand-in."""
    monkeypatch.setattr("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                        lambda self, request, view: True)
    monkeypatch.setattr("nextseek_api.services.assistant.plain_scope", lambda user: None)
    with override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None):
        yield


def _refused(resp, status, code):
    assert resp.status_code == status, resp.content
    body = resp.json()
    assert set(body) == {"code", "reason", "message", "errors"}
    assert body["code"] == code
    assert body["reason"] is None
    return body


# --- what a pass reaches -----------------------------------------------------------------------------------------

def test_the_pass_reads_its_own_task_progress():
    turn, raw = make_turn()
    resp = APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw))
    assert resp.status_code == 200, resp.content
    assert resp.json()["task_id"] == str(turn.task.task_id)


def test_the_pass_reads_its_own_chat():
    turn, raw = make_turn()
    resp = APIClient().get(f"{A}/sessions/{turn.chat_id}/", **pass_header(raw))
    assert resp.status_code == 200, resp.content


def test_the_pass_runs_an_op():
    turn, raw = make_turn()
    with patch("nextseek_api.services.assistant.run_op", return_value={"source": "catalog"}) as run_op:
        resp = APIClient().post(f"{A}/graph-schema/", {}, format="json", **pass_header(raw))
    assert resp.status_code == 200, resp.content
    assert run_op.call_args.args[0] == "graph-schema"


def test_the_pass_starts_a_nested_turn_in_its_own_chat():
    turn, raw = make_turn()
    started = []

    class _Thread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            started.append(kwargs)

        def start(self):
            pass

    with patch("nextseek_api.services.assistant.threading.Thread", _Thread):
        resp = APIClient().post(
            f"{A}/query/async/", {"query": "how many", "mode": "standard", "session_id": str(turn.chat_id)},
            format="json", **pass_header(raw))
    assert resp.status_code == 202, resp.content
    assert resp.json()["session_id"] == str(turn.chat_id)
    assert len(started) == 1


def test_the_pass_validates_an_upload():
    turn, raw = make_turn()
    result = MagicMock()
    result.model_dump.return_value = {"ok": True}
    with patch("nextseek_api.batch_upload.views._save_uploaded_file", return_value="/tmp/turn-pass-test.xlsx"), \
         patch("nextseek_api.batch_upload.views.run_validation_multi", return_value=result) as validate:
        resp = APIClient().post(
            "/nextseek_api/batch-upload/validate/",
            {"project_id": "1", "file": SimpleUploadedFile("a.xlsx", b"PK\x03\x04")},
            format="multipart", **pass_header(raw))
    assert resp.status_code == 200, resp.content
    assert validate.call_args.kwargs["contributor_id"] == turn.user.pk


def test_the_pass_lists_projects_through_the_seek_proxy():
    turn, raw = make_turn()
    answer = (b'{"data": []}', 200, {"Content-Type": "application/vnd.api+json"}, None)
    with patch("nextseek_api.helpers.SeekAPIClient._request", return_value=answer) as seek:
        resp = APIClient().get("/nextseek_api/projects/", **pass_header(raw))
    assert resp.status_code not in (401, 403), resp.content
    assert seek.called


def test_a_csrf_cookie_alone_is_not_a_credential():
    turn, raw = make_turn()
    client = APIClient()
    client.cookies["csrftoken"] = "x"
    resp = client.get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw))
    assert resp.status_code == 200, resp.content


# --- 401 AUTH_FAILED ---------------------------------------------------------------------------------------------

def test_a_revoked_pass_is_refused():
    turn, raw = make_turn()
    turn_pass.revoke(turn)
    resp = APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw))
    _refused(resp, 401, "AUTH_FAILED")
    assert resp["WWW-Authenticate"] == "NextseekTurn"


def test_an_expired_pass_is_refused():
    turn, raw = make_turn(deadline_in=-61)  # expiry = deadline + 60 s, a second ago
    _refused(APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw)), 401, "AUTH_FAILED")


def test_a_pass_whose_task_has_finished_is_refused():
    turn, raw = make_turn()
    QueryTask.objects.filter(pk=turn.task_id).update(status="completed")
    _refused(APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw)), 401, "AUTH_FAILED")


def test_a_pass_for_an_inactive_user_is_refused():
    turn, raw = make_turn()
    type(turn.user).objects.filter(pk=turn.user_id).update(is_active=False)
    _refused(APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw)), 401, "AUTH_FAILED")


def test_an_unknown_or_malformed_pass_is_refused():
    turn, raw = make_turn()
    for header in ("NextseekTurn " + "A" * 43, "NextseekTurn", f"NextseekTurn {raw} extra",
                   f"NextseekTurn {raw}, Basic dTpw"):
        resp = APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", HTTP_AUTHORIZATION=header)
        _refused(resp, 401, "AUTH_FAILED")


@pytest.mark.parametrize("extra", ["seek_header", "session_cookie"])
def test_a_pass_with_another_credential_is_refused(extra):
    turn, raw = make_turn()
    client = APIClient()
    headers = dict(pass_header(raw))
    if extra == "seek_header":
        headers["HTTP_X_SEEK_AUTHORIZATION"] = "Token abc"
    else:
        client.cookies[settings.SESSION_COOKIE_NAME] = "a-session"
    _refused(client.get(f"{A}/tasks/{turn.task.task_id}/progress/", **headers), 401, "AUTH_FAILED")


# --- 403 PASS_NOT_ALLOWED and 422 VALIDATION ---------------------------------------------------------------------

@pytest.mark.parametrize("method, path, body", [
    ("post", "/api-write/", {"parser_plan": "{}", "confirmed_write": True}),
    ("post", "/query/", {"query": "q", "mode": "standard"}),
    ("get", "/me/", None),
    ("get", "/sessions/", None),
    ("post", "/sessions/", {}),
    ("get", "/test-cases/", None),
])
def test_assistant_routes_outside_the_table_are_refused(method, path, body):
    turn, raw = make_turn()
    client = APIClient()
    call = getattr(client, method)
    resp = call(A + path, body, format="json", **pass_header(raw)) if body is not None else call(
        A + path, **pass_header(raw))
    _refused(resp, 403, "PASS_NOT_ALLOWED")


def test_the_pass_cannot_rename_or_delete_its_own_chat():
    turn, raw = make_turn()
    client = APIClient()
    _refused(client.patch(f"{A}/sessions/{turn.chat_id}/", {"title": "x"}, format="json", **pass_header(raw)),
             403, "PASS_NOT_ALLOWED")
    _refused(client.delete(f"{A}/sessions/{turn.chat_id}/", **pass_header(raw)), 403, "PASS_NOT_ALLOWED")
    assert ChatSession.objects.filter(pk=turn.chat_id).exists()


def test_another_chat_is_refused_even_the_same_users():
    turn, raw = make_turn()
    other = str(ChatSession.objects.create(user=turn.user).session_id)
    client = APIClient()
    _refused(client.get(f"{A}/sessions/{other}/", **pass_header(raw)), 403, "PASS_NOT_ALLOWED")
    _refused(client.post(f"{A}/report/", {"mode": "samples", "project": "p", "session_id": other},
                         format="json", **pass_header(raw)), 403, "PASS_NOT_ALLOWED")
    _refused(client.post(f"{A}/query/async/", {"query": "q", "mode": "standard", "session_id": other},
                         format="json", **pass_header(raw)), 403, "PASS_NOT_ALLOWED")


def test_another_users_task_progress_is_refused():
    turn, raw = make_turn()
    stranger, _ = make_turn(make_user("stranger"))
    _refused(APIClient().get(f"{A}/tasks/{stranger.task.task_id}/progress/", **pass_header(raw)),
             403, "PASS_NOT_ALLOWED")


def test_a_nested_turn_without_a_session_is_refused():
    turn, raw = make_turn()
    _refused(APIClient().post(f"{A}/query/async/", {"query": "q", "mode": "standard"}, format="json",
                              **pass_header(raw)), 403, "PASS_NOT_ALLOWED")


@pytest.mark.parametrize("setting, value", [
    ("use_prod", True), ("force_new", True), ("force_route", "cc"), ("max_turn_length_s", 600),
    ("force_parser_mode", "graph"), ("prompt_variant", "v2_apoc"),
])
def test_a_body_that_sets_an_admin_setting_is_refused(setting, value):
    turn, raw = make_turn()
    client = APIClient()
    nested = {"query": "q", "mode": "standard", "session_id": str(turn.chat_id), setting: value}
    _refused(client.post(f"{A}/query/async/", nested, format="json", **pass_header(raw)), 403, "PASS_NOT_ALLOWED")
    _refused(client.post(f"{A}/graph/", {"query": "q", setting: value}, format="json", **pass_header(raw)),
             403, "PASS_NOT_ALLOWED")


def test_a_form_body_cannot_smuggle_a_refused_setting():
    turn, raw = make_turn()
    resp = APIClient().post(f"{A}/graph/", {"query": "q", "use_prod": "true"}, format="multipart",
                            **pass_header(raw))
    _refused(resp, 403, "PASS_NOT_ALLOWED")


def test_a_form_body_to_batch_upload_validate_cannot_carry_a_refused_setting():
    turn, raw = make_turn()
    resp = APIClient().post(
        "/nextseek_api/batch-upload/validate/",
        {"project_id": "1", "use_prod": "true", "file": SimpleUploadedFile("a.xlsx", b"PK\x03\x04")},
        format="multipart", **pass_header(raw))
    _refused(resp, 403, "PASS_NOT_ALLOWED")


def test_the_pass_scheme_is_never_read_as_a_seek_credential():
    from django.test import RequestFactory
    from nextseek_api.helpers import resolve_seek_auth
    request = RequestFactory().get("/x", HTTP_AUTHORIZATION="NextseekTurn " + "A" * 43)
    request.session = {}
    assert resolve_seek_auth(request) == (None, None)


def test_a_session_on_the_throwaway_session_ops_is_a_validation_error():
    turn, raw = make_turn()
    for op in ("entity", "parse", "graph", "aggregate"):
        resp = APIClient().post(f"{A}/{op}/", {"query": "q", "session_id": str(turn.chat_id)}, format="json",
                                **pass_header(raw))
        body = _refused(resp, 422, "VALIDATION")
        assert body["errors"] == [{"field": "session_id", "type": "not_accepted_with_turn_pass"}]


# --- views that never read the pass, and callers with no credential ----------------------------------------------

@pytest.mark.parametrize("method, path, body", [
    ("post", "/nextseek_api/cc-assistant/cc/query/async/", {"query": "q", "mode": "standard"}),
    ("post", "/nextseek_api/cc-assistant/query/async/", {"query": "q", "mode": "standard"}),
    ("get", "/nextseek_api/people/current/", None),
    ("post", "/nextseek_api/samples/graph_search/", {"text": "x"}),
])
def test_views_that_never_read_the_pass_see_an_anonymous_caller(method, path, body):
    turn, raw = make_turn()
    client = APIClient()
    call = getattr(client, method)
    with patch("nextseek_api.services.cc_assistant.cc_turn.start_task") as start_task:
        resp = call(path, body, format="json", **pass_header(raw)) if body is not None else call(
            path, **pass_header(raw))
    assert resp.status_code in (401, 403), resp.content
    start_task.assert_not_called()


def test_unauthenticated_calls_are_still_401():
    """The sidecar healthcheck reads a 401 from assistant/me as healthy."""
    resp = APIClient().get(f"{A}/me/")
    assert resp.status_code == 401
    assert resp["WWW-Authenticate"] == "NextseekTurn"
    assert APIClient().get("/nextseek_api/projects/").status_code == 401


def test_the_pass_never_reaches_a_log_line_or_a_response(caplog):
    turn, raw = make_turn()
    caplog.set_level(logging.DEBUG)
    responses = [
        APIClient().get(f"{A}/tasks/{turn.task.task_id}/progress/", **pass_header(raw)),
        APIClient().post(f"{A}/api-write/", {}, format="json", **pass_header(raw)),
    ]
    turn_pass.revoke(turn)
    responses.append(APIClient().get(f"{A}/me/", **pass_header(raw)))
    assert raw not in caplog.text
    assert all(raw.encode("ascii") not in resp.content for resp in responses)


def test_the_schema_generator_can_resolve_the_authenticator():
    """Without an extension drf-spectacular warns 'could not resolve authenticator' on every view that lists it."""
    import nextseek_api.attributes.openapi  # noqa: F401  (registers the extensions)
    from drf_spectacular.extensions import OpenApiAuthenticationExtension
    from nextseek_api.assistant.turn_pass_auth import TurnPassAuthentication

    assert OpenApiAuthenticationExtension.get_match(TurnPassAuthentication()) is not None
