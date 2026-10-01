"""Signing in returns the user to the page they asked for (UI-020 to UI-025).

Every login bounce carries the requested path and query string as ``next``; the
login view follows ``next`` only to a path on this site.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory


def _seek_login_fails():
    db = MagicMock()
    db.getSeekLogin.return_value = {"status": False, "err": "not signed in"}
    return db


def _anon_get(path):
    req = RequestFactory().get(path)
    req.user = AnonymousUser()
    req.session = {}
    return req


def _bounce(view, path, *args, **kwargs):
    with patch("seek.decorators.SeekDB", return_value=_seek_login_fails()):
        return view(_anon_get(path), *args, **kwargs)


class TestDecoratorNextTarget:
    def test_next_is_the_requested_path_and_query(self):
        from seek.views.assets import sopQuery
        resp = _bounce(sopQuery, "/seek/sop/query/?project=2&tab=x")
        assert resp.status_code == 302
        assert resp.url == "/login/?next=/seek/sop/query/%3Fproject=2%26tab=x"

    def test_new_sample_returns_to_the_upload_page(self):
        from seek.views.upload import batchUpload
        assert _bounce(batchUpload, "/seek/samples/upload/").url == "/login/?next=/seek/samples/upload/"

    def test_detail_pages_return_to_themselves_not_their_list(self):
        from seek.views.catalog import assayDetail, sampleTypeDetail
        assert _bounce(sampleTypeDetail, "/seek/sampletypes/TIS/", "TIS").url == \
            "/login/?next=/seek/sampletypes/TIS/"
        assert _bounce(assayDetail, "/seek/assays/rna-seq/", "rna-seq").url == \
            "/login/?next=/seek/assays/rna-seq/"

    def test_admin_pages_return_to_themselves(self):
        from seek.views.admin import adminClades
        assert _bounce(adminClades, "/seek/admin/clades/").url == "/login/?next=/seek/admin/clades/"

    def test_an_explicit_target_still_wins(self):
        from seek.views.assets import templatesDownload
        assert _bounce(templatesDownload, "/seek/templates/download/?code=TIS").url == \
            "/login/?next=/seek/templates/"


class TestInlineLoginChecks:
    def test_anonymous_assistant_is_sent_to_sign_in(self):
        from seek.views.search import smartSearch
        resp = smartSearch(_anon_get("/seek/assistant/chat/abc/"))
        assert resp.status_code == 302
        assert resp.url == "/login/?next=/seek/assistant/chat/abc/"

    def test_uid_search_returns_to_the_uid_not_a_sample_id(self):
        from seek.views.samples import sampleTree
        with patch("seek.views.samples.DBtable_sample") as table:
            resp = sampleTree(_anon_get("/seek/sampletree/uid=NHP-12.A/"), "NHP-12.A")
        assert resp.url == "/login/?next=/seek/sampletree/uid=NHP-12.A/"
        table.assert_not_called()       # nothing is looked up for a visitor


class TestLoginViewNext:
    def _signed_in(self, path):
        from dmac import views
        req = RequestFactory().post(path, {"username": "u", "password": "p"})
        req.session = MagicMock()
        seekdb = MagicMock()
        seekdb.getSeekLogin.return_value = {
            "status": True, "err": "", "username": "u", "password": "p", "server": "s",
            "storagetype": "", "storage": "", "noexpire": "yes",
        }
        with patch.object(views, "SeekDB", return_value=seekdb), \
             patch.object(views, "authenticate", return_value=MagicMock()), \
             patch.object(views, "login"):
            return views.login_seek(req)

    def test_follows_an_encoded_local_next(self):
        resp = self._signed_in("/login/?next=/seek/search/%3Ftab%3Dadvanced")
        assert resp.url == "/seek/search/?tab=advanced"

    def test_refuses_an_off_site_next(self):
        for target in ("https://example.org/x", "//example.org/x", "/\\example.org"):
            assert self._signed_in("/login/?next=" + target).url == "/", target

    def test_no_next_goes_home(self):
        assert self._signed_in("/login/").url == "/"


class TestRoutes:
    def test_logout_route_is_gone_and_login_is_anchored(self):
        from django.urls import Resolver404, resolve
        for path in ("/logout", "/loginfoo"):
            try:
                match = resolve(path)
            except Resolver404:
                continue
            assert match.url_name not in ("logout_seek", "login_seek"), path
        assert resolve("/login/").url_name == "login_seek"
        assert resolve("/login").url_name == "login_seek"
