"""Batch upload: the lab and person overrides are accepted from a superuser only.

``POST /nextseek_api/batch_upload/start/`` takes an optional ``lababbv`` and an
optional ``person_id``. Both change whom an upload is attributed to, so both are
honoured only when the caller ``is_superuser``; for anyone else the upload is
attributed to the caller's own SEEK identity and lab. ``is_staff`` is not an admin
signal here: the SEEK login sets it on every account (``nextseek_api/permissions.py``).

Hermetic: users are ``MagicMock`` stand-ins, as in ``test_views_coverage.py``, and
every SEEK and Celery collaborator is patched.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.batch_upload.views import BatchUploadViewSet, _resolve_user_context

OWN = {"person_id": 42, "lababbv": "MIT"}
OTHER = {"person_id": 99, "lababbv": "BMC"}


def _user(*, is_staff, is_superuser, username="upload_user"):
    user = MagicMock()
    user.pk = 1
    user.is_authenticated = True
    user.is_active = True
    user.is_staff = is_staff
    user.is_superuser = is_superuser
    user.username = username
    return user


def _resolve(user, data):
    """Run ``_resolve_user_context`` with the caller's own SEEK identity being 42/MIT."""
    request = MagicMock()
    request.user = user
    request.data = data

    seek_user = MagicMock()
    seek_user.person_id = 42

    def user_info(pid):
        return (OTHER, True, "") if int(pid) == 99 else (OWN, True, "")

    with patch("nextseek_api.helpers.resolve_seek_auth",
               return_value=((user.username, "pass"), {})), \
         patch("seek.models.Users") as users, \
         patch("seek.seekdb.SeekDB") as seekdb:
        users.objects.using.return_value.get.return_value = seek_user
        users._DATABASE = "default"
        seekdb.return_value.getSeekLogin.side_effect = Exception("no session")
        seekdb.return_value.getUserInfo.side_effect = user_info
        return _resolve_user_context(request)


# --------------------------------------------------------------------------- #
# person_id
# --------------------------------------------------------------------------- #

def test_person_override_is_not_accepted_from_a_staff_account():
    result = _resolve(_user(is_staff=True, is_superuser=False), {"person_id": 99})
    assert result["contributor_id"] == 42
    assert result["lababbv"] == "MIT"
    assert result.get("person_id_ignored") is True


def test_person_override_is_accepted_from_a_superuser():
    result = _resolve(_user(is_staff=False, is_superuser=True), {"person_id": 99})
    assert result["contributor_id"] == 99
    assert result["lababbv"] == "BMC"
    assert "person_id_ignored" not in result


def test_without_an_override_a_superuser_uploads_as_themselves():
    result = _resolve(_user(is_staff=True, is_superuser=True), {})
    assert result == {"contributor_id": 42, "lababbv": "MIT"}


# --------------------------------------------------------------------------- #
# lababbv, on the start action
# --------------------------------------------------------------------------- #

def _start(user, lababbv):
    view = BatchUploadViewSet.as_view({"post": "start"})
    request = APIRequestFactory().post(
        "/nextseek_api/batch_upload/start/",
        {"project_id": 1, "lababbv": lababbv,
         "rows": [{"SampleType": "M.Mice", "json_metadata": {"Name": "mouse1"}}]},
        format="json",
    )
    force_authenticate(request, user=user)
    return view(request)


@pytest.mark.parametrize("is_staff,is_superuser,expected", [
    (True, False, "MIT"),
    (False, False, "MIT"),
    (False, True, "BMC"),
    (True, True, "BMC"),
])
@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
@patch("nextseek_api.batch_upload.views._resolve_user_context",
       return_value={"contributor_id": 42, "lababbv": "MIT"})
def test_lab_override_is_accepted_from_a_superuser_only(
        _ctx, task, _register, is_staff, is_superuser, expected):
    task.delay.return_value = MagicMock(id="job-1")
    response = _start(_user(is_staff=is_staff, is_superuser=is_superuser), "bmc")
    assert response.status_code == 202
    assert task.delay.call_args.kwargs["lababbv"] == expected
