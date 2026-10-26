"""The data-file upload page must not loop response data it was not given."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

_PAGE = Path(settings.BASE_DIR) / "seek" / "templates" / "dataFileUpload.html"


class DataFileUploadPageTest(SimpleTestCase):
    def test_page_handles_unconfirmed_and_error_answers(self):
        html = _PAGE.read_text()
        self.assertIn('"unconfirmed"', html)
        self.assertIn("errors", html)
        self.assertIn("textContent", html)  # server text goes in as text, not HTML
        self.assertIn("Array.isArray(response_json.data)", html)

    def test_page_names_what_was_not_sent_and_the_list_to_check(self):
        html = _PAGE.read_text()
        self.assertIn("response_json.not_sent", html)  # a time-up names the files it did not send
        self.assertNotIn("Check the data files list", html)  # a SOP upload checks the SOPs list
