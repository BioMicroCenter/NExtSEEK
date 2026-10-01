"""The sample-type grid's two row endpoints require a login.

``/seek/retrieve/samples/`` (``retrieveSamples``, the rows behind the sample-type grid) and
``/seek/samples/retrieveType/`` (``getSampleType``) answer the login-required envelope unless the caller is logged in
with a SEEK login; a logged-in caller gets the rows as before.

Hermetic: SEEK and MySQL are stubbed.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

import seek.views.samples  # noqa: F401  -- so @patch can resolve the target

_MOD = "seek.views.samples"
ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)
MEMBER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)
ROWS = json.dumps({"total": 1, "rows": [{"id": 1}], "msg": "okay", "status": 1})


@pytest.fixture
def world():
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True, "username": "member", "password": "x", "server": "s"}
    dbsample = MagicMock()
    dbsample.processRecords.return_value = ROWS
    dbsample.getSampleType.return_value = ROWS
    with patch(f"{_MOD}.SeekDB", return_value=seekdb), patch(f"{_MOD}.DBtable_sample", return_value=dbsample):
        yield SimpleNamespace(seekdb=seekdb, dbsample=dbsample)


def _call(view, user):
    request = RequestFactory().get("/", {"sampletype_id": "1", "attribute": "none"})
    request.user = user
    request.session = {}
    return getattr(seek.views.samples, view)(request)


@pytest.mark.parametrize("view", ["retrieveSamples", "getSampleType"])
def test_a_caller_without_a_login_gets_the_login_envelope_and_no_rows(world, view):
    body = json.loads(_call(view, ANONYMOUS).content)

    assert body["status"] == 0 and body["msg"] == seek.views.samples.LOGIN_REQUIRED
    world.dbsample.processRecords.assert_not_called()
    world.dbsample.getSampleType.assert_not_called()


@pytest.mark.parametrize("view", ["retrieveSamples", "getSampleType"])
def test_a_caller_without_a_seek_login_gets_the_login_envelope(world, view):
    world.seekdb.getSeekLogin.return_value = {"status": False, "err": ["No valid username or password"]}

    body = json.loads(_call(view, MEMBER).content)

    assert body["status"] == 0 and body["msg"] == seek.views.samples.LOGIN_REQUIRED


@pytest.mark.parametrize("view", ["retrieveSamples", "getSampleType"])
def test_a_logged_in_caller_gets_the_rows_as_before(world, view):
    assert json.loads(_call(view, MEMBER).content) == json.loads(ROWS)
