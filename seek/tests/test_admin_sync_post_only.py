"""The two admin sync views change data, so they accept POST only, with the CSRF token."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

CASES = [
    ("/seek/admin/clades/syncSampleTypes/", "seek.views.admin.DBtable_stc", "syncSampleTypes"),
    ("/seek/admin/internal_assays/syncInternalAssays", "seek.views.admin.DBtable_assaysinternalassays", "syncAssays"),
]


@pytest.fixture
def staff(db):
    return User.objects.create_superuser("sync-admin", password="x")


@pytest.fixture(autouse=True)
def seek_login_ok():
    with patch("seek.decorators._login", return_value={"status": True}), \
            patch("seek.views.admin.hooks.enqueue"):
        yield


@pytest.mark.parametrize("path,target,method", CASES)
def test_get_is_refused_and_syncs_nothing(staff, path, target, method):
    client = Client()
    client.force_login(staff)
    with patch(target) as table:
        assert client.get(path).status_code == 405
    getattr(table.return_value, method).assert_not_called()


@pytest.mark.parametrize("path,target,method", CASES)
def test_supervisor_post_runs_the_sync(staff, path, target, method):
    client = Client()
    client.force_login(staff)
    with patch(target) as table:
        assert client.post(path).status_code == 200
    getattr(table.return_value, method).assert_called_once_with()


@pytest.mark.parametrize("path,target,method", CASES)
def test_post_without_a_csrf_token_is_forbidden(staff, path, target, method):
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    with patch(target) as table:
        assert client.post(path).status_code == 403
    getattr(table.return_value, method).assert_not_called()
