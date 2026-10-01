"""The operator's SEEK session (tool spec 4.6, 7.4): the credential, the proof, the write timeout, the answers."""
import base64
import io
import logging

import orjson
import pytest
import requests
from django.contrib.auth import get_user_model

from nextseek_api.studies import seek as s

PASSWORD = "s3cr3t pass:wordé"


class FakeClient:
    """SeekAPIClient's surface: each method records the request's Authorization and the timeout it was sent with."""

    def __init__(self, answers=None):
        self.timeout_s = 20
        self.sent = []
        self.answers = answers or {}

    def _answer(self, name, request, *args):
        self.sent.append((name, request.META.get("HTTP_AUTHORIZATION"), self.timeout_s, args))
        answer = self.answers.get(name, (200, {"data": {"id": "1", "type": "people"}}))
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer
        return orjson.dumps(body), status, {}, None

    def get_current_person(self, request):
        return self._answer("get_current_person", request)

    def get_study(self, request, study_id):
        return self._answer("get_study", request, study_id)

    def get_assay(self, request, assay_id):
        return self._answer("get_assay", request, assay_id)

    def create_study(self, request, payload):
        return self._answer("create_study", request, payload)

    def create_assay(self, request, payload):
        return self._answer("create_assay", request, payload)

    def delete_study(self, request, study_id):
        return self._answer("delete_study", request, study_id)

    def delete_assay(self, request, assay_id):
        return self._answer("delete_assay", request, assay_id)


def _session(answers=None):
    client = FakeClient(answers)
    return s.SeekSession(s.SeekCredential("operator", PASSWORD), client_factory=lambda: client), client


def test_the_credential_is_redacted_everywhere_it_prints():
    cred = s.SeekCredential("operator", PASSWORD)
    assert PASSWORD not in repr(cred) and PASSWORD not in str(cred) and "operator" in repr(cred)
    session, _ = _session()
    assert PASSWORD not in repr(session) and PASSWORD not in str(vars(session))


def test_the_request_is_basic_only_with_no_cookie_or_session():
    request = s.SeekCredential("operator", PASSWORD).request()
    expected = "Basic " + base64.b64encode(f"operator:{PASSWORD}".encode("utf-8")).decode("ascii")
    assert request.META == {"HTTP_AUTHORIZATION": expected}
    assert request.COOKIES == {} and request.session == {}


def test_read_password_from_stdin_keeps_every_character_but_the_newline():
    assert s.read_password(from_stdin=True, stdin=io.StringIO(PASSWORD + "\n")) == PASSWORD
    assert s.read_password(from_stdin=True, stdin=io.StringIO(PASSWORD + "\r\n")) == PASSWORD
    with pytest.raises(s.SeekRefused, match="no password"):
        s.read_password(from_stdin=True, stdin=io.StringIO("\n"))


def test_read_password_from_the_terminal_uses_the_prompt_given():
    assert s.read_password(from_stdin=False, prompt=lambda text: PASSWORD) == PASSWORD


@pytest.mark.django_db
def test_prove_needs_one_person_bound_to_a_superuser(monkeypatch):
    user = get_user_model().objects.create(username="operator", is_superuser=True)
    bound = []
    monkeypatch.setattr(s, "_assert_local_seek_binding", lambda u, pid: bound.append((u.pk, pid)))
    session, client = _session({"get_current_person": (200, {"data": {"id": "42", "type": "people"}})})
    assert session.prove() is session
    assert (session.person_id, session.django_user_id) == (42, user.pk) and bound == [(user.pk, 42)]
    assert client.sent[0][2] == s.READ_TIMEOUT_S


@pytest.mark.django_db
@pytest.mark.parametrize("answer, code", [((401, {}), "login_rejected"),
                                          ((200, {"data": {"id": "42", "type": "projects"}}), "no_person")])
def test_prove_refuses_a_bad_answer(monkeypatch, answer, code):
    get_user_model().objects.create(username="operator", is_superuser=True)
    monkeypatch.setattr(s, "_assert_local_seek_binding", lambda u, pid: None)
    session, _ = _session({"get_current_person": answer})
    with pytest.raises(s.SeekRefused) as exc:
        session.prove()
    assert exc.value.code == code


@pytest.mark.django_db
def test_prove_refuses_a_user_who_is_not_a_superuser_or_not_bound(monkeypatch):
    get_user_model().objects.create(username="operator", is_staff=True, is_superuser=False)
    monkeypatch.setattr(s, "_assert_local_seek_binding", lambda u, pid: None)
    session, _ = _session({"get_current_person": (200, {"data": {"id": "42", "type": "people"}})})
    with pytest.raises(s.SeekRefused) as exc:
        session.prove()
    assert exc.value.code == "not_superuser"

    from rest_framework.exceptions import AuthenticationFailed

    def unbound(u, pid):
        raise AuthenticationFailed("no")

    monkeypatch.setattr(s, "_assert_local_seek_binding", unbound)
    with pytest.raises(s.SeekRefused) as exc:
        session.prove()
    assert exc.value.code == "person_not_bound"


def test_writes_use_the_write_timeout_reads_the_read_timeout():
    session, client = _session({"create_study": (201, {"data": {"id": "77", "type": "studies"}}),
                                "get_study": (200, {"data": {"id": "20"}})})
    assert session.create_study({"data": {}}) == 77
    assert session.get_study(20) == {"data": {"id": "20"}}
    assert [sent[2] for sent in client.sent] == [s.WRITE_TIMEOUT_S, s.READ_TIMEOUT_S]


@pytest.mark.parametrize("error", [requests.Timeout("slow"), requests.ConnectionError("gone")])
def test_a_post_that_raised_is_an_unknown_outcome(error):
    session, _ = _session({"create_assay": error})
    with pytest.raises(s.SeekUnknownOutcome):
        session.create_assay({"data": {}})


def test_a_get_is_retried_once():
    session, client = _session()
    calls = iter([requests.Timeout("slow"), (200, {"data": {"id": "101"}})])
    client.answers["get_assay"] = None

    def flaky(request, assay_id):
        answer = next(calls)
        if isinstance(answer, BaseException):
            raise answer
        return orjson.dumps(answer[1]), answer[0], {}, None

    client.get_assay = flaky
    assert session.get_assay(101) == {"data": {"id": "101"}}


@pytest.mark.parametrize("status, code", [(401, "unauthorized"), (403, "forbidden"), (422, "rejected"),
                                          (500, "seek_error"), (503, "seek_error")])
def test_answers_that_stop_the_run(status, code):
    session, _ = _session({"create_study": (status, {"errors": [{"detail": "title is too long"}]})})
    with pytest.raises(s.SeekError) as exc:
        session.create_study({"data": {}})
    assert exc.value.code == code and exc.value.status == status
    if status == 422:
        assert "title is too long" in exc.value.message


def test_deletes_report_rather_than_raise():
    session, _ = _session({"delete_assay": (204, {}), "delete_study": (422, {})})
    assert session.delete_assay(501) == (True, 204)
    assert session.delete_study(101) == (False, 422)
    session, _ = _session({"delete_study": requests.Timeout("slow")})
    assert session.delete_study(101) == (False, None)


def test_the_session_logs_the_login_never_the_password(caplog):
    caplog.set_level(logging.DEBUG)
    session, _ = _session({"create_study": (401, {})})
    with pytest.raises(s.SeekError):
        session.create_study({"data": {}})
    assert PASSWORD not in caplog.text


def test_the_lookups_compare_titles_in_python(monkeypatch):
    rows = {"studies": [(101, "Paper One"), (102, " paper one "), (103, "Other")],
            "assays": [(501, "RNA-seq run"), (502, "RNA-seq run"), (503, "Imaging run")]}
    monkeypatch.setattr(s, "_seek_rows", lambda sql, params: rows["studies" if "FROM studies" in sql else "assays"])
    session, _ = _session()
    assert session.find_study(7, "Paper One") == [101, 102]
    assert session.find_assay(101, "RNA-seq run") == [501, 502]


# --- the share endpoint's credential: the caller's own (tool spec 16.6, T39) ----------------------------------------

def _request(**meta):
    from django.test import RequestFactory

    return RequestFactory().post("/nextseek_api/sample-shares/x/apply/", **meta)


def test_a_basic_header_gives_the_callers_credential():
    header = "Basic " + base64.b64encode(f"operator:{PASSWORD}".encode("utf-8")).decode("ascii")
    cred = s.SeekCredential.from_request(_request(HTTP_AUTHORIZATION=header))
    assert cred.login == "operator" and cred.request().META == {"HTTP_AUTHORIZATION": header}
    assert PASSWORD not in repr(cred)


def test_a_session_login_gives_the_callers_credential(monkeypatch):
    from nextseek_api import helpers

    monkeypatch.setattr(helpers, "get_auth", lambda request: ("operator", PASSWORD))
    assert s.SeekCredential.from_request(_request()).login == "operator"


def test_a_token_header_gives_none(monkeypatch):
    from nextseek_api import helpers

    monkeypatch.setattr(helpers, "get_auth", lambda request: None)
    assert s.SeekCredential.from_request(_request(HTTP_AUTHORIZATION="Token abc")) is None
    assert s.SeekCredential.from_request(_request()) is None


@pytest.mark.django_db
def test_prove_for_refuses_another_user(monkeypatch):
    model = get_user_model()
    operator = model.objects.create(username="operator", is_superuser=True)
    other = model.objects.create(username="someone", is_superuser=True)
    monkeypatch.setattr(s, "_assert_local_seek_binding", lambda u, pid: None)
    session, _ = _session({"get_current_person": (200, {"data": {"id": "42", "type": "people"}})})
    assert session.prove_for(operator) is session
    with pytest.raises(s.SeekRefused) as exc:
        session.prove_for(other)
    assert exc.value.code == "seek_identity_mismatch"
