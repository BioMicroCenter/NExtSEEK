"""The mirror models must only select columns SEEK 1.18.3 still has."""
import logging
from unittest import mock

from django.test import SimpleTestCase

from seek.models import People

# people columns in SEEK v1.18.3 db/schema.rb (skype_name was dropped by
# migration 20260312102334 DropSkypeNameFromPerson).
SEEK_1_18_3_PEOPLE_COLUMNS = {
    "id", "created_at", "updated_at", "first_name", "last_name", "email", "phone",
    "web_page", "description", "avatar_id", "status_id", "first_letter", "uuid",
    "roles_mask", "orcid",
}


class PeopleMirrorTests(SimpleTestCase):
    def test_people_columns_exist_in_seek_1_18_3(self):
        cols = {f.column for f in People._meta.concrete_fields}
        self.assertEqual(cols - SEEK_1_18_3_PEOPLE_COLUMNS, set())

    def test_people_select_has_no_skype_name(self):
        self.assertNotIn("skype_name", str(People.objects.filter(id=1).query))


class LoginSyncLogsTests(SimpleTestCase):
    def test_people_lookup_failure_is_logged_with_traceback(self):
        from dmac import views

        with mock.patch.object(views.People.objects, "get", side_effect=RuntimeError("1054 unknown column")), \
                self.assertLogs(views.logger, level=logging.ERROR) as cm:
            status, msg = views.userSynchronization({"username": "u", "password": "p", "user_id": 1})
        self.assertEqual(status, 0)
        self.assertIn("Login user not found in SEEK people table", msg)
        self.assertTrue(any(r.exc_info for r in cm.records))
