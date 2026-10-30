"""The lab code in new sample UIDs is the uploader's own, read from SEEK's tables when SEEK's API is out of reach.

Every UID a batch job mints carries a lab code (``D.SEQ-<yymmdd><lab>-<n>``), the first three characters of the
SEEK institution of the person the upload is filed under. The upload used to read it only from SEEK's API as the
caller, so a caller with no SEEK session or password (a token, a turn pass, an in-process upload) got the "NA"
placeholder written into its UIDs for good. Now the resolver falls back to SEEK's own tables, and a person who belongs
to no institution is refused at every job start instead of minting "NA". The SQL runs for real on SQLite.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from django.db import connections
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.batch_upload import views
from seek.seekdb import lab_abbreviation

DEMO, LEAVER, NOBODY = 1, 2, 3


def _utc(delta):
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%d %H:%M:%S")


@pytest.mark.parametrize("title,code", [
    ("BMC-BioMicro Center (MIT)", "BMC"),
    ("mit", "MIT"),
    ("Ab", "AB"),
    ("A B Lab", "A_B"),
    ("", ""),
    (None, ""),
])
def test_the_lab_code_is_the_institutions_first_three_characters(title, code):
    assert lab_abbreviation(title) == code


@override_settings(SEEK_DATABASE="default")  # the SEEK tables below live in the test database
class LabCodeFromSeekTables(TestCase):
    databases = {"default"}

    def setUp(self):
        past = _utc(-timedelta(days=1))
        with connections["default"].cursor() as c:
            for table in ("group_memberships", "work_groups", "institutions"):
                c.execute(f"DROP TABLE IF EXISTS {table}")
            c.execute("CREATE TABLE institutions (id INTEGER PRIMARY KEY, title TEXT)")
            c.execute("CREATE TABLE work_groups (id INTEGER PRIMARY KEY, project_id INTEGER, institution_id INTEGER)")
            c.execute("CREATE TABLE group_memberships (id INTEGER PRIMARY KEY, person_id INTEGER, "
                      "work_group_id INTEGER, has_left INTEGER, time_left_at DATETIME)")
            c.execute("INSERT INTO institutions (id, title) VALUES (1, 'BMC-BioMicro Center (MIT)'), "
                      "(2, 'Koch Institute')")
            c.execute("INSERT INTO work_groups (id, project_id, institution_id) VALUES (1, 1, 1), (2, 2, 1), "
                      "(3, 3, 2)")
            # demo: two memberships at BMC. leaver: left BMC (joined first), still at Koch.
            c.execute("INSERT INTO group_memberships (person_id, work_group_id, has_left, time_left_at) VALUES "
                      "(%s, 1, 0, NULL), (%s, 2, NULL, NULL), (%s, 1, 1, %s), (%s, 3, 0, NULL)",
                      [DEMO, DEMO, LEAVER, past, LEAVER])

    def test_a_member_gets_their_institutions_code(self):
        assert views._lab_code_from_seek_db(DEMO) == "BMC"

    def test_a_current_membership_wins_over_an_earlier_one_the_person_left(self):
        assert views._lab_code_from_seek_db(LEAVER) == "KOC"

    def test_a_person_in_no_institution_has_no_code(self):
        assert views._lab_code_from_seek_db(NOBODY) is None

    def test_a_failed_read_is_no_code_not_an_error(self):
        with connections["default"].cursor() as c:
            c.execute("DROP TABLE institutions")
        assert views._lab_code_from_seek_db(DEMO) is None


# --------------------------------------------------------------------------- #
# _resolve_user_context
# --------------------------------------------------------------------------- #

def _user(*, is_superuser=False, username="demo"):
    user = MagicMock()
    user.pk = 1
    user.is_authenticated = True
    user.is_active = True
    user.is_staff = True
    user.is_superuser = is_superuser
    user.username = username
    return user


def _resolve(user, data, *, session_lababbv=None, db_codes=None):
    """Resolve with SEEK person 1 as the caller. ``session_lababbv`` None: no SEEK session and no
    credential, so the caller is found through the Users table alone (an in-process or token call)."""
    request = MagicMock()
    request.user = user
    request.data = data
    seek_user = MagicMock(person_id=DEMO)
    db = MagicMock(side_effect=lambda pid: (db_codes or {}).get(pid))
    with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(None, {})), \
         patch("seek.models.Users") as users, \
         patch("seek.seekdb.SeekDB") as seekdb, \
         patch.object(views, "_lab_code_from_seek_db", db):
        users.objects.using.return_value.get.return_value = seek_user
        users._DATABASE = "default"
        if session_lababbv is None:
            seekdb.return_value.getSeekLogin.side_effect = Exception("no session")
        else:
            seekdb.return_value.getSeekLogin.return_value = {
                "status": True, "person_id": DEMO, "lababbv": session_lababbv}
            seekdb.return_value.getUserInfo.return_value = ({"lababbv": "NA"}, False, "")
        return views._resolve_user_context(request), db


def test_without_a_seek_session_the_lab_code_comes_from_seeks_tables():
    result, db = _resolve(_user(), {}, db_codes={DEMO: "BMC"})
    assert result == {"contributor_id": DEMO, "lababbv": "BMC"}
    db.assert_called_once_with(DEMO)


def test_the_seek_apis_code_is_used_when_it_has_one():
    result, db = _resolve(_user(), {}, session_lababbv="MIT", db_codes={DEMO: "BMC"})
    assert result["lababbv"] == "MIT"
    db.assert_not_called()


def test_the_apis_na_placeholder_is_not_a_code():
    result, _db = _resolve(_user(), {}, session_lababbv="NA", db_codes={DEMO: "BMC"})
    assert result["lababbv"] == "BMC"


def test_a_person_in_no_institution_resolves_with_no_code():
    result, _db = _resolve(_user(), {})
    assert result == {"contributor_id": DEMO, "lababbv": None}


def test_an_upload_filed_under_another_person_carries_their_code_not_the_admins():
    result, db = _resolve(_user(is_superuser=True), {"person_id": 9}, session_lababbv="MIT",
                          db_codes={DEMO: "MIT", 9: "KOC"})
    assert result == {"contributor_id": 9, "lababbv": "KOC"}
    db.assert_called_once_with(9)


# --------------------------------------------------------------------------- #
# every job start refuses a missing code
# --------------------------------------------------------------------------- #

def _start(user, data):
    request = APIRequestFactory().post(
        "/nextseek_api/batch_upload/start/",
        {"project_id": 1, "rows": [{"SampleType": "M.Mice", "json_metadata": {"Name": "mouse1"}}], **data},
        format="json")
    force_authenticate(request, user=user)
    return views.BatchUploadViewSet.as_view({"post": "start"})(request)


@pytest.mark.parametrize("is_superuser,data", [
    (False, {}),
    (False, {"lababbv": "BMC"}),  # only a superuser may name the code
    (True, {"lababbv": "na"}),
])
@patch("nextseek_api.batch_upload.views._caller_project_scope", new=lambda user: None)
@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
@patch("nextseek_api.batch_upload.views._resolve_user_context",
       return_value={"contributor_id": DEMO, "lababbv": None})
def test_start_refuses_an_upload_with_no_lab_code(_ctx, task, register, is_superuser, data):
    response = _start(_user(is_superuser=is_superuser), data)
    assert response.status_code == 400
    assert "institution" in response.data["detail"]
    task.delay.assert_not_called()
    register.assert_not_called()


@patch("nextseek_api.batch_upload.views._caller_project_scope", new=lambda user: None)
@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
@patch("nextseek_api.batch_upload.views._resolve_user_context",
       return_value={"contributor_id": DEMO, "lababbv": None})
def test_a_superuser_may_still_name_the_code(_ctx, task, _register):
    task.delay.return_value = MagicMock(id="job-1")
    response = _start(_user(is_superuser=True), {"lababbv": "bmc"})
    assert response.status_code == 202
    assert task.delay.call_args.kwargs["lababbv"] == "BMC"


@pytest.mark.parametrize("lababbv", [None, "", " ", "NA", "na"])
@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
def test_dispatch_refuses_a_missing_or_placeholder_code(task, register, lababbv):
    """The seam both the start action and the upload-reingest op go through, and in-process callers too."""
    with pytest.raises(ValueError, match="lab code"):
        views.dispatch_batch_job(
            user_pk=1, user_ctx={"contributor_id": DEMO, "lababbv": lababbv}, lababbv=lababbv,
            project_id=1, config_overrides={}, scope_project_ids=None, xlsx_paths=["/m/a.xlsx"])
    task.delay.assert_not_called()
    register.assert_not_called()
