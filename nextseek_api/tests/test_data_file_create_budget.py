"""The data-file create gets the SOP create's time budget; a read timeout answers 202 unconfirmed."""
import json
from unittest.mock import MagicMock, patch

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


def _view(client, names=("d.csv",)):
    files = [SimpleUploadedFile(n, b"a,b\n1,2", content_type="text/csv") for n in names]
    req = APIRequestFactory().post("/nextseek_api/data_files/", {"metadata": _META, "file": files},
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


def _made(df_id, name):
    """SEEK's answer to a data-file create: the record and its empty file placeholder."""
    return (json.dumps({"data": {"id": df_id, "type": "data_files",
                                 "attributes": {"title": name, "content_blobs": [{
                                     "link": f"http://seek/data_files/{df_id}/content_blobs/9",
                                     "original_filename": name, "content_type": "text/csv"}]},
                                 "relationships": {}, "links": {"self": f"/data_files/{df_id}"},
                                 "meta": {}}}).encode(), 201, {}, None)


class DataFileCreateTimeUpTest(TestCase):
    def _client(self, *names):
        client = MagicMock()
        client.create_data_file.side_effect = [_made(str(i + 1), n) for i, n in enumerate(names)]
        client.upload_content_blob.return_value = (200, {}, None)
        return client

    @patch("nextseek_api.services.data_files._within", side_effect=[(10, 120), (10, 77)])
    def test_the_file_upload_is_capped_by_the_request_limit(self, within):
        client = self._client("d.csv")
        vs, drf = _view(client)
        self.assertEqual(vs.create(drf).status_code, 201)
        self.assertEqual(within.call_args_list[1].args[1], 300)  # the upload's own 300 s, inside the limit
        self.assertEqual(client.upload_content_blob.call_args.kwargs["timeout_s"], (10, 77))

    @patch("nextseek_api.services.data_files._within", side_effect=[(10, 120), (10, 300), None])
    def test_time_up_before_a_create_keeps_the_created_records(self, _):
        vs, drf = _view(self._client("d.csv", "e.csv"), names=("d.csv", "e.csv"))
        resp = vs.create(drf)
        body = json.loads(resp.content)
        self.assertEqual(resp.status_code, 207)
        self.assertEqual([d["data"]["id"] for d in body["data"]], ["1"])  # the page links what was made
        self.assertEqual([c["id"] for c in body["created"]], ["1"])
        self.assertEqual(body["not_sent"], ["e.csv"])

    @patch("nextseek_api.services.data_files._within", side_effect=[(10, 120), None])
    def test_time_up_before_an_upload_names_the_record_without_its_file(self, _):
        client = self._client("d.csv")
        vs, drf = _view(client)
        resp = vs.create(drf)
        body = json.loads(resp.content)
        self.assertEqual(resp.status_code, 207)
        self.assertEqual([d["data"]["id"] for d in body["data"]], ["1"])
        self.assertEqual(body["created"], [{"id": "1", "file": "d.csv"}])
        self.assertEqual(body["not_sent"], ["d.csv"])
        client.upload_content_blob.assert_not_called()
