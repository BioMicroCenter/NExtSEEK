"""One selection rule and one writer behind both template downloads.

`/seek/templates/download/` (a browser form POST) and
`/nextseek_api/templates/generate/` (a JSON API call) produce the same artifact
from the same catalog. Before this module they each resolved codes against
`load_catalog()` and drove `write_template_workbook` themselves, so the two
could drift in exactly the way `build_catalog()` was introduced to stop the
picker page and the catalog endpoint drifting.

The claim here is narrow and mechanical: both call paths go through
`template_catalog.select_entries` and `sample_workbook.render_template_workbook`,
so patching those two names changes both. What the callers still decide for
themselves is what an unknown code costs -- the page drops it so a stale
bookmark still produces the types it names, the API answers 422 so a workbook is
never quietly missing a sheet -- and that difference is asserted too.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

import seek.views.assets  # noqa: F401  -- so @patch can resolve the target
from nextseek_api.services.template_catalog import SampleTypeEntry, select_entries

TIS = SampleTypeEntry(code="TIS", sample_type_id=2, name="Tissue",
                      description="A tissue sample.", group="")
SEQ = SampleTypeEntry(code="D.SEQ", sample_type_id=11, name="Sequencing Data",
                      description="Reads.", group="D.")
CATALOG = [TIS, SEQ]

GENERATE_URL = "/nextseek_api/templates/generate/"


class SelectEntriesTests(TestCase):
    """The selection rule itself, with no HTTP anywhere near it."""

    databases = {"default"}

    def _select(self, codes):
        with patch("nextseek_api.services.template_catalog.load_catalog",
                   return_value=CATALOG):
            return select_entries(codes)

    def test_request_order_wins_over_catalog_order(self):
        chosen, _ = self._select(["D.SEQ", "TIS"])
        self.assertEqual([e.code for e in chosen], ["D.SEQ", "TIS"])

    def test_a_repeated_code_is_taken_once(self):
        chosen, _ = self._select(["TIS", "TIS", "D.SEQ"])
        self.assertEqual([e.code for e in chosen], ["TIS", "D.SEQ"])

    def test_an_unknown_code_is_reported_and_not_chosen(self):
        chosen, unknown = self._select(["TIS", "NOPE"])
        self.assertEqual([e.code for e in chosen], ["TIS"])
        self.assertEqual(unknown, ["NOPE"])

    def test_unknown_codes_come_back_sorted_and_deduped(self):
        _, unknown = self._select(["ZED", "NOPE", "ZED"])
        self.assertEqual(unknown, ["NOPE", "ZED"])

    def test_no_codes_selects_nothing(self):
        chosen, unknown = self._select([])
        self.assertEqual(chosen, [])
        self.assertEqual(unknown, [])


class BothCallersUseTheSharedSeamTests(TestCase):
    """Patch the two shared names; both download paths must change with them."""

    databases = {"default"}

    def setUp(self):
        self.superuser = User.objects.create_user(
            "su-shared", password="pw", is_staff=True, is_superuser=True)

    def _page_post(self, codes):
        req = RequestFactory().post("/seek/templates/download/", {"codes": codes})
        req.user = MagicMock()
        return req

    def _logged_in_seek(self):
        db = MagicMock()
        db.getSeekLogin.return_value = {
            "status": True, "server": "https://seek.example",
            "username": "demo", "password": "demopassword",
        }
        return db

    def test_the_page_resolves_codes_through_select_entries(self):
        from seek.views.assets import templatesDownload

        with patch("seek.decorators.SeekDB") as mock_db, \
             patch("nextseek_api.services.template_catalog.load_catalog",
                   return_value=CATALOG), \
             patch("seek.views.assets.render_template_workbook") as render:
            mock_db.return_value = self._logged_in_seek()
            render.return_value = (_BUFFER(), "x.xlsx")
            templatesDownload(self._page_post(["D.SEQ", "TIS"]))

        self.assertEqual([e.code for e in render.call_args[0][0]], ["D.SEQ", "TIS"])

    def test_the_api_resolves_codes_through_select_entries(self):
        client = APIClient()
        client.force_authenticate(user=self.superuser)

        with patch("nextseek_api.services.template_catalog.load_catalog",
                   return_value=CATALOG), \
             patch("nextseek_api.services.templates.render_template_workbook") as render:
            render.return_value = (_BUFFER(), "x.xlsx")
            resp = client.post(GENERATE_URL, {"codes": ["D.SEQ", "TIS"]},
                               format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e.code for e in render.call_args[0][0]], ["D.SEQ", "TIS"])

    def test_the_page_drops_an_unknown_code_and_the_api_refuses_it(self):
        """The one difference the two callers are allowed to keep."""
        from seek.views.assets import templatesDownload

        with patch("seek.decorators.SeekDB") as mock_db, \
             patch("nextseek_api.services.template_catalog.load_catalog",
                   return_value=CATALOG), \
             patch("seek.views.assets.render_template_workbook") as render:
            mock_db.return_value = self._logged_in_seek()
            render.return_value = (_BUFFER(), "x.xlsx")
            page = templatesDownload(self._page_post(["TIS", "NOPE"]))

        self.assertEqual(page.status_code, 200)
        self.assertEqual([e.code for e in render.call_args[0][0]], ["TIS"])

        client = APIClient()
        client.force_authenticate(user=self.superuser)
        with patch("nextseek_api.services.template_catalog.load_catalog",
                   return_value=CATALOG):
            api = client.post(GENERATE_URL, {"codes": ["TIS", "NOPE"]},
                              format="json")
        self.assertEqual(api.status_code, 422)


def _BUFFER():
    import io
    return io.BytesIO(b"x")
