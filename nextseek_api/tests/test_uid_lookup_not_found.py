"""A sample, SOP or data file named by UID or title answers the same 404 whether it does not exist or exists where the
caller may not see it.

SEEK answers 403 for an item the caller may not view. The proxies answer their own
404 for that, on the fetch, the download and on a write whose target the caller cannot view; a write SEEK refuses on
an item the caller can view keeps SEEK's 403. Validation details in a 502 quote the upstream body, so the
``debug_validation`` flag is honoured for a superuser only. SEEK, the UID tables and the graph hooks are faked; the
routes, authentication and handlers are the real ones.
"""

import base64
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.models import SopDownloadRequest
from nextseek_api.services.content_blobs import resolve_asset_and_blobs

HIDDEN_SAMPLE, OWN_SAMPLE = 4242, 321
HIDDEN_SOP, HIDDEN_DATA_FILE = 77, 88
OWN_SOP, OWN_DATA_FILE = 55, 66
_JSONAPI = {"Content-Type": "application/vnd.api+json"}


def _seek(code, payload):
    return (json.dumps(payload).encode(), code, dict(_JSONAPI), None)


def _forbidden(kind, item_id, privilege="view"):
    """SEEK's answer for an item the caller may not view or change."""
    return _seek(403, {"errors": [{"title": "Forbidden", "details": f"You may not {privilege} {kind}:{item_id}"}]})


def _sample_payload(sid):
    return {"data": {"id": str(sid), "type": "samples", "attributes": {"title": "A"},
                     "relationships": {"sample_type": {"data": {"type": "sample_types", "id": "12"}},
                                       "creators": {"data": []}, "projects": {"data": []}, "people": {"data": []},
                                       "assays": {"data": []}, "data_files": {"data": []}},
                     "links": {"self": f"/samples/{sid}"}, "meta": {}},
            "jsonapi": {"version": "1.0"}}


def _get_sample(_request, seek_id):
    if int(seek_id) == HIDDEN_SAMPLE:
        return _forbidden("sample", seek_id)
    if int(seek_id) == OWN_SAMPLE:
        return _seek(200, _sample_payload(seek_id))
    return _seek(404, {"errors": [{"title": "Not found"}]})


class _SampleTable:
    def getSampleID(self, uid):
        return {"HID-1-PUB": HIDDEN_SAMPLE, "OWN-1-PUB": OWN_SAMPLE}.get(uid)


class _Sops:
    def __init__(self, *_a):
        pass

    def queryRecordsByConstraint(self, constraint):
        return [{"id": {"OTHER-SOP-1": HIDDEN_SOP, "OWN-SOP-1": OWN_SOP}[constraint["title"]]}] \
            if constraint.get("title") in ("OTHER-SOP-1", "OWN-SOP-1") else []


class _DataFiles:
    def __init__(self, *_a):
        pass

    def queryRecordsByConstraint(self, _constraint):
        return []

    def queryRecordsCustom(self, q):
        if "HID-1-PUB_" in str(q):
            return [{"id": HIDDEN_DATA_FILE}]
        return [{"id": OWN_DATA_FILE}] if "OWN-1-PUB_" in str(q) else []


def _get_sop(_request, sid):
    if int(sid) == OWN_SOP:
        return _seek(200, {"data": {"id": str(sid), "type": "sops"}})
    return _forbidden("sop", sid)


def _get_data_file(_request, sid, *_a):
    if int(sid) == OWN_DATA_FILE:
        return _seek(200, {"data": {"id": str(sid), "type": "data_files"}})
    return _forbidden("data_file", sid)


def _basic(client, username):
    client.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(f"{username}:pw".encode()).decode())
    return client


class _Base(TestCase):
    databases = {"default"}

    def setUp(self):
        User.objects.create_user("member", password="pw")
        User.objects.create_superuser("admin", password="pw")
        self.member = _basic(APIClient(), "member")
        self.admin = _basic(APIClient(), "admin")
        self._patches = [
            patch("nextseek_api.services.samples.DBtable_sample", _SampleTable),
            patch("seek.dbtable_sops.DBtable_sops", _Sops),
            patch("nextseek_api.models.DBtable_sops", _Sops),
            patch("seek.dbtable_data_files.DBtable_data_files", _DataFiles),
            patch("nextseek_api.helpers.SeekAPIClient.get_sample", side_effect=_get_sample),
            patch("nextseek_api.helpers.SeekAPIClient.get_sop", side_effect=_get_sop),
            patch("nextseek_api.helpers.SeekAPIClient.get_data_file", side_effect=_get_data_file),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class SampleLookupTests(_Base):
    def _assert_like_unknown(self, hidden, unknown):
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual((hidden.status_code, hidden.content), (unknown.status_code, unknown.content))
        self.assertNotIn(str(HIDDEN_SAMPLE).encode(), hidden.content)

    def test_a_hidden_sample_uid_answers_like_an_unknown_one(self):
        self._assert_like_unknown(self.member.get("/nextseek_api/samples/HID-1-PUB/"),
                                  self.member.get("/nextseek_api/samples/NOPE-9/"))
        self._assert_like_unknown(self.member.get("/nextseek_api/samples/HID-1/"),
                                  self.member.get("/nextseek_api/samples/NOPE-9/"))

    def test_a_hidden_sample_id_answers_not_found(self):
        self._assert_like_unknown(self.member.get(f"/nextseek_api/samples/{HIDDEN_SAMPLE}/"),
                                  self.member.get("/nextseek_api/samples/NOPE-9/"))

    def test_a_visible_sample_is_still_served(self):
        r = self.member.get("/nextseek_api/samples/OWN-1-PUB/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["id"], str(OWN_SAMPLE))

    def test_a_hidden_sample_write_answers_like_an_unknown_one(self):
        body = {"data": {"type": "samples", "attributes": {"title": "Revised"}}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_sample",
                   return_value=_forbidden("sample", HIDDEN_SAMPLE, "edit")), \
             patch("nextseek_api.helpers.SeekAPIClient.delete_sample",
                   return_value=_forbidden("sample", HIDDEN_SAMPLE, "delete")):
            self._assert_like_unknown(self.member.patch("/nextseek_api/samples/HID-1-PUB/", body, format="json"),
                                      self.member.patch("/nextseek_api/samples/NOPE-9/", body, format="json"))
            self._assert_like_unknown(self.member.delete("/nextseek_api/samples/HID-1-PUB/"),
                                      self.member.delete("/nextseek_api/samples/NOPE-9/"))

    def test_a_refused_write_on_a_visible_sample_keeps_seeks_403(self):
        refusal = _forbidden("sample", OWN_SAMPLE, "edit")
        body = {"data": {"type": "samples", "attributes": {"title": "Revised"}}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_sample", return_value=refusal), \
             patch("nextseek_api.helpers.SeekAPIClient.delete_sample", return_value=refusal):
            self.assertEqual(self.member.patch("/nextseek_api/samples/OWN-1-PUB/", body, format="json").status_code,
                             403)
            self.assertEqual(self.member.delete("/nextseek_api/samples/OWN-1-PUB/").status_code, 403)


class DebugValidationTests(_Base):
    """The flag adds the upstream body's validation errors to a 502: a superuser's diagnostic, nobody else's."""

    def _fetch(self, client):
        with patch("nextseek_api.helpers.SeekAPIClient.get_sample", return_value=_seek(200, {"data": {"id": "1"}})):
            return client.get(f"/nextseek_api/samples/{OWN_SAMPLE}/?debug_validation=1")

    def test_a_member_gets_no_validation_details(self):
        r = self._fetch(self.member)
        self.assertEqual(r.status_code, 502)
        self.assertNotIn(b"validation_errors", r.content)

    def test_a_superuser_gets_them(self):
        r = self._fetch(self.admin)
        self.assertEqual(r.status_code, 502)
        self.assertIn(b"validation_errors", r.content)


class SopAndDataFileLookupTests(_Base):
    def _assert_like_unknown(self, hidden, unknown, hidden_id):
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual((hidden.status_code, hidden.content), (unknown.status_code, unknown.content))
        self.assertNotIn(f":{hidden_id}".encode(), hidden.content)

    def test_a_hidden_sop_title_answers_like_an_unknown_one(self):
        self._assert_like_unknown(self.member.get("/nextseek_api/sops/OTHER-SOP-1/"),
                                  self.member.get("/nextseek_api/sops/NOPE-SOP/"), HIDDEN_SOP)

    def test_a_hidden_data_file_uid_answers_like_an_unknown_one(self):
        self._assert_like_unknown(self.member.get("/nextseek_api/data_files/HID-1-PUB/"),
                                  self.member.get("/nextseek_api/data_files/NOPE-9/"), HIDDEN_DATA_FILE)

    def test_a_hidden_download_answers_like_an_unknown_one(self):
        for kind, hidden_ref, hidden_id in (("sops", "OTHER-SOP-1", HIDDEN_SOP),
                                            ("data_files", "HID-1-PUB", HIDDEN_DATA_FILE)):
            with self.subTest(kind=kind):
                self._assert_like_unknown(
                    self.member.post(f"/nextseek_api/{kind}/download/", {"uid_or_id": hidden_ref}, format="json"),
                    self.member.post(f"/nextseek_api/{kind}/download/", {"uid_or_id": "NOPE-9"}, format="json"),
                    hidden_id)

    def test_a_hidden_item_in_a_batch_download_fails_like_an_unknown_one(self):
        hidden = resolve_asset_and_blobs(_client(), None, "sops", SopDownloadRequest(uid_or_id="OTHER-SOP-1"))
        unknown = resolve_asset_and_blobs(_client(), None, "sops", SopDownloadRequest(uid_or_id="NOPE-SOP"))
        self.assertEqual(hidden, unknown)

    def test_a_hidden_sop_write_answers_like_an_unknown_one(self):
        body = {"data": {"type": "sops", "attributes": {"title": "Revised"}}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_sop", return_value=_forbidden("sop", HIDDEN_SOP, "edit")):
            self._assert_like_unknown(self.member.patch("/nextseek_api/sops/OTHER-SOP-1/", body, format="json"),
                                      self.member.patch("/nextseek_api/sops/NOPE-SOP/", body, format="json"),
                                      HIDDEN_SOP)

    def test_a_hidden_data_file_write_answers_not_found(self):
        body = {"data": {"id": str(HIDDEN_DATA_FILE), "type": "data_files", "attributes": {"title": "Revised"}}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_data_file",
                   return_value=_forbidden("data_file", HIDDEN_DATA_FILE, "edit")):
            r = self.member.patch("/nextseek_api/data_files/HID-1-PUB/", body, format="json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, self.member.get("/nextseek_api/data_files/NOPE-9/").content)


def _client():
    from nextseek_api.helpers import SeekAPIClient
    return SeekAPIClient()


def _never(*_a, **_k):
    raise AssertionError("the write must not reach SEEK")


class PatchByUidTests(_Base):
    """A PATCH whose path names an item by UID or title answers the same for an item the caller may not see as for one
    that does not exist, whatever id the body carries. The path names the item: a path that names nothing the caller
    can see answers 404, and a body id that differs from the path's item is refused only once the caller can see it."""

    def _assert_like_unknown(self, hidden, unknown):
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual((hidden.status_code, hidden.content), (unknown.status_code, unknown.content))

    def test_a_sample_patch_with_a_mismatched_body_id(self):
        body = {"data": {"id": "x", "type": "samples"}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_sample", side_effect=_never):
            self._assert_like_unknown(self.member.patch("/nextseek_api/samples/HID-1-PUB/", body, format="json"),
                                      self.member.patch("/nextseek_api/samples/NOPE-9/", body, format="json"))
            self.assertEqual(self.member.patch("/nextseek_api/samples/OWN-1-PUB/", body, format="json").status_code,
                             422)

    def test_a_data_file_patch_with_another_body_id(self):
        body = {"data": {"id": str(OWN_DATA_FILE), "type": "data_files"}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_data_file", side_effect=_never):
            self._assert_like_unknown(self.member.patch("/nextseek_api/data_files/HID-1-PUB/", body, format="json"),
                                      self.member.patch("/nextseek_api/data_files/NOPE-9/", body, format="json"))
            mismatch = {"data": {"id": str(HIDDEN_DATA_FILE), "type": "data_files"}}
            self.assertEqual(
                self.member.patch("/nextseek_api/data_files/OWN-1-PUB/", mismatch, format="json").status_code, 422)

    def test_a_sop_patch_with_another_body_id(self):
        body = {"data": {"id": str(OWN_SOP), "type": "sops", "attributes": {"title": "t"}}}
        with patch("nextseek_api.helpers.SeekAPIClient.update_sop", side_effect=_never):
            self._assert_like_unknown(self.member.patch("/nextseek_api/sops/OTHER-SOP-1/", body, format="json"),
                                      self.member.patch("/nextseek_api/sops/NOPE-SOP/", body, format="json"))
            mismatch = {"data": {"id": str(HIDDEN_SOP), "type": "sops", "attributes": {"title": "t"}}}
            self.assertEqual(
                self.member.patch("/nextseek_api/sops/OWN-SOP-1/", mismatch, format="json").status_code, 422)

    def test_a_sop_patch_sends_seek_the_paths_id_whatever_title_the_body_names(self):
        sent = []

        def _update(_request, sop_id, payload):
            sent.append((str(sop_id), payload["data"].get("id")))
            return _seek(200, {"data": {"id": str(sop_id), "type": "sops"}})

        with patch("nextseek_api.helpers.SeekAPIClient.update_sop", side_effect=_update):
            for title in ("OTHER-SOP-1", "NOPE-SOP"):
                body = {"data": {"uid": title, "type": "sops", "attributes": {"title": "t"}}}
                self.member.patch("/nextseek_api/sops/OWN-SOP-1/", body, format="json")
        self.assertEqual(sent, [(str(OWN_SOP), str(OWN_SOP))] * 2)


class NumericIdWriteTests(_Base):
    """A write by SEEK's own numeric id answers the proxy's not-found for an item SEEK does not have, the same as for one
    the caller may not see."""

    UNKNOWN = 999999

    def _write(self, kind):
        def _fn(_request, sid, *_a):
            if int(sid) == self.UNKNOWN:
                return _seek(404, {"errors": [{"title": "Not found"}]})
            return _forbidden(kind, sid, "edit")
        return _fn

    def test_a_hidden_id_answers_like_an_unknown_one(self):
        m, u = self.member, self.UNKNOWN
        cases = [
            ("sample PATCH", "update_sample", "sample",
             lambda ref: m.patch(f"/nextseek_api/samples/{ref}/",
                                 {"data": {"type": "samples", "attributes": {"title": "t"}}}, format="json"),
             HIDDEN_SAMPLE),
            ("sample DELETE", "delete_sample", "sample",
             lambda ref: m.delete(f"/nextseek_api/samples/{ref}/"), HIDDEN_SAMPLE),
            ("SOP PATCH", "update_sop", "sop",
             lambda ref: m.patch(f"/nextseek_api/sops/{ref}/",
                                 {"data": {"type": "sops", "attributes": {"title": "t"}}}, format="json"),
             HIDDEN_SOP),
            ("data file PATCH", "update_data_file", "data_file",
             lambda ref: m.patch(f"/nextseek_api/data_files/{ref}/",
                                 {"data": {"id": str(ref), "type": "data_files"}}, format="json"),
             HIDDEN_DATA_FILE),
        ]
        for label, method, kind, call, hidden_id in cases:
            with self.subTest(label), patch(f"nextseek_api.helpers.SeekAPIClient.{method}",
                                            side_effect=self._write(kind)):
                hidden, unknown = call(hidden_id), call(u)
                self.assertEqual((hidden.status_code, hidden.content), (unknown.status_code, unknown.content))
                self.assertEqual(unknown.status_code, 404)


class NoSeekLoginTests(TestCase):
    """A signed-in caller that holds no SEEK login of its own gets the same 401 on the sample, SOP and data file routes
    whatever the UID names, before anything is looked up."""

    databases = {"default"}

    def setUp(self):
        self.c = APIClient()
        self.c.force_login(User.objects.create_user("member", password="pw"))
        for p in (patch("nextseek_api.services.samples.DBtable_sample", _SampleTable),
                  patch("seek.dbtable_sops.DBtable_sops", _Sops),
                  patch("nextseek_api.models.DBtable_sops", _Sops),
                  patch("seek.dbtable_data_files.DBtable_data_files", _DataFiles)):
            p.start()
            self.addCleanup(p.stop)

    def test_an_existing_uid_answers_like_an_unknown_one(self):
        sample = {"data": {"type": "samples", "attributes": {"title": "t"}}}
        sop = {"data": {"type": "sops", "attributes": {"title": "t"}}}
        data_file = {"data": {"id": "1", "type": "data_files", "attributes": {"title": "t"}}}
        c = self.c
        pairs = {
            "GET sample": lambda ref: c.get(f"/nextseek_api/samples/{ref}/"),
            "PATCH sample": lambda ref: c.patch(f"/nextseek_api/samples/{ref}/", sample, format="json"),
            "DELETE sample": lambda ref: c.delete(f"/nextseek_api/samples/{ref}/"),
            "GET data file": lambda ref: c.get(f"/nextseek_api/data_files/{ref}/"),
            "PATCH data file": lambda ref: c.patch(f"/nextseek_api/data_files/{ref}/", data_file, format="json"),
            "download data file": lambda ref: c.post("/nextseek_api/data_files/download/", {"uid_or_id": ref},
                                                     format="json"),
        }
        sop_pairs = {
            "GET sop": lambda ref: c.get(f"/nextseek_api/sops/{ref}/"),
            "PATCH sop": lambda ref: c.patch(f"/nextseek_api/sops/{ref}/", sop, format="json"),
            "download sop": lambda ref: c.post("/nextseek_api/sops/download/", {"uid_or_id": ref}, format="json"),
            "batch download sop": lambda ref: c.post("/nextseek_api/sops/download/", [{"uid_or_id": ref}],
                                                     format="json"),
        }
        cases = [(label, call, "HID-1-PUB", "NOPE-9") for label, call in pairs.items()]
        cases += [(label, call, "OTHER-SOP-1", "NOPE-SOP") for label, call in sop_pairs.items()]
        for label, call, existing_ref, unknown_ref in cases:
            with self.subTest(label):
                existing, unknown = _answer(call(existing_ref)), _answer(call(unknown_ref))
                self.assertEqual(existing, unknown)
                self.assertEqual(unknown[0], 401)


def _answer(r):
    return r.status_code, b"".join(r.streaming_content) if r.streaming else r.content
