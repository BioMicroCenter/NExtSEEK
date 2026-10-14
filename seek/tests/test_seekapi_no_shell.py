"""SeekAPI reads SEEK over HTTP with requests: no process, no shell, no password in a command line.

Hermetic: ``subprocess.Popen`` and ``requests.get`` are replaced.
"""

from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.helpers import basic_auth_header
from seek.seekapi import SeekAPI

HOSTILE = "x' ; touch /tmp/owned ; echo 'y"


class FakeResponse:
    def __init__(self, text):
        self.content = text.encode("utf-8")
        self.status_code = 200


@pytest.fixture
def world():
    popen = MagicMock(name="Popen")
    calls = []
    answers = {}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(answers[url])

    with patch("subprocess.Popen", popen), patch("seek.seekapi.Popen", popen), patch("requests.get", side_effect=fake_get):
        yield popen, calls, answers


def test_get_query_starts_no_process_and_keeps_the_password_out_of_argv(world):
    popen, calls, answers = world
    answers["http://seek:3000/people/5"] = '{"data": {}}'

    assert SeekAPI("http://seek:3000", "member", "s3cret-pw").runGetQuery("/people/5") == {"data": {}}

    popen.assert_not_called()
    (url, kwargs), = calls
    assert url == "http://seek:3000/people/5"
    assert kwargs["headers"]["Authorization"] == basic_auth_header(("member", "s3cret-pw"))["Authorization"]
    assert kwargs["headers"]["accept"] == "application/json"
    assert kwargs["timeout"]


def test_assay_titles_never_reach_a_command(world):
    popen, calls, answers = world
    answers["http://seek:3000/studies/5"] = '{"data": {"relationships": {"assays": {"data": [{"id": "12"}]}}}}'
    answers["http://seek:3000/assays/12"] = '{"data": {"attributes": {"title": %s}}}' % __import__("json").dumps(HOSTILE)

    assert SeekAPI("http://seek:3000", "member", "pw").getAssays("5") == {HOSTILE: "12"}
    popen.assert_not_called()


def test_no_credentials_send_no_authorization_header(world):
    _, calls, answers = world
    answers["http://seek:3000/people/5"] = "{}"

    SeekAPI("http://seek:3000", None, None).runGetQuery("/people/5")

    assert "Authorization" not in calls[0][1]["headers"]


def test_access_denied_body_reads_as_none(world):
    _, _, answers = world
    answers["http://seek:3000/people/5"] = "Access denied"

    assert SeekAPI("http://seek:3000", "m", "p").runGetQuery("/people/5") is None


def test_get_assays_of_a_missing_study_is_empty(world):
    _, _, answers = world
    answers["http://seek:3000/studies/5"] = "Access denied"
    assert SeekAPI("http://seek:3000", "m", "p").getAssays("5") == {}
    answers["http://seek:3000/studies/5"] = '{"errors": [{"title": "Not found"}]}'
    assert SeekAPI("http://seek:3000", "m", "p").getAssays("5") == {}


def test_get_assays_skips_an_assay_it_cannot_read(world):
    _, _, answers = world
    answers["http://seek:3000/studies/5"] = '{"data": {"relationships": {"assays": {"data": [{"id": "12"}, {"id": "13"}]}}}}'
    answers["http://seek:3000/assays/12"] = "Access denied"
    answers["http://seek:3000/assays/13"] = '{"data": {"attributes": {"title": "ok"}}}'
    assert SeekAPI("http://seek:3000", "m", "p").getAssays("5") == {"ok": "13"}
