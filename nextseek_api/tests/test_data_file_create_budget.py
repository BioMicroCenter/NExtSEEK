"""The data-file create gets the SOP create's time budget; a read timeout answers 202 unconfirmed."""
import json
from unittest.mock import MagicMock

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from nextseek_api.helpers import SOP_CREATE_TIMEOUT_S, SeekAPIClient
from nextseek_api.services.data_files import DataFileProxyViewSet

_META = json.dumps({"data": {"type": "data_files", "attributes": {
    "title": "T_X", "content_blobs": [{"original_filename": "d.csv", "content_type": "text/csv"}]},
    "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}}})


def _view(client):
    req = APIRequestFactory().post("/nextseek_api/data_files/", {
        "metadata": _META, "file": SimpleUploadedFile("d.csv", b"a,b\n1,2", content_type="text/csv")},
        format="multipart")
    vs = DataFileProxyViewSet()
    vs.client = client
    drf = Request(req, parsers=[p() for p in vs.parser_classes])
    drf.user, drf.auth = MagicMock(), "token"
    return vs, drf


class DataFileCreateBudgetTest(TestCase):
    def test_client_default_is_the_sop_budget(self):
        c = SeekAPIClient()
        c._request = MagicMock(return_value=(b"{}", 201, {}, None))
        c.create_data_file(MagicMock(), {"data": {}})
        self.assertEqual(c._request.call_args.kwargs["timeout_s"], (10, SOP_CREATE_TIMEOUT_S))

    def test_view_passes_a_budget_and_read_timeout_is_202_unconfirmed(self):
        client = MagicMock()
        client.create_data_file.side_effect = requests.ReadTimeout("slow")
        vs, drf = _view(client)
        resp = vs.create(drf)
        wait = client.create_data_file.call_args.kwargs["timeout_s"]
        self.assertAlmostEqual(wait[1], SOP_CREATE_TIMEOUT_S, delta=1)
        self.assertEqual(resp.status_code, 202)
        body = json.loads(resp.content)
        self.assertEqual(body["status"], "unconfirmed")
        self.assertEqual(body["title"], "T_X")
        self.assertIn(f"{SOP_CREATE_TIMEOUT_S} s", body["detail"])
